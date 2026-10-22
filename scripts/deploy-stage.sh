#!/usr/bin/env bash
# Slim stage deploy to 10.1.1.8:/opt/docker/instamagic.
# Copies compose + env example only (no source tree, no --build).
# Host pulls images from Docker Hub / GHCR. Secrets stay on the host.
#
# Optional pin (recommended after CI publish):
#   IMAGE_PREFIX=ghcr.io/guillebot IMAGE_TAG=20261022-01 scripts/deploy-stage.sh
set -euo pipefail

HOST="${INSTAMAGIC_STAGE_HOST:-guille@10.1.1.8}"
REMOTE="${INSTAMAGIC_STAGE_PATH:-/opt/docker/instamagic}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Optional overrides written into host .env before pull (alnum + ._-/: only).
PIN_PREFIX="${IMAGE_PREFIX:-}"
PIN_TAG="${IMAGE_TAG:-}"

if [[ -n "$PIN_PREFIX" && ! "$PIN_PREFIX" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "Refusing IMAGE_PREFIX with unexpected characters: ${PIN_PREFIX}" >&2
  exit 1
fi
if [[ -n "$PIN_TAG" && ! "$PIN_TAG" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Refusing IMAGE_TAG with unexpected characters: ${PIN_TAG}" >&2
  exit 1
fi

echo "Deploying stage compose to ${HOST}:${REMOTE} (no source tree, no build)..."
if [[ -n "$PIN_PREFIX" || -n "$PIN_TAG" ]]; then
  echo "Pin overrides: IMAGE_PREFIX=${PIN_PREFIX:-'(unchanged)'} IMAGE_TAG=${PIN_TAG:-'(unchanged)'}"
fi
ssh -o BatchMode=yes "$HOST" "sudo mkdir -p '$REMOTE' && sudo chown guille:guille '$REMOTE'"

# Host pull-all.sh expects docker-compose.yml. Stage file is the only compose on host.
scp -o BatchMode=yes \
  "$ROOT/docker-compose.stage.yml" \
  "$HOST:$REMOTE/docker-compose.yml"

scp -o BatchMode=yes \
  "$ROOT/deploy/stage.env.example" \
  "$HOST:$REMOTE/stage.env.example"

# Pass pin via argv (validated above); keep heredoc quoted so host secrets stay local.
ssh -o BatchMode=yes "$HOST" bash -s -- "$PIN_PREFIX" "$PIN_TAG" <<'REMOTE'
set -euo pipefail
PIN_PREFIX="${1:-}"
PIN_TAG="${2:-}"
cd /opt/docker/instamagic
mkdir -p data/stage-library models

if [[ ! -f .env ]]; then
  echo "Creating stage .env with fresh secrets (not printed)..."
  umask 077
  APP_PASSWORD="$(openssl rand -hex 16)"
  APP_SECRET="$(openssl rand -hex 32)"
  POSTGRES_PASSWORD="$(openssl rand -hex 24)"
  sed \
    -e "s|^APP_PASSWORD=.*|APP_PASSWORD=${APP_PASSWORD}|" \
    -e "s|^APP_SECRET=.*|APP_SECRET=${APP_SECRET}|" \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${POSTGRES_PASSWORD}|" \
    -e "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+psycopg://instamagic:${POSTGRES_PASSWORD}@postgres:5432/instamagic|" \
    stage.env.example > .env
  chmod 600 .env
  unset APP_PASSWORD APP_SECRET POSTGRES_PASSWORD
  echo "Wrote .env (secrets generated on host)."
else
  echo "Keeping existing .env"
  # Ensure image pin keys exist (idempotent; does not overwrite values).
  grep -q '^IMAGE_PREFIX=' .env || echo 'IMAGE_PREFIX=gschimmel' >> .env
  grep -q '^IMAGE_TAG=' .env || echo 'IMAGE_TAG=latest' >> .env
  grep -q '^SAFETY_MODEL_SHA256=' .env || \
    echo 'SAFETY_MODEL_SHA256=c15d8273adad2d0a92f014cc69ab2d6c311a06777a55545f2c4eb46f51911f0f' >> .env
  grep -q '^APP_UID=' .env || echo 'APP_UID=1000' >> .env
  grep -q '^APP_GID=' .env || echo 'APP_GID=100' >> .env
  # Prefer real host library when still on the empty stage stub.
  if grep -q '^ORIGINALS_ROOT=./data/stage-library$' .env; then
    echo "Updating ORIGINALS_ROOT to /fotosarte (was ./data/stage-library)"
    sed -i 's|^ORIGINALS_ROOT=./data/stage-library$|ORIGINALS_ROOT=/fotosarte|' .env
  fi
  grep -q '^ORIGINALS_ROOT=' .env || echo 'ORIGINALS_ROOT=/fotosarte' >> .env
  # Product default: allow Curate to send derivative previews (toggleable per run).
  if grep -q '^GEMINI_IMAGE_POLICY=' .env; then
    sed -i 's|^GEMINI_IMAGE_POLICY=.*|GEMINI_IMAGE_POLICY=preview|' .env
  else
    echo 'GEMINI_IMAGE_POLICY=preview' >> .env
  fi
  echo "Set GEMINI_IMAGE_POLICY=preview"
fi

# Apply optional pin from the Mac deploy invocation (preserves other secrets).
if [[ -n "${PIN_PREFIX:-}" ]]; then
  if grep -q '^IMAGE_PREFIX=' .env; then
    sed -i "s|^IMAGE_PREFIX=.*|IMAGE_PREFIX=${PIN_PREFIX}|" .env
  else
    echo "IMAGE_PREFIX=${PIN_PREFIX}" >> .env
  fi
  echo "Set IMAGE_PREFIX=${PIN_PREFIX}"
fi
if [[ -n "${PIN_TAG:-}" ]]; then
  if grep -q '^IMAGE_TAG=' .env; then
    sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=${PIN_TAG}|" .env
  else
    echo "IMAGE_TAG=${PIN_TAG}" >> .env
  fi
  echo "Set IMAGE_TAG=${PIN_TAG}"
fi

if [[ ! -f models/320n.onnx ]]; then
  echo "WARNING: models/320n.onnx missing. Stage requires ONNX (SAFETY_CLASSIFIER=fake is refused)."
  echo "  Copy weights to ${PWD}/models/ before expecting scans to classify."
fi

echo "Validating compose..."
docker compose config -q

echo "Pulling images (IMAGE_PREFIX/IMAGE_TAG from .env)..."
if docker compose pull; then
  docker compose up -d --force-recreate
  docker compose ps
else
  echo "WARNING: docker compose pull failed (images not published yet, or wrong IMAGE_PREFIX)."
  echo "  Compose is installed at ${PWD}/docker-compose.yml and ready."
  echo "  After CI pushes images: docker compose pull && docker compose up -d"
  echo "  Leaving any already-running containers untouched."
  exit 0
fi
REMOTE

echo "Done. Health check from LAN: curl -fsS https://instamagic.schimmel.homes/health"
