import { describe, expect, it } from "vitest";
import { blurVerificationLabel, canOfferMarkSafe, coverBorderRadius, isTextEntryTarget } from "./BlurPanel";

const dnp = (status: string, message: string | null = null) => ({
  model_state: "DO_NOT_POST",
  blur_unlock_status: status,
  blur_unlock_message: message,
});
const flags = { verifying: false, hasCovers: true, approved: true };

describe("blurVerificationLabel", () => {
  it("shows verifying while approve runs", () => {
    expect(blurVerificationLabel(dnp("none"), { ...flags, verifying: true })?.text).toMatch(/Verifying/);
    expect(blurVerificationLabel(dnp("pending"), flags)?.tone).toBe("pending");
  });
  it("shows SAFE · BLURRED only when the server confirms the verified geometry", () => {
    const ok = blurVerificationLabel(dnp("verified", "SAFE · BLURRED (verified)"), flags);
    expect(ok?.tone).toBe("ok");
    expect(ok?.text).toMatch(/SAFE · BLURRED \(verified\)/);
    const stale = blurVerificationLabel(dnp("verified", null), flags);
    expect(stale?.tone).toBe("error");
    expect(stale?.text).toMatch(/re-verify/);
  });
  it("names the uncovered areas on failure", () => {
    const failed = blurVerificationLabel(dnp("failed", "2 regions uncovered"), flags);
    expect(failed?.tone).toBe("error");
    expect(failed?.text).toMatch(/Verification failed — 2 regions uncovered\. Still blocked\./);
  });
  it("non-DO_NOT_POST covers say the blurred version is what gets published", () => {
    const label = blurVerificationLabel({ model_state: "REVIEW", blur_unlock_status: "none", blur_unlock_message: null }, flags);
    expect(label?.text).toMatch(/only the blurred version will be published/);
  });
  it("any pending edit hides SAFE · BLURRED and shows re-rendering", () => {
    const label = blurVerificationLabel(dnp("verified", "SAFE · BLURRED (verified)"), { ...flags, dirty: true });
    expect(label?.tone).toBe("pending");
    expect(label?.text).toMatch(/re-rendering/);
    expect(label?.text).not.toMatch(/SAFE/);
  });
  it("does not show SAFE when a current cover is not approved (geometry changed)", () => {
    const label = blurVerificationLabel(dnp("verified", "SAFE · BLURRED (verified)"), { ...flags, approved: false });
    expect(label?.tone).not.toBe("ok");
    expect(label?.text ?? "").not.toMatch(/SAFE/);
  });
});

describe("coverBorderRadius", () => {
  it("previews rectangles as rectangles (draft and saved covers)", () => {
    expect(coverBorderRadius("rectangle", 0, 0.5)).toBe("0px");
    expect(coverBorderRadius("rectangle", 20, 0.5)).toBe("10px");
    expect(coverBorderRadius("rectangle", null, 1)).toBe("0px");
  });
  it("keeps ellipses round", () => {
    expect(coverBorderRadius("ellipse", 0, 1)).toBe("50%");
  });
});

describe("isTextEntryTarget", () => {
  it("lets Enter/A through from selects, sliders and buttons", () => {
    const select = document.createElement("select");
    const range = Object.assign(document.createElement("input"), { type: "range" });
    const button = document.createElement("button");
    expect(isTextEntryTarget(select)).toBe(false);
    expect(isTextEntryTarget(range)).toBe(false);
    expect(isTextEntryTarget(button)).toBe(false);
    expect(isTextEntryTarget(document.body)).toBe(false);
    expect(isTextEntryTarget(null)).toBe(false);
  });
  it("keeps typing in text fields untouched", () => {
    expect(isTextEntryTarget(document.createElement("textarea"))).toBe(true);
    expect(isTextEntryTarget(Object.assign(document.createElement("input"), { type: "text" }))).toBe(true);
    expect(isTextEntryTarget(Object.assign(document.createElement("input"), { type: "number" }))).toBe(true);
  });
});

describe("canOfferMarkSafe", () => {
  const ok = { approved: true, dirty: false, hasCovers: true };
  it("offers Mark safe only after a verified SAFE · BLURRED result for flagged photos", () => {
    expect(canOfferMarkSafe({ ...dnp("verified", "SAFE · BLURRED (verified)"), human_rating: null }, ok)).toBe(true);
    expect(canOfferMarkSafe({ ...dnp("failed", "1 region uncovered"), human_rating: null }, ok)).toBe(false);
    expect(canOfferMarkSafe({ ...dnp("pending"), human_rating: null }, ok)).toBe(false);
    expect(canOfferMarkSafe({ ...dnp("verified", null), human_rating: null }, ok)).toBe(false);
  });
  it("hides it while edits are pending, covers unapproved, or already safe", () => {
    const v = { ...dnp("verified", "SAFE · BLURRED (verified)"), human_rating: null };
    expect(canOfferMarkSafe(v, { ...ok, dirty: true })).toBe(false);
    expect(canOfferMarkSafe(v, { ...ok, approved: false })).toBe(false);
    expect(canOfferMarkSafe({ ...v, human_rating: "safe" }, ok)).toBe(false);
  });
});
