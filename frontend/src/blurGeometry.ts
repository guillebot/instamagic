export type BlurRect = {
  x: number;
  y: number;
  width: number;
  height: number;
  rotation_deg: number;
};

export type BlurResizeMode = "move" | "nw" | "ne" | "sw" | "se" | "n" | "s" | "e" | "w";

const MIN_SIZE = 8;

function toRadians(deg: number) {
  return (deg * Math.PI) / 180;
}

/** Map screen-space delta into the blur's local axes (inverse rotation). */
function localDelta(dx: number, dy: number, rotationDeg: number) {
  const theta = toRadians(rotationDeg);
  const cos = Math.cos(theta);
  const sin = Math.sin(theta);
  return {
    ldx: dx * cos + dy * sin,
    ldy: -dx * sin + dy * cos,
  };
}

/** Shift center in image space along local axes. */
function shiftCenter(cx: number, cy: number, ldx: number, ldy: number, rotationDeg: number) {
  const theta = toRadians(rotationDeg);
  const cos = Math.cos(theta);
  const sin = Math.sin(theta);
  return {
    cx: cx + ldx * cos - ldy * sin,
    cy: cy + ldx * sin + ldy * cos,
  };
}

export function moveBlur(rect: BlurRect, dx: number, dy: number): BlurRect {
  return {
    ...rect,
    x: Math.round(rect.x + dx),
    y: Math.round(rect.y + dy),
  };
}

export function resizeBlur(rect: BlurRect, mode: BlurResizeMode, dx: number, dy: number): BlurRect {
  if (mode === "move") {
    return moveBlur(rect, dx, dy);
  }
  const { ldx, ldy } = localDelta(dx, dy, rect.rotation_deg);
  const cx = rect.x + rect.width / 2;
  const cy = rect.y + rect.height / 2;
  let width = rect.width;
  let height = rect.height;
  let shiftLx = 0;
  let shiftLy = 0;

  switch (mode) {
    case "se":
      width = Math.max(MIN_SIZE, rect.width + ldx);
      height = Math.max(MIN_SIZE, rect.height + ldy);
      shiftLx = (width - rect.width) / 2;
      shiftLy = (height - rect.height) / 2;
      break;
    case "nw":
      width = Math.max(MIN_SIZE, rect.width - ldx);
      height = Math.max(MIN_SIZE, rect.height - ldy);
      shiftLx = -(width - rect.width) / 2;
      shiftLy = -(height - rect.height) / 2;
      break;
    case "ne":
      width = Math.max(MIN_SIZE, rect.width + ldx);
      height = Math.max(MIN_SIZE, rect.height - ldy);
      shiftLx = (width - rect.width) / 2;
      shiftLy = -(height - rect.height) / 2;
      break;
    case "sw":
      width = Math.max(MIN_SIZE, rect.width - ldx);
      height = Math.max(MIN_SIZE, rect.height + ldy);
      shiftLx = -(width - rect.width) / 2;
      shiftLy = (height - rect.height) / 2;
      break;
    case "e":
      width = Math.max(MIN_SIZE, rect.width + ldx);
      shiftLx = (width - rect.width) / 2;
      break;
    case "w":
      width = Math.max(MIN_SIZE, rect.width - ldx);
      shiftLx = -(width - rect.width) / 2;
      break;
    case "s":
      height = Math.max(MIN_SIZE, rect.height + ldy);
      shiftLy = (height - rect.height) / 2;
      break;
    case "n":
      height = Math.max(MIN_SIZE, rect.height - ldy);
      shiftLy = -(height - rect.height) / 2;
      break;
    default:
      break;
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

export function snapRotation(deg: number, snap: boolean) {
  if (!snap) return deg;
  return Math.round(deg / 15) * 15;
}
