import type { ImageItem } from "./types";

export function publishVariantLabel(variant: string | null | undefined): string | null {
  if (!variant) return null;
  const labels: Record<string, string> = {
    ORIGINAL: "SAFE",
    BLURRED: "SAFE · BLURRED",
    CROPPED: "SAFE · CROPPED",
    BLURRED_CROPPED: "SAFE · BLURRED + CROPPED",
  };
  return labels[variant] ?? null;
}

/** Primary grid / card thumbnail (API already points at blurred derivatives when needed). */
export function displayThumbnailUrl(image: ImageItem): string | null {
  return image.thumbnail_url;
}

/** Classify / review preview; respects optional show-original toggle. */
export function displayPreviewUrl(image: ImageItem, showOriginal = false): string | null {
  if (showOriginal && image.original_preview_url) {
    return image.original_preview_url;
  }
  return image.preview_url ?? image.thumbnail_url;
}

/** Exact upload JPEG for publish confirmation. */
export function publishConfirmPreviewUrl(image: ImageItem): string | null {
  return image.publish_preview_url ?? image.preview_url ?? image.thumbnail_url;
}
