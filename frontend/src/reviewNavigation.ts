import type { ImageItem } from "./types";

export type GridImageItem = ImageItem & { gridStale?: boolean };

/** Sort key aligned with API: ingested_at desc, id desc. */
export function imageSortKey(item: ImageItem): [number, string] {
  const ingested = item.ingested_at ? Date.parse(item.ingested_at) : 0;
  return [ingested, item.id];
}

export function compareImagesNewestFirst(a: ImageItem, b: ImageItem): number {
  const [ta, ida] = imageSortKey(a);
  const [tb, idb] = imageSortKey(b);
  if (tb !== ta) return tb - ta;
  return idb.localeCompare(ida);
}

export function sortImagesStable(items: ImageItem[]): GridImageItem[] {
  return [...items]
    .sort(compareImagesNewestFirst)
    .map((row) => ({ ...row, gridStale: false }));
}

export function hardResetGridItems(fresh: ImageItem[]): GridImageItem[] {
  return fresh.map((row) => ({ ...row, gridStale: false }));
}

/** Update rows already on the grid; mark filter drop-outs stale instead of removing them. */
export function mergeGridUpdatesInPlace(
  previous: GridImageItem[],
  freshHead: ImageItem[],
): { items: GridImageItem[]; pendingNewItems: ImageItem[] } {
  const freshById = new Map(freshHead.map((row) => [row.id, row]));
  const prevIds = new Set(previous.map((row) => row.id));
  const pendingNewItems = freshHead.filter((row) => !prevIds.has(row.id));
  const items = previous.map((row) => {
    const updated = freshById.get(row.id);
    if (updated) return { ...updated, gridStale: false };
    return { ...row, gridStale: true };
  });
  return { items, pendingNewItems };
}

/** Append the next cursor page without reordering or duplicating existing rows. */
export function appendGridPage(previous: GridImageItem[], pageItems: ImageItem[]): GridImageItem[] {
  if (pageItems.length === 0) return previous;
  const seen = new Set(previous.map((row) => row.id));
  const appended: GridImageItem[] = [];
  for (const row of pageItems) {
    if (seen.has(row.id)) continue;
    seen.add(row.id);
    appended.push({ ...row, gridStale: false });
  }
  return appended.length ? [...previous, ...appended] : previous;
}

/** Insert scan-time newcomers: newer-than-head at front, otherwise at end in stable order. */
export function applyPendingNewPhotos(previous: GridImageItem[], pending: ImageItem[]): GridImageItem[] {
  const prevIds = new Set(previous.map((row) => row.id));
  const newcomers = pending.filter((row) => !prevIds.has(row.id));
  if (newcomers.length === 0) return previous;
  if (previous.length === 0) return sortImagesStable(newcomers);

  const headKey = imageSortKey(previous[0]);
  const atFront: ImageItem[] = [];
  const atEnd: ImageItem[] = [];
  for (const row of newcomers) {
    const key = imageSortKey(row);
    const newer =
      key[0] > headKey[0] || (key[0] === headKey[0] && key[1].localeCompare(headKey[1]) > 0);
    if (newer) atFront.push(row);
    else atEnd.push(row);
  }
  atFront.sort(compareImagesNewestFirst);
  atEnd.sort(compareImagesNewestFirst);
  return [
    ...atFront.map((row) => ({ ...row, gridStale: false })),
    ...previous,
    ...atEnd.map((row) => ({ ...row, gridStale: false })),
  ];
}

/** Merge a fresh API page into local state without moving the review cursor (legacy helper). */
export function mergeImageListRefresh(
  previous: ImageItem[],
  fresh: ImageItem[],
  currentId: string | null,
  pinned: ImageItem | null,
): { items: ImageItem[]; pinned: ImageItem | null } {
  const { items } = mergeGridUpdatesInPlace(
    previous.map((row) => ({ ...row, gridStale: false })),
    fresh,
  );
  let nextPinned = pinned;
  if (currentId) {
    const inFresh = fresh.find((item) => item.id === currentId);
    if (inFresh) {
      nextPinned = null;
    } else {
      const fromPrev = previous.find((item) => item.id === currentId);
      const fromPinned = pinned?.id === currentId ? pinned : null;
      nextPinned = fromPrev ?? fromPinned ?? nextPinned;
    }
  }
  return { items, pinned: nextPinned };
}

export function resolveCurrentImage(
  items: ImageItem[],
  currentId: string | null,
  pinned: ImageItem | null,
): ImageItem | undefined {
  if (!currentId) return undefined;
  return items.find((item) => item.id === currentId) ?? (pinned?.id === currentId ? pinned : undefined);
}

export function indexOfImageId(items: ImageItem[], id: string | null): number {
  if (!id) return -1;
  return items.findIndex((item) => item.id === id);
}

/** Next id after `currentId` in the list snapshot at action time (rating / skip / next). */
export function nextImageIdAfterCurrent(orderedIds: string[], currentId: string): string | null {
  const pos = orderedIds.indexOf(currentId);
  if (pos < 0 || pos >= orderedIds.length - 1) return null;
  return orderedIds[pos + 1];
}

export type ReviewNavStacks = {
  back: string[];
  forward: string[];
};

export function createNavStacks(): ReviewNavStacks {
  return { back: [], forward: [] };
}

/** Record a deliberate navigation to `nextId` (not back/forward). */
export function navVisit(stacks: ReviewNavStacks, currentId: string | null, nextId: string): ReviewNavStacks {
  if (!currentId || currentId === nextId) return stacks;
  return { back: [...stacks.back, currentId], forward: [] };
}

export function navBack(stacks: ReviewNavStacks, currentId: string): { stacks: ReviewNavStacks; targetId: string | null } {
  if (stacks.back.length === 0) return { stacks, targetId: null };
  const back = [...stacks.back];
  const targetId = back.pop() ?? null;
  if (!targetId) return { stacks, targetId: null };
  return {
    stacks: { back, forward: [...stacks.forward, currentId] },
    targetId,
  };
}

export function navForward(stacks: ReviewNavStacks, currentId: string): { stacks: ReviewNavStacks; targetId: string | null } {
  if (stacks.forward.length === 0) return { stacks, targetId: null };
  const forward = [...stacks.forward];
  const targetId = forward.pop() ?? null;
  if (!targetId) return { stacks, targetId: null };
  return {
    stacks: { back: [...stacks.back, currentId], forward },
    targetId,
  };
}

/**
 * Whether URL `image` param should drive selection on first load only.
 * After mount, returning the stale route id must not override in-session navigation.
 */
export function shouldApplyInitialImageFromUrl(
  initialized: boolean,
  items: ImageItem[],
  urlImageId: string | null,
): string | null {
  if (initialized || !urlImageId || items.length === 0) return null;
  return items.some((item) => item.id === urlImageId) ? urlImageId : null;
}

export type ScrollAnchor = { imageId: string; offsetBelowTop: number };

export function buildRowTopIndex(
  rows: { items: { id: string }[]; height: number }[],
  gap: number,
): Map<string, number> {
  const map = new Map<string, number>();
  let y = 0;
  for (const row of rows) {
    for (const item of row.items) {
      map.set(item.id, y);
    }
    y += row.height + gap;
  }
  return map;
}

export function captureScrollAnchor(
  scrollTop: number,
  imageId: string | null,
  rowTopsByImageId: Map<string, number>,
): ScrollAnchor | null {
  if (!imageId) return null;
  const rowTop = rowTopsByImageId.get(imageId);
  if (rowTop === undefined) return null;
  return { imageId, offsetBelowTop: scrollTop - rowTop };
}

export function scrollTopForAnchor(anchor: ScrollAnchor, rowTopsByImageId: Map<string, number>): number | null {
  const rowTop = rowTopsByImageId.get(anchor.imageId);
  if (rowTop === undefined) return null;
  return Math.max(0, rowTop + anchor.offsetBelowTop);
}
