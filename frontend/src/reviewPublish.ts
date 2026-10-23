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

export type InstagramStatusLite = {
  configured: boolean;
  token_configured?: boolean;
  business_account_id_configured?: boolean;
};

export type HealthCheckLite = { id: string; status: string; detail: string };

export type Connectivity = { ok: boolean; loading: boolean; reason: string };

/**
 * Instagram connectivity for publish actions, from /api/instagram/status and the
 * `instagram` check of /api/health/status. Unknown (still loading) never enables posting.
 */
export function instagramConnectivity(
  status: InstagramStatusLite | undefined,
  healthChecks: HealthCheckLite[] | undefined,
  opts: { statusError?: boolean } = {},
): Connectivity {
  if (opts.statusError) {
    return { ok: false, loading: false, reason: "Instagram status unavailable — reconnect to post." };
  }
  if (!status) return { ok: false, loading: true, reason: "Checking Instagram connection…" };
  if (!status.configured || status.token_configured === false) {
    return { ok: false, loading: false, reason: "Instagram disconnected — reconnect to post." };
  }
  if (status.business_account_id_configured === false) {
    return { ok: false, loading: false, reason: "Instagram account not linked — reconnect to post." };
  }
  const ig = healthChecks?.find((check) => check.id === "instagram");
  if (ig && ig.status === "down") {
    return {
      ok: false,
      loading: false,
      reason: `Instagram disconnected — reconnect to post.${ig.detail ? ` (${ig.detail})` : ""}`,
    };
  }
  return { ok: true, loading: false, reason: "" };
}

/** Queue states from which "Post now" may queue/approve/publish in one confirmed flow. */
const POSTABLE_STATES = new Set([null, "CANDIDATE", "APPROVED", "FAILED"]);

export function canPublishNowFromReadiness(
  readiness: ImagePublishReadiness | undefined,
  captionId: string | null,
  connectivity?: Connectivity,
): { ok: boolean; reason: string } {
  if (!readiness) return { ok: false, reason: "Loading publish gates…" };
  if (!readiness.instagram_configured) {
    return { ok: false, reason: "Instagram disconnected — reconnect to post." };
  }
  if (connectivity && !connectivity.ok) return { ok: false, reason: connectivity.reason };
  if (readiness.published_at || readiness.queue_state === "PUBLISHED") {
    return { ok: false, reason: "Already published." };
  }
  // Server-side safety gates (DO_NOT_POST, blur verification, attestation) — never bypassed.
  if (!readiness.publishing_allowed) {
    return { ok: false, reason: formatGateReasons(readiness.publishing_reasons) || "Publishing is blocked." };
  }
  if (!POSTABLE_STATES.has(readiness.queue_state)) {
    return { ok: false, reason: `Queue state is ${readiness.queue_state}; cannot post now.` };
  }
  if (!captionId) return { ok: false, reason: "Pick or generate a caption before posting." };
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
