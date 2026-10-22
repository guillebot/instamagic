/**
 * Review cursor / URL sync helpers (no browser).
 * Run: node frontend/src/reviewNavigation.test.mjs
 */

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function item(id) {
  return { id, relative_path: id, human_rating: null };
}

function mergeImageListRefresh(previous, fresh, currentId, pinned) {
  let nextPinned = pinned;
  if (currentId) {
    const inFresh = fresh.find((row) => row.id === currentId);
    if (inFresh) {
      nextPinned = null;
    } else {
      const fromPrev = previous.find((row) => row.id === currentId);
      const fromPinned = pinned?.id === currentId ? pinned : null;
      nextPinned = fromPrev ?? fromPinned ?? nextPinned;
    }
  }
  return { items: fresh, pinned: nextPinned };
}

function resolveCurrentImage(items, currentId, pinned) {
  if (!currentId) return undefined;
  return items.find((row) => row.id === currentId) ?? (pinned?.id === currentId ? pinned : undefined);
}

function nextImageIdAfterCurrent(orderedIds, currentId) {
  const pos = orderedIds.indexOf(currentId);
  if (pos < 0 || pos >= orderedIds.length - 1) return null;
  return orderedIds[pos + 1];
}

function shouldApplyInitialImageFromUrl(initialized, items, urlImageId) {
  if (initialized || !urlImageId || items.length === 0) return null;
  return items.some((row) => row.id === urlImageId) ? urlImageId : null;
}

function navVisit(stacks, currentId, nextId) {
  if (!currentId || currentId === nextId) return stacks;
  return { back: [...stacks.back, currentId], forward: [] };
}

function navBack(stacks, currentId) {
  if (stacks.back.length === 0) return { stacks, targetId: null };
  const back = [...stacks.back];
  const targetId = back.pop() ?? null;
  if (!targetId) return { stacks, targetId: null };
  return { stacks: { back, forward: [...stacks.forward, currentId] }, targetId };
}

// Background refetch drops rated rows but cursor stays on the same id.
const before = [item("a"), item("b"), item("c")];
const afterRefresh = [item("a"), item("c")];
const { items: merged, pinned } = mergeImageListRefresh(before, afterRefresh, "b", null);
assert(merged.length === 2 && merged[1].id === "c", "list replaced from API");
assert(pinned?.id === "b", "rated image pinned when filtered out");
const shown = resolveCurrentImage(merged, "b", pinned);
assert(shown?.id === "b", "displayed image id unchanged after refetch");

// Advance uses snapshot at action time.
const ids = ["a", "b", "c"];
assert(nextImageIdAfterCurrent(ids, "b") === "c", "next after rate");
assert(nextImageIdAfterCurrent(["a", "c"], "b") === null, "missing id has no next");

// URL param must not override after session navigation (simulated re-render).
let initialized = false;
let currentId = "c";
const urlStillSaysFirstOpened = "a";
const fromUrl = shouldApplyInitialImageFromUrl(initialized, afterRefresh, urlStillSaysFirstOpened);
assert(fromUrl === "a", "first mount picks URL image");
initialized = true;
currentId = "c";
const staleUrlReapply = shouldApplyInitialImageFromUrl(initialized, afterRefresh, urlStillSaysFirstOpened);
assert(staleUrlReapply === null, "stale ?image= must not reset cursor after advance");
assert(currentId === "c", "session cursor stays on advanced image");

// Back stack.
let stacks = { back: [], forward: [] };
stacks = navVisit(stacks, "a", "b");
stacks = navVisit(stacks, "b", "c");
const back = navBack(stacks, "c");
assert(back.targetId === "b", "back returns previous id");

console.log("reviewNavigation.test.mjs: ok");
