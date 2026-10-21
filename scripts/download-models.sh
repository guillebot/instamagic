#!/bin/sh
# Operator-run. Downloads region-detector weights that are NOT shipped with Instamagic.
# The NudeNet weights are AGPL-3.0. This script does not import that package.
# Instamagic runs the file with its own ONNX Runtime decoder.
# Do not use this for anything other than a local safety filter on photographs you own.
set -eu
if [ "${1:-}" != "--i-accept-the-weight-license" ]; then
  echo "Refusing to download. Re-run with --i-accept-the-weight-license after you read the weight license." >&2
  exit 1
fi
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
DEST="${ROOT}/models/320n.onnx"
URL="https://github.com/notAI-tech/NudeNet/releases/download/v3.4-weights/320n.onnx"
# Pin updated when the operator verifies the file. Leave empty to print the hash and stop.
EXPECTED_SHA256="${SAFETY_MODEL_SHA256:-}"
mkdir -p "${ROOT}/models"
curl -fsSL "$URL" -o "${DEST}.partial"
ACTUAL="$(shasum -a 256 "${DEST}.partial" | awk '{print $1}')"
if [ -z "$EXPECTED_SHA256" ]; then
  echo "Downloaded hash: ${ACTUAL}" >&2
  echo "Set SAFETY_MODEL_SHA256 to that value and re-run to install the file." >&2
  rm -f "${DEST}.partial"
  exit 1
fi
if [ "$ACTUAL" != "$EXPECTED_SHA256" ]; then
  echo "Checksum mismatch. Expected ${EXPECTED_SHA256}, got ${ACTUAL}." >&2
  rm -f "${DEST}.partial"
  exit 1
fi
mv "${DEST}.partial" "$DEST"
echo "Installed ${DEST}"
