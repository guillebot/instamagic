/**
 * Focused harness for crop drag/resize math (no browser / no Vite).
 * Run: node --experimental-vm-modules src/cropGeometry.test.mjs
 * (compiled helpers inlined below to avoid a TS build step)
 */

function parseAspectRatio(aspect, fallbackWidth, fallbackHeight) {
  const match = /^(\d+)\s*:\s*(\d+)$/.exec(aspect);
  if (match) {
    const w = Number(match[1]);
    const h = Number(match[2]);
    if (w > 0 && h > 0) return w / h;
  }
  if (fallbackHeight <= 0) return 1;
  return fallbackWidth / fallbackHeight;
}

function containedLayout(elementW, elementH, imageW, imageH) {
  if (elementW <= 0 || elementH <= 0 || imageW <= 0 || imageH <= 0) {
    return { offsetX: 0, offsetY: 0, displayW: 0, displayH: 0, scale: 1 };
  }
  const scale = Math.min(elementW / imageW, elementH / imageH);
  const displayW = imageW * scale;
  const displayH = imageH * scale;
  return {
    offsetX: (elementW - displayW) / 2,
    offsetY: (elementH - displayH) / 2,
    displayW,
    displayH,
    scale,
  };
}

function clampCrop(rect, imageW, imageH) {
  const width = Math.min(Math.max(1, Math.round(rect.width)), imageW);
  const height = Math.min(Math.max(1, Math.round(rect.height)), imageH);
  const x = Math.min(Math.max(0, Math.round(rect.x)), Math.max(0, imageW - width));
  const y = Math.min(Math.max(0, Math.round(rect.y)), Math.max(0, imageH - height));
  return { x, y, width, height };
}

function moveCrop(rect, dx, dy, imageW, imageH) {
  return clampCrop({ ...rect, x: rect.x + dx, y: rect.y + dy }, imageW, imageH);
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const aspect = parseAspectRatio("4:5", 400, 500);
assert(Math.abs(aspect - 0.8) < 1e-9, "4:5 aspect");

const layout = containedLayout(1000, 500, 1000, 1000);
assert(Math.abs(layout.scale - 0.5) < 1e-9, "contain scale");
assert(Math.abs(layout.offsetX - 250) < 1e-9, "letterbox x");
assert(Math.abs(layout.offsetY - 0) < 1e-9, "letterbox y");

const rect = { x: 100, y: 100, width: 400, height: 500 };
const slid = moveCrop(rect, -500, 0, 1000, 1000);
assert(slid.x === 0 && slid.width === 400 && slid.height === 500, "slide along edge, keep size");

const overflow = moveCrop(rect, 900, 900, 1000, 1000);
assert(overflow.x === 600 && overflow.y === 500, "clamp bottom-right");

console.log("cropGeometry.test.mjs: ok");
