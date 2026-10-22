/**
 * Run: node frontend/src/reviewPublish.test.mjs
 */

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function canQueueFromReadiness(readiness) {
  if (!readiness) return { ok: false, reason: "Loading publish gates…" };
  if (readiness.published_at || readiness.queue_state === "PUBLISHED") {
    return { ok: false, reason: "Already published." };
  }
  if (!readiness.publishing_allowed) {
    return { ok: false, reason: readiness.publishing_reasons.join(" ") };
  }
  return { ok: true, reason: "" };
}

function instagramHandle(readiness, fallback = "foto.sch") {
  const raw = readiness?.instagram_target_username?.trim();
  const handle = raw || fallback;
  return handle.startsWith("@") ? handle : `@${handle}`;
}

const blocked = {
  publishing_allowed: false,
  publishing_reasons: ["The local model marked this image DO_NOT_POST."],
  queue_state: null,
  published_at: null,
};
assert(!canQueueFromReadiness(blocked).ok, "DO_NOT_POST blocks queue");

const ready = {
  publishing_allowed: true,
  publishing_reasons: [],
  queue_state: null,
  published_at: null,
  instagram_target_username: "foto.sch",
};
assert(canQueueFromReadiness(ready).ok, "excellent path can queue");
assert(instagramHandle(ready) === "@foto.sch", "instagram handle formatting");

console.log("reviewPublish.test.mjs: ok");
