#!/usr/bin/env bash
# Compute next Instamagic build id: YYYYMMDD-XX
# Calendar day is America/Argentina/Buenos_Aires (documented in docs/operations.md).
# XX is 01..99: one higher than the highest existing GHCR tag for that day
# (instamagic-backend), or 01 if none / listing fails.
set -euo pipefail

DATE="${APP_VERSION_DATE:-$(TZ=America/Argentina/Buenos_Aires date +%Y%m%d)}"
OWNER="${APP_VERSION_OWNER:-guillebot}"
PKG="${APP_VERSION_PACKAGE:-instamagic-backend}"
MAX=0

if [[ -n "${GH_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  TOKEN="${GH_TOKEN:-$GITHUB_TOKEN}"
  URL="https://api.github.com/users/${OWNER}/packages/container/${PKG}/versions?per_page=100"
  if RESP=$(curl -fsSL \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$URL" 2>/dev/null); then
    while IFS= read -r tag; do
      [[ -z "$tag" || "$tag" == "null" ]] && continue
      if [[ "$tag" =~ ^${DATE}-([0-9]{2})$ ]]; then
        n=$((10#${BASH_REMATCH[1]}))
        if (( n > MAX )); then MAX=$n; fi
      fi
    done < <(echo "$RESP" | jq -r '.[].metadata.container.tags[]?' 2>/dev/null || true)
  fi
fi

NEXT=$((MAX + 1))
if (( NEXT > 99 )); then
  echo "Too many builds for ${DATE} (max 99)." >&2
  exit 1
fi

printf '%s-%02d\n' "$DATE" "$NEXT"
