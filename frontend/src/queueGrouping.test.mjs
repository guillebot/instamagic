/**
 * Run: node frontend/src/queueGrouping.test.mjs
 */

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const QUEUE_PUBLISHED_STATE = "PUBLISHED";

function splitQueueItems(items) {
  const active = [];
  const published = [];
  for (const item of items) {
    if (item.state === QUEUE_PUBLISHED_STATE) published.push(item);
    else active.push(item);
  }
  return { active, published };
}

function captionFirstLine(text, maxLen = 120) {
  if (!text?.trim()) return "—";
  const line = text.trim().split(/\n/)[0] ?? "";
  if (line.length <= maxLen) return line;
  return `${line.slice(0, maxLen - 1)}…`;
}

const mixed = [
  { id: "1", state: "APPROVED" },
  { id: "2", state: "PUBLISHED" },
  { id: "3", state: "CANDIDATE" },
  { id: "4", state: "PUBLISHED" },
];
const split = splitQueueItems(mixed);
assert(split.active.map((row) => row.id).join(",") === "1,3", "active keeps non-published");
assert(split.published.map((row) => row.id).join(",") === "2,4", "published bucket");

assert(captionFirstLine("Hello\nworld") === "Hello", "first line only");
assert(captionFirstLine("   ") === "—", "empty caption");

console.log("queueGrouping.test.mjs: ok");
