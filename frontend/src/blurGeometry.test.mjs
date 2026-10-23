import assert from "node:assert/strict";

function toRadians(deg) {
  return (deg * Math.PI) / 180;
}
function localDelta(dx, dy, rotationDeg) {
  const theta = toRadians(rotationDeg);
  const cos = Math.cos(theta);
  const sin = Math.sin(theta);
  return { ldx: dx * cos + dy * sin, ldy: -dx * sin + dy * cos };
}
function shiftCenter(cx, cy, ldx, ldy, rotationDeg) {
  const theta = toRadians(rotationDeg);
  const cos = Math.cos(theta);
  const sin = Math.sin(theta);
  return { cx: cx + ldx * cos - ldy * sin, cy: cy + ldx * sin + ldy * cos };
}
function moveBlur(rect, dx, dy) {
  return { ...rect, x: Math.round(rect.x + dx), y: Math.round(rect.y + dy) };
}
function resizeBlur(rect, mode, dx, dy) {
  if (mode === "move") return moveBlur(rect, dx, dy);
  const { ldx, ldy } = localDelta(dx, dy, rect.rotation_deg);
  const cx = rect.x + rect.width / 2;
  const cy = rect.y + rect.height / 2;
  let width = rect.width;
  let height = rect.height;
  let shiftLx = 0;
  let shiftLy = 0;
  const MIN = 8;
  if (mode === "se") {
    width = Math.max(MIN, rect.width + ldx);
    height = Math.max(MIN, rect.height + ldy);
    shiftLx = (width - rect.width) / 2;
    shiftLy = (height - rect.height) / 2;
  }
  const nextCenter = shiftCenter(cx, cy, shiftLx, shiftLy, rect.rotation_deg);
  return {
    ...rect,
    x: Math.round(nextCenter.cx - width / 2),
    y: Math.round(nextCenter.cy - height / 2),
    width: Math.round(width),
    height: Math.round(height),
  };
}
function snapRotation(deg, snap) {
  return snap ? Math.round(deg / 15) * 15 : deg;
}

const base = { x: 100, y: 100, width: 80, height: 40, rotation_deg: 0 };

assert.deepEqual(moveBlur(base, 5, -3), { ...base, x: 105, y: 97 });

const grown = resizeBlur(base, "se", 20, 10);
assert.equal(grown.width, 100);
assert.equal(grown.height, 50);

const rotated = resizeBlur({ ...base, rotation_deg: 90 }, "e", 10, 0);
assert.ok(rotated.width >= base.width);

assert.equal(snapRotation(47, true), 45);

console.log("blurGeometry.test.mjs: ok");
