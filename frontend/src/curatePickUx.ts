import type { CurationPick, CurationSkipped } from "./types";

/** Curate tabs: "ready" and "needs_caption" are the two halves of the pending pool. */
export type CurateFilterTab = "ready" | "needs_caption" | "queued" | "rejected";

export function isPendingCuratePick(pick: CurationPick): boolean {
  return pick.status === "suggested" || pick.status === "accepted";
}

export function pickHasCaption(pick: CurationPick): boolean {
  return pick.captions.some((row) => row.text.trim().length > 0);
}

export function isReadyCuratePick(pick: CurationPick): boolean {
  return isPendingCuratePick(pick) && pickHasCaption(pick);
}

export function isBlurVerifiedPick(pick: CurationPick): boolean {
  const image = pick.image;
  return image.blur_unlock_status === "verified" || Boolean(image.blur_verified && image.has_approved_blur);
}

export function filterCuratePicks(picks: CurationPick[], tab: CurateFilterTab): CurationPick[] {
  switch (tab) {
    case "ready":
      return picks.filter(isReadyCuratePick);
    case "needs_caption":
      return picks.filter((pick) => isPendingCuratePick(pick) && !pickHasCaption(pick));
    case "queued":
      return picks.filter((pick) => pick.status === "queued");
    case "rejected":
      return picks.filter((pick) => pick.status === "rejected");
    default:
      return [...picks];
  }
}

export function curateTabCounts(picks: CurationPick[]): Record<CurateFilterTab, number> {
  return {
    ready: filterCuratePicks(picks, "ready").length,
    needs_caption: filterCuratePicks(picks, "needs_caption").length,
    queued: filterCuratePicks(picks, "queued").length,
    rejected: filterCuratePicks(picks, "rejected").length,
  };
}

/** Short badge on grid tiles. */
export function pickTileBadge(pick: CurationPick): string {
  if (pick.status === "queued") return "Sent to queue";
  if (pick.status === "rejected") return "Rejected";
  if (!pickHasCaption(pick)) return pick.image.gemini_refused ? "Write caption" : "Caption pending";
  return "Ready";
}

export function nextPendingPickId(picks: CurationPick[], afterId: string | null): string | null {
  const pending = picks.filter(isPendingCuratePick);
  if (pending.length === 0) return null;
  if (!afterId) return pending[0]?.id ?? null;
  const index = pending.findIndex((pick) => pick.id === afterId);
  if (index < 0) return pending[0]?.id ?? null;
  return pending[index + 1]?.id ?? null;
}

export function neighborPickId(list: CurationPick[], currentId: string, delta: -1 | 1): string | null {
  const index = list.findIndex((pick) => pick.id === currentId);
  if (index < 0) return null;
  return list[index + delta]?.id ?? null;
}

export function isCurateTypingTarget(element: EventTarget | null): boolean {
  if (!(element instanceof HTMLElement)) return false;
  const tag = element.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return element.isContentEditable;
}

export function chunkGridRows<T>(items: T[], columnCount: number): T[][] {
  if (columnCount < 1) return [];
  const rows: T[][] = [];
  for (let index = 0; index < items.length; index += columnCount) {
    rows.push(items.slice(index, index + columnCount));
  }
  return rows;
}

export function columnCountForWidth(width: number, minTileWidth = 132): number {
  return Math.max(2, Math.floor(width / minTileWidth));
}

export type CuratePendingSort = "score" | "rated";

/** Score order is the batch's own sort_order; "rated" puts the most recently rated first. */
export function sortCuratePicks(picks: CurationPick[], mode: CuratePendingSort): CurationPick[] {
  const rows = [...picks];
  if (mode === "rated") {
    return rows.sort((a, b) => {
      const left = a.rated_at ?? "";
      const right = b.rated_at ?? "";
      if (left !== right) return left < right ? 1 : -1;
      return a.sort_order - b.sort_order;
    });
  }
  return rows.sort((a, b) => a.sort_order - b.sort_order);
}

/** "5 safe-rated since last run: 3 picked, 2 skipped" (null before the first run). */
export function curateSinceLastRunLine(skipped: CurationSkipped | null | undefined): string | null {
  if (!skipped || !skipped.since) return null;
  return `${skipped.since_rated} safe-rated since last run: ${skipped.since_picked} picked, ${skipped.since_skipped} skipped`;
}
