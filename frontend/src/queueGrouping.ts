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
