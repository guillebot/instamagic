import { describe, expect, it } from "vitest";
import { blurVerificationLabel } from "./BlurPanel";

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
});
