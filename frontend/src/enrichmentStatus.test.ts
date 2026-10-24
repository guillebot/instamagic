import { describe, expect, it } from "vitest";
import { enrichmentEtaMinutes, enrichmentLine, type EnrichmentStatus } from "./enrichmentStatus";

const base: EnrichmentStatus = {
  enabled: true,
  paused: false,
  daily_cap: 400,
  calls_today: 10,
  eligible: 1840,
  ready: 312,
  pending: 1500,
  errors: 3,
  errors_exhausted: 0,
  refused: 1,
  state: "running",
  batch_active: true,
  backoff_until: null,
  rpm: 30,
};

describe("enrichment status line", () => {
  it("formats progress and state", () => {
    expect(enrichmentLine(base)).toBe("Enrichment: rated-safe ready 312/1,840 · running");
    expect(
      enrichmentLine({ ...base, prefetch_enabled: true, prefetched: 150, prefetch_eligible: 21000 }),
    ).toBe("Enrichment: rated-safe ready 312/1,840 · prefetched 150/21,000 clean unrated · running");
    expect(enrichmentLine({ ...base, prefetch_enabled: false, prefetched: 150, prefetch_eligible: 21000 })).not.toContain(
      "prefetched",
    );
    expect(enrichmentLine({ ...base, state: "paused" })).toContain("· paused");
    expect(enrichmentLine({ ...base, state: "idle", pending: 0 })).toContain("up to date");
  });

  it("estimates minutes only while running", () => {
    expect(enrichmentEtaMinutes(base)).toBe(100);
    expect(enrichmentEtaMinutes({ ...base, state: "paused" })).toBeNull();
    expect(enrichmentEtaMinutes({ ...base, pending: 0 })).toBeNull();
  });
});
