#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
mkdir -p "${ROOT}/data/backups"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${ROOT}/data/backups/instamagic-${STAMP}.sql"
docker compose -f "${ROOT}/docker-compose.yml" exec -T postgres \
  pg_dump -U instamagic --no-owner --no-privileges instamagic > "$OUT"
echo "Wrote ${OUT}"
