import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { curateSinceLastRunLine, sortCuratePicks } from "./curatePickUx";
import { WhyNotPicked } from "./pages/Curate";
import type { CurationPick, CurationSkipped } from "./types";

afterEach(() => cleanup());

function pick(id: string, sortOrder: number, ratedAt: string | null): CurationPick {
  return {
    id,
    batch_id: "b",
    sort_order: sortOrder,
    gemini_rank: sortOrder,
    local_score: 0,
    reasons: [],
    suggested_tags: [],
    status: "suggested",
    preferred_caption_id: null,
    rated_at: ratedAt,
    image: {} as CurationPick["image"],
    captions: [],
  };
}

const SKIPPED: CurationSkipped = {
  since: "2026-10-23T19:00:00+00:00",
  since_rated: 5,
  since_picked: 3,
  since_skipped: 2,
  total: 2,
  truncated: false,
  items: [
    {
      image_id: "15f077f1-0000-0000-0000-000000000000",
      code: "dnp_coverage",
      reason: "blur coverage 83% < 95% threshold on 1 region (fix in editor)",
      rating: "safe",
      model_state: "DO_NOT_POST",
      rated_at: "2026-10-23T19:38:00+00:00",
      since_last_run: true,
      duplicate_of: null,
      queue_note: null,
      blur_editor: true,
      coverage_percent: 83,
      coverage_threshold_percent: 95,
      thumbnail_url: null,
      filename: "flagged.jpg",
    },
    {
      image_id: "aaaa0000-0000-0000-0000-000000000000",
      code: "near_duplicate",
      reason: "near-duplicate of 8b9116ee",
      rating: "safe",
      model_state: "SAFE",
      rated_at: "2026-10-23T19:37:00+00:00",
      since_last_run: true,
      duplicate_of: "8b9116ee-0000-0000-0000-000000000000",
      queue_note: null,
      blur_editor: false,
      thumbnail_url: "/api/derivatives/aaaa/thumbnail",
      filename: "dup.jpg",
    },
  ],
};

describe("curate why-not-picked", () => {
  it("sorts pending by score or most recently rated", () => {
    const rows = [pick("a", 1, "2026-10-01T00:00:00Z"), pick("b", 2, "2026-10-23T00:00:00Z"), pick("c", 3, null)];
    expect(sortCuratePicks(rows, "score").map((row) => row.id)).toEqual(["a", "b", "c"]);
    expect(sortCuratePicks(rows, "rated").map((row) => row.id)).toEqual(["b", "a", "c"]);
  });

  it("builds the since-last-run summary line", () => {
    expect(curateSinceLastRunLine(SKIPPED)).toBe("5 safe-rated since last run: 3 picked, 2 skipped");
    expect(curateSinceLastRunLine({ ...SKIPPED, since: null })).toBeNull();
    expect(curateSinceLastRunLine(null)).toBeNull();
  });

  it("lists reasons with a blur-editor link and no image for unblurred flagged items", () => {
    render(<WhyNotPicked skipped={SKIPPED} />);
    expect(screen.getByText(/blur coverage 83% < 95% threshold/)).toBeTruthy();
    expect(screen.getByText("near-duplicate of 8b9116ee")).toBeTruthy();
    const links = screen.getAllByRole("link", { name: "Open blur editor" });
    expect(links).toHaveLength(1);
    expect(links[0]!.getAttribute("href")).toBe("/?image=15f077f1-0000-0000-0000-000000000000&open=1");
    const flagged = document.querySelector('[data-code="dnp_coverage"]')!;
    expect(flagged.querySelector("img")).toBeNull();
  });
});
