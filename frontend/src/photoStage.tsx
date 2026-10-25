/**
 * Photo lifecycle stage — shared pill, filter chip bar and "new since last visit" helper.
 * The stage itself is computed server-side (`stage`, `stage_detail` on every image payload);
 * this module only presents it so Review, Curate, Queue and the editor all look the same.
 */
import { useEffect, useState } from "react";

export type PhotoStage = "new" | "to_review" | "candidate" | "scheduled" | "posted" | "rejected";

export const STAGE_ORDER: PhotoStage[] = ["new", "to_review", "candidate", "scheduled", "posted", "rejected"];

export const STAGE_LABELS: Record<PhotoStage, string> = {
  new: "New",
  to_review: "To review",
  candidate: "Candidate",
  scheduled: "Scheduled",
  posted: "Posted",
  rejected: "Rejected",
};

const DETAIL_LABELS: Record<string, string> = {
  caption_ready: "caption ready",
  needs_caption: "needs caption",
  in_queue: "in queue — approve",
  slot: "slot",
  awaiting_slot: "awaiting slot",
  publishing: "publishing",
  failed: "failed",
  rated_review: "rated review",
  blur_unverified: "blur not verified",
  blur_pending: "blur pending/failed",
};

export type StageInfo = {
  stage?: string | null;
  stage_detail?: string | null;
  posted_permalink?: string | null;
};

export function stageLabel(stage: string | null | undefined): string {
  return STAGE_LABELS[(stage ?? "new") as PhotoStage] ?? stage ?? "New";
}

export function stageDetailLabel(detail: string | null | undefined): string | null {
  if (!detail || detail === "posted") return null;
  return DETAIL_LABELS[detail] ?? detail.replace(/_/g, " ");
}

/** Colored stage pill. Posted is muted grey and links to the Instagram permalink when known. */
export function StagePill({ image, compact = false }: { image: StageInfo; compact?: boolean }) {
  const stage = (image.stage ?? "new") as PhotoStage;
  const detail = compact ? null : stageDetailLabel(image.stage_detail);
  const label = stageLabel(stage);
  const content = (
    <>
      {label}
      {detail ? <span className="stage-pill-sub"> · {detail}</span> : null}
    </>
  );
  const title = `Stage: ${label}${stageDetailLabel(image.stage_detail) ? ` (${stageDetailLabel(image.stage_detail)})` : ""}`;
  if (stage === "posted" && image.posted_permalink) {
    return (
      <a
        className={`stage-pill stage-${stage}`}
        href={image.posted_permalink}
        target="_blank"
        rel="noreferrer"
        title={`${title} — open on Instagram`}
        data-testid="stage-pill"
        onClick={(event) => event.stopPropagation()}
      >
        {content}
      </a>
    );
  }
  return (
    <span className={`stage-pill stage-${stage}`} title={title} data-testid="stage-pill">
      {content}
    </span>
  );
}

/** Filter chips with counts. `value` "" means All. */
export function StageChipBar({
  counts,
  value,
  onChange,
  stages = STAGE_ORDER,
  allLabel = "All",
  allCount,
}: {
  counts: Partial<Record<string, number>>;
  value: string;
  onChange: (stage: string) => void;
  stages?: PhotoStage[];
  allLabel?: string;
  allCount?: number;
}) {
  return (
    <div className="stage-chip-bar" role="group" aria-label="Filter by stage">
      <button
        type="button"
        className={`stage-chip${value === "" ? " active" : ""}`}
        aria-pressed={value === ""}
        onClick={() => onChange("")}
      >
        {allLabel}
        {allCount != null ? ` (${allCount})` : ""}
      </button>
      {stages.map((stage) => (
        <button
          key={stage}
          type="button"
          className={`stage-chip stage-chip-${stage}${value === stage ? " active" : ""}`}
          aria-pressed={value === stage}
          onClick={() => onChange(value === stage ? "" : stage)}
        >
          {STAGE_LABELS[stage]} ({counts[stage] ?? 0})
        </button>
      ))}
    </div>
  );
}

const LAST_VISIT_PREFIX = "instamagic.lastVisit.";

/**
 * Timestamp of the previous visit to `page` (localStorage). The new visit is recorded when the
 * page unmounts, so items ingested since the previous visit stay marked while you work.
 */
export function useLastVisit(page: string): number | null {
  const [previous] = useState<number | null>(() => {
    try {
      const raw = window.localStorage.getItem(LAST_VISIT_PREFIX + page);
      const value = raw ? Number(raw) : NaN;
      return Number.isFinite(value) ? value : null;
    } catch {
      return null;
    }
  });
  useEffect(() => {
    const record = () => {
      try {
        window.localStorage.setItem(LAST_VISIT_PREFIX + page, String(Date.now()));
      } catch {
        /* storage unavailable */
      }
    };
    window.addEventListener("pagehide", record);
    return () => {
      window.removeEventListener("pagehide", record);
      record();
    };
  }, [page]);
  return previous;
}

export function isNewSince(timestamp: string | null | undefined, lastVisit: number | null): boolean {
  if (!timestamp || lastVisit == null) return false;
  const value = Date.parse(timestamp);
  return Number.isFinite(value) && value > lastVisit;
}
