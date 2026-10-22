/**
 * Review cursor / grid merge helpers (no browser).
 * Run: node frontend/src/reviewNavigation.test.mjs
 */

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function item(id, ingested = "") {
  const day = String(10 + id.charCodeAt(0) % 18).padStart(2, "0");
  return {
    id,
    relative_path: id,
    human_rating: null,
    ingested_at: ingested || `2024-03-${day}T00:00:00Z`,
  };
}

function imageSortKey(row) {
  const ingested = row.ingested_at ? Date.parse(row.ingested_at) : 0;
  return [ingested, row.id];
}

function compareImagesNewestFirst(a, b) {
  const [ta, ida] = imageSortKey(a);
  const [tb, idb] = imageSortKey(b);
  if (tb !== ta) return tb - ta;
  return idb.localeCompare(ida);
}

function mergeGridUpdatesInPlace(previous, freshHead) {
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

function appendGridPage(previous, pageItems) {
  const seen = new Set(previous.map((row) => row.id));
  const appended = [];
  for (const row of pageItems) {
    if (seen.has(row.id)) continue;
    seen.add(row.id);
    appended.push({ ...row, gridStale: false });
  }
  return appended.length ? [...previous, ...appended] : previous;
}

function applyPendingNewPhotos(previous, pending) {
  const prevIds = new Set(previous.map((row) => row.id));
  const newcomers = pending.filter((row) => !prevIds.has(row.id));
  if (newcomers.length === 0) return previous;
  if (previous.length === 0) return [...newcomers].sort(compareImagesNewestFirst).map((row) => ({ ...row, gridStale: false }));
  const headKey = imageSortKey(previous[0]);
  const atFront = [];
  const atEnd = [];
  for (const row of newcomers) {
    const key = imageSortKey(row);
    const newer = key[0] > headKey[0] || (key[0] === headKey[0] && key[1].localeCompare(headKey[1]) > 0);
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

function buildRowTopIndex(rows, gap) {
  const map = new Map();
  let y = 0;
  for (const row of rows) {
    for (const entry of row.items) map.set(entry.id, y);
    y += row.height + gap;
  }
  return map;
}

function captureScrollAnchor(scrollTop, imageId, rowTops) {
  if (!imageId) return null;
  const rowTop = rowTops.get(imageId);
  if (rowTop === undefined) return null;
  return { imageId, offsetBelowTop: scrollTop - rowTop };
}

function scrollTopForAnchor(anchor, rowTops) {
  const rowTop = rowTops.get(anchor.imageId);
  if (rowTop === undefined) return null;
  return Math.max(0, rowTop + anchor.offsetBelowTop);
}

function shouldApplyInitialImageFromUrl(initialized, items, urlImageId) {
  if (initialized || !urlImageId || items.length === 0) return null;
  return items.some((row) => row.id === urlImageId) ? urlImageId : null;
}

// In-place refresh keeps order and marks filtered-out rows stale.
const grid = [item("a"), item("b"), item("c")].map((row) => ({ ...row, gridStale: false }));
const head = [item("a"), item("c"), item("new")];
const merged = mergeGridUpdatesInPlace(grid, head);
assert(merged.items.map((row) => row.id).join(",") === "a,b,c", "grid order preserved");
assert(merged.items[1].gridStale === true, "missing from filter marked stale");
assert(merged.pendingNewItems.length === 1 && merged.pendingNewItems[0].id === "new", "new ids queued");

// Append page does not reorder or duplicate.
const page2 = [item("c"), item("d")];
const appended = appendGridPage(merged.items, page2);
assert(appended.map((row) => row.id).join(",") === "a,b,c,d", "append only new tail");

// Pending newcomers newer than head insert at front; others at end.
const withPending = applyPendingNewPhotos(appended, [
  item("z", "2099-01-01T00:00:00Z"),
  item("e", "2020-01-01T00:00:00Z"),
]);
assert(withPending[0].id === "z", "brand-new scan at front");
assert(withPending[withPending.length - 1].id === "e", "older newcomer at end");

// Scroll anchor survives row height change.
const rowsBefore = [{ items: [{ id: "a" }, { id: "b" }], height: 100 }];
const topsBefore = buildRowTopIndex(rowsBefore, 8);
const anchor = captureScrollAnchor(150, "b", topsBefore);
const rowsAfter = [
  { items: [{ id: "a" }, { id: "b" }], height: 100 },
  { items: [{ id: "c" }], height: 120 },
];
const topsAfter = buildRowTopIndex(rowsAfter, 8);
assert(scrollTopForAnchor(anchor, topsAfter) === 150, "anchor restores same scrollTop");

// Stale URL must not reset cursor after advance.
let initialized = false;
assert(shouldApplyInitialImageFromUrl(initialized, head, "a") === "a", "first mount uses URL");
initialized = true;
assert(shouldApplyInitialImageFromUrl(initialized, head, "a") === null, "stale URL ignored");

console.log("reviewNavigation.test.mjs: ok");
