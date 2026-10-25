import type { QueueItem } from "./types";

export const QUEUE_PUBLISHED_STATE = "PUBLISHED";

const PUBLISHED_EXPANDED_KEY = "instamagic.queue.publishedExpanded";

export function splitQueueItems(items: QueueItem[]): { active: QueueItem[]; published: QueueItem[] } {
  const active: QueueItem[] = [];
  const published: QueueItem[] = [];
  for (const item of items) {
    if (item.state === QUEUE_PUBLISHED_STATE) published.push(item);
    else active.push(item);
  }
  return { active, published };
}

export function captionFirstLine(text: string | null | undefined, maxLen = 120): string {
  if (!text?.trim()) return "—";
  const line = text.trim().split(/\n/)[0] ?? "";
  if (line.length <= maxLen) return line;
  return `${line.slice(0, maxLen - 1)}…`;
}

export function readPublishedSectionExpanded(): boolean {
  try {
    return window.localStorage.getItem(PUBLISHED_EXPANDED_KEY) === "true";
  } catch {
    return false;
  }
}

export function writePublishedSectionExpanded(expanded: boolean): void {
  try {
    window.localStorage.setItem(PUBLISHED_EXPANDED_KEY, expanded ? "true" : "false");
  } catch {
    /* ignore quota / private mode */
  }
}

const COMPACT_VIEW_KEY = "instamagic.queue.compactView";
export const QUEUE_DISPLAY_TIMEZONE = "America/Argentina/Buenos_Aires";

/** APPROVED with an auto/explicit slot: nothing left to do, so collapsed by default. */
export function isAutoScheduled(item: QueueItem): boolean {
  return item.state === "APPROVED" && Boolean(item.scheduled_at);
}

/** Split active (non-published) items into a time-sorted Scheduled group and Needs action. */
export function groupActiveQueue(items: QueueItem[]): { scheduled: QueueItem[]; needsAction: QueueItem[] } {
  const scheduled: QueueItem[] = [];
  const needsAction: QueueItem[] = [];
  for (const item of items) {
    if (isAutoScheduled(item)) scheduled.push(item);
    else needsAction.push(item);
  }
  scheduled.sort((a, b) => Date.parse(a.scheduled_at ?? "") - Date.parse(b.scheduled_at ?? ""));
  return { scheduled, needsAction };
}

export function queuePostTypeLabel(item: QueueItem): string {
  if (item.media_type === "STORY") return "Story";
  if (item.media_type === "REELS") return "Reel";
  if (item.media_type === "CAROUSEL" && (item.slides?.length ?? 0) > 1) return "Carousel";
  return "Feed";
}

export function formatArt(iso: string | null | undefined): string {
  if (!iso) return "not scheduled";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "not scheduled";
  const text = date.toLocaleString("en-GB", {
    timeZone: QUEUE_DISPLAY_TIMEZONE,
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return `${text} ART`;
}

export function hashtagCount(text: string | null | undefined): number {
  if (!text) return 0;
  return (text.match(/(^|\s)#[\p{L}\p{N}_]+/gu) ?? []).length;
}

/** First caption line with hashtags stripped (they are summarised as a count). */
export function captionSummary(text: string | null | undefined, maxLen = 90): string {
  if (!text?.trim()) return "—";
  const lines = text.trim().split(/\n/).map((l) => l.replace(/(^|\s)#[\p{L}\p{N}_]+/gu, "").trim());
  return captionFirstLine(lines.find((l) => l.length > 0) ?? "", maxLen);
}

export function readCompactView(): boolean {
  try {
    return window.localStorage.getItem(COMPACT_VIEW_KEY) === "true";
  } catch {
    return false;
  }
}

export function writeCompactView(on: boolean): void {
  try {
    window.localStorage.setItem(COMPACT_VIEW_KEY, on ? "true" : "false");
  } catch {
    /* ignore quota / private mode */
  }
}
