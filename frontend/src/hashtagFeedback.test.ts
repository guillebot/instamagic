import { describe, expect, it } from "vitest";
import { countNewHashtags, hashtagSuccessMessage } from "./hashtagFeedback";

describe("hashtagFeedback", () => {
  it("counts only new normalized tags", () => {
    expect(countNewHashtags(["portrait"], ["#Portrait", "street", "STREET"])).toBe(1);
  });

  it("formats success messages", () => {
    expect(hashtagSuccessMessage(0)).toBe("No new suggestions");
    expect(hashtagSuccessMessage(1)).toBe("Added 1 hashtag");
    expect(hashtagSuccessMessage(3)).toBe("Added 3 hashtags");
  });
});
