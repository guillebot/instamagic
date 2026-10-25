export type BlurEdgeMode = "hard" | "soft";

export function defaultEdgeMode(shape: string): BlurEdgeMode {
  return shape === "rectangle" ? "hard" : "soft";
}

export type BlurShape = "ellipse" | "rectangle";
export const BLUR_SHAPE_STORAGE_KEY = "instamagic.blur.shape";

/** Last-used cover shape, or rectangle (hard edge) when none is saved. */
export function initialBlurShape(): BlurShape {
  try {
    const saved = window.localStorage.getItem(BLUR_SHAPE_STORAGE_KEY);
    if (saved === "ellipse" || saved === "rectangle") return saved;
  } catch {
    // Storage unavailable: fall through to the default.
  }
  return "rectangle";
}

export function rememberBlurShape(shape: BlurShape): void {
  try {
    window.localStorage.setItem(BLUR_SHAPE_STORAGE_KEY, shape);
  } catch {
    // Ignore storage failures.
  }
}
