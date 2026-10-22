import assert from "node:assert/strict";
import { publishVariantLabel, publishConfirmPreviewUrl } from "./publishPresentation.ts";

assert.equal(publishVariantLabel("BLURRED"), "SAFE · BLURRED");
assert.equal(publishVariantLabel("BLURRED_CROPPED"), "SAFE · BLURRED + CROPPED");
assert.equal(
  publishConfirmPreviewUrl({ publish_preview_url: "/api/images/x/publish-preview" }),
  "/api/images/x/publish-preview",
);
console.log("publishPresentation.test.mjs ok");
