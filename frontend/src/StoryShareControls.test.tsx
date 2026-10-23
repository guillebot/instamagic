import { describe, expect, it } from "vitest";
import { defaultStoryShareValue, renderVersion, storyPreviewUrl } from "./StoryShareControls";

describe("storyPreviewUrl", () => {
  it("carries the publish render version so a moved blur busts the cached story", () => {
    const value = defaultStoryShareValue({});
    expect(renderVersion("/api/images/i/publish-preview?v=abc123")).toBe("abc123");
    expect(renderVersion("/api/images/i/publish-preview")).toBeNull();
    const a = storyPreviewUrl("i", value, "abc123");
    const b = storyPreviewUrl("i", value, "def456");
    expect(a).toContain("v=abc123");
    expect(a).not.toBe(b);
    expect(storyPreviewUrl("i", value)).not.toContain("v=");
  });
});
