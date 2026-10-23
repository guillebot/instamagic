import { describe, expect, it } from "vitest";
import { coverageCheckLines, coverageVsOriginalLine, detectorProbeLine, missedHint } from "./BlurPanel";
import type { BlurCoverageReport, BlurDetectorProbe } from "./types";

const base = (over: Partial<BlurCoverageReport> = {}): BlurCoverageReport => ({
  min_coverage: 0.95,
  coverage_threshold_percent: 95,
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

const probePass: BlurDetectorProbe = {
  passed: true,
  top_score: 0.26,
  detections: [],
  model_name: "test",
  detector_available: true,
};

describe("coverage feedback", () => {
  it("summarizes coverage vs original flagged areas with required percent", () => {
    expect(coverageVsOriginalLine(base()).text).toMatch(/Coverage vs original flagged areas: 1\/2 ✗ \(needs 95%\)/);
    const allOk = base({
      regions: base().regions.map((row) => ({ ...row, covered: true, coverage: 0.99 })),
    });
    expect(coverageVsOriginalLine(allOk).text).toBe("Coverage vs original flagged areas: 2/2 ✓ (needs 95%)");
  });

  it("formats detector probe pass and fail lines", () => {
    expect(detectorProbeLine(probePass, false).text).toBe("Detector on blurred image: PASS (top score 0.26)");
    const fail: BlurDetectorProbe = {
      ...probePass,
      passed: false,
      detections: [{ label: "FEMALE_BREAST_EXPOSED", score: 0.91, x: 0, y: 0, width: 10, height: 10 }],
    };
    expect(detectorProbeLine(fail, false).text).toMatch(/FAIL: breast 91%/);
  });

  it("names uncovered regions in the detail line", () => {
    const lines = coverageCheckLines(base());
    expect(lines[0].tone).toBe("error");
    expect(lines[0].text).toMatch(/needs 95%/);
    expect(lines[0].text).toMatch(/breast 64%/);
  });

  it("names the re-check when the classifier still detects exposure on approve", () => {
    const report = base({
      regions: base().regions.map((row) => ({ ...row, covered: true, coverage: 0.99 })),
      verification: { ...base().verification, recheck: "fail", classifier_detections: [{ label: "FEMALE_BREAST_EXPOSED" }] },
    });
    const lines = coverageCheckLines(report);
    expect(lines[1].text).toMatch(/Last approve re-check ✗/);
  });

  it("never reports a re-check result for unsaved geometry", () => {
    expect(coverageCheckLines(base(), true)[1].text).toMatch(/Formal unlock/);
  });

  it("explains a thin core when no whole edge strip is missed", () => {
    expect(missedHint({ missed_px: { top: 0, bottom: 0, left: 0, right: 0 } })).toMatch(/lower feather/);
    expect(missedHint({ missed_px: { top: 3, bottom: 0, left: 5, right: 0 } })).toBe("extend ~3px up, ~5px left");
  });
});
