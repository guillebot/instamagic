import type { CaptionItem, ImagePublishReadiness } from "./types";

export function formatGateReasons(reasons: string[]): string {
  if (reasons.length === 0) return "";
  return reasons.join(" ");
}

export function canQueueFromReadiness(readiness: ImagePublishReadiness | undefined): {
  ok: boolean;
  reason: string;
} {
  if (!readiness) return { ok: false, reason: "Loading publish gates…" };
  if (readiness.published_at || readiness.queue_state === "PUBLISHED") {
    return { ok: false, reason: "Already published." };
  }
  if (!readiness.publishing_allowed) {
    return { ok: false, reason: formatGateReasons(readiness.publishing_reasons) };
  }
  return { ok: true, reason: "" };
}

export function canApproveFromReadiness(readiness: ImagePublishReadiness | undefined): {
  ok: boolean;
  reason: string;
} {
  if (!readiness) return { ok: false, reason: "Loading publish gates…" };
  if (readiness.published_at || readiness.queue_state === "PUBLISHED") {
    return { ok: false, reason: "Already published." };
  }
  if (!readiness.publishing_allowed) {
    return { ok: false, reason: formatGateReasons(readiness.publishing_reasons) };
  }
  if (readiness.queue_state === "CANDIDATE") return { ok: true, reason: "" };
  if (!readiness.queue_item_id) return { ok: true, reason: "" };
  if (readiness.queue_state === "APPROVED" || readiness.queue_state === "FAILED") {
    return { ok: false, reason: "Already approved." };
  }
  return { ok: false, reason: `Queue state is ${readiness.queue_state ?? "unknown"}.` };
}

export function canScheduleFromReadiness(
  readiness: ImagePublishReadiness | undefined,
  captionId: string | null,
): { ok: boolean; reason: string } {
  if (!readiness?.queue_item_id) return { ok: false, reason: "Queue and approve first." };
  if (readiness.queue_state !== "APPROVED" && readiness.queue_state !== "FAILED") {
    return { ok: false, reason: "Approve the queue item before scheduling." };
  }
  if (!captionId) return { ok: false, reason: "Pick or generate a caption." };
  if (readiness.published_at) return { ok: false, reason: "Already published." };
  return { ok: true, reason: "" };
}

export function canPublishNowFromReadiness(
  readiness: ImagePublishReadiness | undefined,
  captionId: string | null,
): { ok: boolean; reason: string } {
  if (!readiness?.instagram_configured) {
    return { ok: false, reason: "Instagram is not configured." };
  }
  const schedule = canScheduleFromReadiness(readiness, captionId);
  if (!schedule.ok) return schedule;
  return { ok: true, reason: "" };
}

export function pickDefaultCaption(captions: CaptionItem[] | undefined): string | null {
  if (!captions?.length) return null;
  const preferred = captions.find((row) => row.status === "used") ?? captions[0];
  return preferred?.id ?? null;
}

export function instagramHandle(readiness: ImagePublishReadiness | undefined, fallback = "foto.sch"): string {
  const raw = readiness?.instagram_target_username?.trim();
  const handle = raw || fallback;
  return handle.startsWith("@") ? handle : `@${handle}`;
}
