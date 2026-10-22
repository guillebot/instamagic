import assert from "node:assert/strict";

function publishVariantLabel(variant) {
  if (!variant) return null;
  const labels = {
    ORIGINAL: "SAFE",
    BLURRED: "SAFE · BLURRED",
    CROPPED: "SAFE · CROPPED",
    BLURRED_CROPPED: "SAFE · BLURRED + CROPPED",
  };
  return labels[variant] ?? null;
}

function publishConfirmPreviewUrl(image) {
  return image.publish_preview_url ?? image.preview_url ?? image.thumbnail_url;
}

assert.equal(publishVariantLabel("BLURRED"), "SAFE · BLURRED");
assert.equal(publishVariantLabel("BLURRED_CROPPED"), "SAFE · BLURRED + CROPPED");
assert.equal(
  publishConfirmPreviewUrl({ publish_preview_url: "/api/images/x/publish-preview" }),
  "/api/images/x/publish-preview",
);
console.log("publishPresentation.test.mjs ok");
