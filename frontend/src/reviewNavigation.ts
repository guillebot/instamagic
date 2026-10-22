import type { ImageItem } from "./types";

/** Merge a fresh API page into local state without moving the review cursor. */
export function mergeImageListRefresh(
  previous: ImageItem[],
  fresh: ImageItem[],
  currentId: string | null,
  pinned: ImageItem | null,
): { items: ImageItem[]; pinned: ImageItem | null } {
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
  return { items: fresh, pinned: nextPinned };
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
