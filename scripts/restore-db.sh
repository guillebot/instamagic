#!/bin/sh
set -eu
if [ $# -ne 1 ]; then
  echo "Usage: scripts/restore-db.sh data/backups/instamagic-TIMESTAMP.sql" >&2
  exit 1
fi
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
DUMP="$1"
docker compose -f "${ROOT}/docker-compose.yml" exec -T postgres \
  psql -U instamagic -d instamagic -v ON_ERROR_STOP=1 < "$DUMP"
echo "Restored ${DUMP}"
