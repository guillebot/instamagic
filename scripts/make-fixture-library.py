#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/fixtures/library/session-001"
mkdir -p "$OUT"
PY="${ROOT}/backend/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi
"$PY" - <<PY
from pathlib import Path
from PIL import Image, ImageDraw
out = Path("$OUT")

def marked(name, color, box):
    image = Image.new("RGB", (400, 600), color)
    ImageDraw.Draw(image).rectangle(box, fill=(230, 230, 230))
    image.save(out / name, quality=90)

marked("clean.jpg", (32, 32, 32), (20, 20, 90, 90))
marked("unsafe-portrait.jpg", (90, 40, 40), (210, 30, 300, 140))
marked("blocked-frame.jpg", (40, 40, 90), (30, 320, 180, 460))
(out / "frame.cr3").write_bytes(b"raw-placeholder")
(out / "clean-copy.jpg").write_bytes((out / "clean.jpg").read_bytes())
print(out)
PY
