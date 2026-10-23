import type { CurationPick, CurationSkipped } from "./types";

export type CurateFilterTab = "pending" | "queued" | "rejected" | "all";

export function isPendingCuratePick(pick: CurationPick): boolean {
  return pick.status === "suggested" || pick.status === "accepted";
}

export function filterCuratePicks(picks: CurationPick[], tab: CurateFilterTab): CurationPick[] {
  switch (tab) {
    case "pending":
      return picks.filter(isPendingCuratePick);
    case "queued":
      return picks.filter((pick) => pick.status === "queued");
    case "rejected":
      return picks.filter((pick) => pick.status === "rejected");
    default:
      return [...picks];
  }
}

export function curateTabCounts(picks: CurationPick[]) {
  return {
    pending: picks.filter(isPendingCuratePick).length,
    queued: picks.filter((pick) => pick.status === "queued").length,
    rejected: picks.filter((pick) => pick.status === "rejected").length,
    all: picks.length,
  };
}

/** Short badge on grid tiles (status + safety presentation). */
export function pickTileBadge(pick: CurationPick): string {
  if (pick.status === "queued") return "queued";
  if (pick.status === "rejected") return "rejected";
  const image = pick.image;
  if (image.blur_unlock_message) return image.blur_unlock_message;
  if (image.publish_variant_label) return image.publish_variant_label;
  if (pick.status === "accepted") return "accepted";
  return "pending";
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
