import { describe, expect, it } from "vitest";
import { coverageCheckLines, missedHint } from "./BlurPanel";
import type { BlurCoverageReport } from "./types";

const base = (over: Partial<BlurCoverageReport> = {}): BlurCoverageReport => ({
  min_coverage: 0.95,
  padding: 6,
  do_not_post: true,
  all_covered: false,
  coverage_check: "fail",
  regions: [
    {
      label: "FEMALE_BREAST_EXPOSED",
      score: 0.77,
      x: 341,
      y: 304,
      width: 186,
      height: 162,
      coverage: 0.6497,
      inside_outline: 0.8,
      covered: false,
      missed_px: { top: 27, bottom: 0, left: 0, right: 0 },
    },
    {
      label: "FEMALE_BREAST_EXPOSED",
      score: 0.74,
      x: 152,
      y: 309,
      width: 174,
      height: 152,
      coverage: 0.97,
      inside_outline: 1,
      covered: true,
      missed_px: { top: 0, bottom: 0, left: 0, right: 0 },
    },
  ],
  verification: {
    status: "failed",
    effective: false,
    current_geometry: true,
    recheck: "pass",
    classifier_detections: [],
    message: null,
  },
  ...over,
});

describe("coverage feedback", () => {
  it("names the coverage check, the percentage and the side to extend", () => {
    const lines = coverageCheckLines(base());
    expect(lines[0].tone).toBe("error");
    expect(lines[0].text).toMatch(/Coverage check ✗ — 1 of 2 flagged areas under 95%: breast 64% \(extend ~27px up\)/);
    expect(lines[1]).toEqual({ tone: "ok", text: expect.stringMatching(/Re-check on the blurred render ✓/) });
  });

  it("names the re-check when the classifier still detects exposure on the blurred render", () => {
    const report = base({
      regions: base().regions.map((row) => ({ ...row, covered: true, coverage: 0.99 })),
      verification: { ...base().verification, recheck: "fail", classifier_detections: [{ label: "FEMALE_BREAST_EXPOSED" }] },
    });
    const lines = coverageCheckLines(report);
    expect(lines[0].tone).toBe("ok");
    expect(lines[1].text).toMatch(/Re-check on the blurred render ✗ — classifier still detects breast/);
  });

  it("never reports a re-check result for unsaved or unverified geometry", () => {
    expect(coverageCheckLines(base(), true)[1].text).toMatch(/runs when you approve/);
    const stale = base({ verification: { ...base().verification, current_geometry: false } });
    expect(coverageCheckLines(stale)[1].text).toMatch(/runs when you approve/);
  });

  it("explains a thin core when no whole edge strip is missed", () => {
    expect(missedHint({ missed_px: { top: 0, bottom: 0, left: 0, right: 0 } })).toMatch(/lower feather/);
    expect(missedHint({ missed_px: { top: 3, bottom: 0, left: 5, right: 0 } })).toBe("extend ~3px up, ~5px left");
  });
});
