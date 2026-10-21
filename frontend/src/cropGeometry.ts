/** Image-pixel crop rectangle. */
export type CropRect = { x: number; y: number; width: number; height: number };

/** Where the object-fit:contain image content sits inside its element. */
export type ContainedLayout = {
  offsetX: number;
  offsetY: number;
  displayW: number;
  displayH: number;
  scale: number;
};

export function parseAspectRatio(aspect: string, fallbackWidth: number, fallbackHeight: number): number {
  const match = /^(\d+)\s*:\s*(\d+)$/.exec(aspect);
  if (match) {
    const w = Number(match[1]);
    const h = Number(match[2]);
    if (w > 0 && h > 0) return w / h;
  }
  if (fallbackHeight <= 0) return 1;
  return fallbackWidth / fallbackHeight;
}

export function containedLayout(
  elementW: number,
  elementH: number,
  imageW: number,
  imageH: number,
): ContainedLayout {
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

export function clampCrop(rect: CropRect, imageW: number, imageH: number): CropRect {
  const width = Math.min(Math.max(1, Math.round(rect.width)), imageW);
  const height = Math.min(Math.max(1, Math.round(rect.height)), imageH);
  const x = Math.min(Math.max(0, Math.round(rect.x)), Math.max(0, imageW - width));
  const y = Math.min(Math.max(0, Math.round(rect.y)), Math.max(0, imageH - height));
  return { x, y, width, height };
}

/** Move a fixed-size crop; slides along edges when the drag would leave the image. */
export function moveCrop(rect: CropRect, dx: number, dy: number, imageW: number, imageH: number): CropRect {
  return clampCrop({ ...rect, x: rect.x + dx, y: rect.y + dy }, imageW, imageH);
}

/**
 * Resize from a corner while locking aspect ratio.
 * Corner is nw|ne|sw|se. Pointer deltas are in image pixels.
 */
export function resizeCropAspect(
  start: CropRect,
  corner: "nw" | "ne" | "sw" | "se",
  dx: number,
  dy: number,
  aspect: number,
  imageW: number,
  imageH: number,
): CropRect {
  if (aspect <= 0) return start;
  let { x, y, width, height } = start;
  const maxW = imageW;
  const maxH = imageH;

  if (corner === "se") {
    const byW = Math.max(1, width + dx);
    const byH = Math.max(1, height + dy);
    if (Math.abs(dx) * aspect >= Math.abs(dy)) {
      width = byW;
      height = Math.max(1, Math.round(width / aspect));
    } else {
      height = byH;
      width = Math.max(1, Math.round(height * aspect));
    }
  } else if (corner === "sw") {
    const byW = Math.max(1, width - dx);
    const byH = Math.max(1, height + dy);
    if (Math.abs(dx) * aspect >= Math.abs(dy)) {
      width = byW;
      height = Math.max(1, Math.round(width / aspect));
    } else {
      height = byH;
      width = Math.max(1, Math.round(height * aspect));
    }
    x = start.x + start.width - width;
  } else if (corner === "ne") {
    const byW = Math.max(1, width + dx);
    const byH = Math.max(1, height - dy);
    if (Math.abs(dx) * aspect >= Math.abs(dy)) {
      width = byW;
      height = Math.max(1, Math.round(width / aspect));
    } else {
      height = byH;
      width = Math.max(1, Math.round(height * aspect));
    }
    y = start.y + start.height - height;
  } else {
    const byW = Math.max(1, width - dx);
    const byH = Math.max(1, height - dy);
    if (Math.abs(dx) * aspect >= Math.abs(dy)) {
      width = byW;
      height = Math.max(1, Math.round(width / aspect));
    } else {
      height = byH;
      width = Math.max(1, Math.round(height * aspect));
    }
    x = start.x + start.width - width;
    y = start.y + start.height - height;
  }

  width = Math.min(width, maxW);
  height = Math.min(height, maxH);
  // Re-sync the locked side after clamping one dimension to the image edge.
  if (width / height > aspect) {
    width = Math.max(1, Math.round(height * aspect));
  } else {
    height = Math.max(1, Math.round(width / aspect));
  }
  if (corner === "sw" || corner === "nw") x = start.x + start.width - width;
  if (corner === "ne" || corner === "nw") y = start.y + start.height - height;
  return clampCrop({ x, y, width, height }, imageW, imageH);
}

export function cropToCss(rect: CropRect, layout: ContainedLayout): {
  left: number;
  top: number;
  width: number;
  height: number;
} {
  return {
    left: layout.offsetX + rect.x * layout.scale,
    top: layout.offsetY + rect.y * layout.scale,
    width: rect.width * layout.scale,
    height: rect.height * layout.scale,
  };
}
