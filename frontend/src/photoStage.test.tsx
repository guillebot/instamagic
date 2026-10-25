import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StageChipBar, StagePill, isNewSince, stageDetailLabel } from "./photoStage";

describe("photo stage UI", () => {
  it("renders stage pills with sub-badges; Posted links to Instagram", () => {
    render(
      <>
        <StagePill image={{ stage: "candidate", stage_detail: "caption_ready" }} />
        <StagePill image={{ stage: "posted", stage_detail: "posted", posted_permalink: "https://instagram.com/p/x" }} />
        <StagePill image={{ stage: "rejected" }} />
      </>,
    );
    const pills = screen.getAllByTestId("stage-pill");
    expect(pills[0]).toHaveTextContent("Candidate · caption ready");
    expect(pills[0]).toHaveClass("stage-candidate");
    expect(pills[1]).toHaveTextContent("Posted");
    expect(pills[1]).toHaveAttribute("href", "https://instagram.com/p/x");
    expect(pills[2]).toHaveClass("stage-rejected");
    expect(stageDetailLabel("awaiting_slot")).toBe("awaiting slot");
  });

  it("chip bar shows counts and toggles a stage", async () => {
    const onChange = vi.fn();
    render(<StageChipBar counts={{ new: 3, posted: 9 }} value="new" allCount={12} onChange={onChange} />);
    expect(screen.getByRole("button", { name: "All (12)" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "New (3)" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Posted (9)" }));
    expect(onChange).toHaveBeenCalledWith("posted");
    await userEvent.click(screen.getByRole("button", { name: "New (3)" }));
    expect(onChange).toHaveBeenLastCalledWith("");
  });

  it("new since last visit compares ingested_at to the stored visit", () => {
    expect(isNewSince("2026-10-25T14:00:00Z", Date.parse("2026-10-25T13:00:00Z"))).toBe(true);
    expect(isNewSince("2026-10-25T12:00:00Z", Date.parse("2026-10-25T13:00:00Z"))).toBe(false);
    expect(isNewSince("2026-10-25T12:00:00Z", null)).toBe(false);
  });
});
