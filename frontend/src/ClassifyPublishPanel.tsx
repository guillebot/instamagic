import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ButtonSpinner, PendingButtonLabel } from "./actionButton";
import { ActionLabel } from "./KeyHint";
import { ApiError, api } from "./api";
import {
  canApproveFromReadiness,
  canPublishNowFromReadiness,
  canQueueFromReadiness,
  canScheduleFromReadiness,
  instagramConnectivity,
  instagramHandle,
  pickDefaultCaption,
  type HealthCheckLite,
  type InstagramStatusLite,
} from "./reviewPublish";
import {
  defaultStoryShareValue,
  StoryShareControls,
  storyPreviewUrl,
  type StoryShareValue,
} from "./StoryShareControls";
import type { CaptionItem, ImageItem, ImagePublishReadiness, PostingScheduleConfig, QueueItem } from "./types";

type PublishOutcome = {
  permalink?: string | null;
  published_caption?: string | null;
};

type PostPhase = "idle" | "queueing" | "approving" | "checking" | "publishing" | "done" | "error";

const PHASE_LABEL: Record<PostPhase, string> = {
  idle: "",
  queueing: "Adding to the publishing queue…",
  approving: "Approving queue item…",
  checking: "Re-checking safety gates…",
  publishing: "Publishing to Instagram… (this can take up to a minute)",
  done: "Published.",
  error: "",
};

function describeError(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const kind =
      err.status === 409 || err.status === 403
        ? "Blocked"
        : err.status === 401
          ? "Session expired — sign in again"
          : err.status === 422
            ? "Invalid request"
            : err.status >= 500
              ? "Server error"
              : "Failed";
    return `${kind} (${err.status}): ${err.message}`;
  }
  if (err instanceof Error && err.message) return `${fallback}: ${err.message}`;
  return fallback;
}

export function ClassifyPublishPanel({
  image,
  geminiPolicy,
  onImageUpdated,
  onStatus,
}: {
  image: ImageItem;
  geminiPolicy: string;
  onImageUpdated: (patch: Partial<ImageItem> & { id: string }) => void;
  onStatus: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const readiness = useQuery({
    // Gate inputs are part of the key so a rating, blur approve/verify or crop approve
    // always re-reads server readiness instead of keeping a stale "blocked" answer.
    queryKey: [
      "publish-readiness",
      image.id,
      image.human_rating ?? null,
      image.model_state ?? null,
      image.blur_unlock_status ?? null,
      image.has_approved_blur ?? null,
      image.has_approved_crop ?? null,
    ],
    queryFn: () => api<ImagePublishReadiness>(`/api/images/${image.id}/publish-readiness`),
  });
  const captions = useQuery({
    queryKey: ["captions", image.id],
    queryFn: () => api<CaptionItem[]>(`/api/images/${image.id}/captions`),
  });
  const [selectedCaptionId, setSelectedCaptionId] = useState<string | null>(null);
  const [geminiConfirmed, setGeminiConfirmed] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [scheduleAt, setScheduleAt] = useState("");
  const [confirmPostOpen, setConfirmPostOpen] = useState(false);
  const [publishResult, setPublishResult] = useState<PublishOutcome | null>(null);
  const [postPhase, setPostPhase] = useState<PostPhase>("idle");
  const [postError, setPostError] = useState("");
  const [storyShare, setStoryShare] = useState<StoryShareValue>(() => defaultStoryShareValue());
  const confirmRef = useRef<HTMLDivElement | null>(null);
  const scheduleDefaults = useQuery({
    queryKey: ["posting-schedule-config"],
    queryFn: () => api<PostingScheduleConfig>("/api/posting-schedule/config"),
    staleTime: 60_000,
  });
  const igStatus = useQuery({
    queryKey: ["instagram-status"],
    queryFn: () => api<InstagramStatusLite>("/api/instagram/status"),
    staleTime: 20_000,
    retry: 1,
  });
  const health = useQuery({
    queryKey: ["health-status"],
    queryFn: () => api<{ checks: HealthCheckLite[] }>("/api/health/status"),
    staleTime: 20_000,
    retry: 1,
  });

  useEffect(() => {
    setConfirmPostOpen(false);
    setPublishResult(null);
    setPostPhase("idle");
    setPostError("");
    const cfg = scheduleDefaults.data;
    setStoryShare(
      defaultStoryShareValue({
        story_share_enabled: cfg?.story_share_default ?? false,
        story_render_mode: cfg?.story_render_mode_default ?? "blur_backdrop",
        story_border_color: cfg?.story_border_color_default ?? "black",
      }),
    );
  }, [image.id, scheduleDefaults.data?.story_share_default]);

  useEffect(() => {
    setSelectedCaptionId(pickDefaultCaption(captions.data));
  }, [captions.data, image.id]);

  const ready = readiness.data;
  const queueGate = canQueueFromReadiness(ready);
  const approveGate = canApproveFromReadiness(ready);
  const connectivity = instagramConnectivity(igStatus.data, health.data?.checks, {
    statusError: igStatus.isError && !igStatus.data,
  });
  const scheduleBase = canScheduleFromReadiness(ready, selectedCaptionId);
  const scheduleGate =
    scheduleBase.ok && !connectivity.ok ? { ok: false, reason: connectivity.reason } : scheduleBase;
  const publishGate = canPublishNowFromReadiness(ready, selectedCaptionId, connectivity);
  const target = instagramHandle(ready);
  const posting = ["queueing", "approving", "checking", "publishing"].includes(postPhase);

  // While the confirm step is open, Review hotkeys (1–4 rate+advance, arrows, Q, Esc close)
  // must not fire underneath it. Capture on window runs before the bubbling handlers.
  useEffect(() => {
    if (!confirmPostOpen) return;
    function onKeyCapture(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        if (!posting) setConfirmPostOpen(false);
        return;
      }
      if (event.key === "Tab" || event.key === "Enter" || event.key === " ") return;
      event.stopPropagation();
    }
    window.addEventListener("keydown", onKeyCapture, true);
    confirmRef.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    return () => window.removeEventListener("keydown", onKeyCapture, true);
  }, [confirmPostOpen, posting]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key.toLowerCase() !== "q" || confirmPostOpen) return;
      const targetEl = event.target as HTMLElement | null;
      if (targetEl && ["INPUT", "TEXTAREA", "SELECT"].includes(targetEl.tagName)) return;
      event.preventDefault();
      if (busy !== "") {
        onStatus("Still working on the previous action…");
        return;
      }
      if (!queueGate.ok) {
        const message = `Can't queue: ${queueGate.reason}`;
        setError(message);
        onStatus(message);
        return;
      }
      void queueOnly();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [image.id, queueGate.ok, queueGate.reason, busy, confirmPostOpen]);

  async function refreshAll() {
    await Promise.all([
      readiness.refetch(),
      captions.refetch(),
      igStatus.refetch(),
      queryClient.invalidateQueries({ queryKey: ["queue"] }),
    ]);
  }

  async function syncStoryToQueue(itemId: string) {
    await api(`/api/queue/${itemId}`, {
      method: "PATCH",
      body: JSON.stringify(storyShare),
    });
  }

  async function ensureQueueItem(): Promise<QueueItem> {
    const item = await api<QueueItem>(`/api/queue`, {
      method: "POST",
      body: JSON.stringify({ image_id: image.id, ...storyShare }),
    });
    onImageUpdated({ id: image.id, queued: true, queue_state: item.state });
    return item;
  }

  async function queueOnly() {
    setError("");
    setBusy("queue");
    try {
      await ensureQueueItem();
      onStatus("Added to the publishing queue.");
      await refreshAll();
    } catch (err) {
      const message = describeError(err, "Could not queue");
      setError(message);
      onStatus(message);
    } finally {
      setBusy("");
    }
  }

  async function queueAndApprove() {
    setError("");
    setBusy("approve");
    try {
      const item = await ensureQueueItem();
      if (item.state === "CANDIDATE") {
        await api(`/api/queue/${item.id}/approve`, { method: "POST" });
      }
      onStatus("Queued and approved for publish.");
      await refreshAll();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not approve.");
    } finally {
      setBusy("");
    }
  }

  async function generateCaptions() {
    if (!ready?.caption_generation_allowed && geminiPolicy === "manual_only" && !geminiConfirmed) {
      setError("Confirm the Gemini request first.");
      return;
    }
    if (!ready?.caption_generation_allowed) {
      setError(ready?.caption_generation_reasons.join(" ") || "Caption generation blocked.");
      return;
    }
    setError("");
    setBusy("captions");
    try {
      const rows = await api<CaptionItem[]>(`/api/images/${image.id}/captions`, {
        method: "POST",
        body: JSON.stringify({ confirmed: geminiPolicy !== "manual_only" || geminiConfirmed }),
      });
      await captions.refetch();
      if (rows[0]) setSelectedCaptionId(rows[0].id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Caption request failed.");
    } finally {
      setBusy("");
    }
  }

  async function scheduleAuto() {
    if (!ready?.queue_item_id || !selectedCaptionId) return;
    setBusy("schedule");
    setError("");
    try {
      await api(`/api/queue/${ready.queue_item_id}/schedule`, {
        method: "POST",
        body: JSON.stringify({ mode: "auto_slot", caption_id: selectedCaptionId }),
      });
      onStatus("Added to the auto posting schedule.");
      await refreshAll();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Schedule failed.");
    } finally {
      setBusy("");
    }
  }

  async function scheduleExplicit() {
    if (!ready?.queue_item_id || !selectedCaptionId || !scheduleAt) return;
    setBusy("schedule");
    setError("");
    try {
      const when = new Date(scheduleAt);
      if (Number.isNaN(when.getTime())) {
        setError("Pick a valid date and time.");
        return;
      }
      await api(`/api/queue/${ready.queue_item_id}/schedule`, {
        method: "POST",
        body: JSON.stringify({
          mode: "explicit",
          scheduled_at: when.toISOString(),
          caption_id: selectedCaptionId,
        }),
      });
      onStatus(`Scheduled for ${when.toLocaleString()}.`);
      await refreshAll();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Schedule failed.");
    } finally {
      setBusy("");
    }
  }

  function openPostConfirm() {
    setPostError("");
    setPostPhase("idle");
    setPublishResult(null);
    setConfirmPostOpen(true);
  }

  async function publishNow() {
    if (!ready || !selectedCaptionId || !publishGate.ok) return;
    setBusy("publish");
    setError("");
    setPostError("");
    try {
      let itemId = ready.queue_item_id;
      let state = ready.queue_state;
      if (!itemId || state === "CANDIDATE") {
        setPostPhase("queueing");
        const item = await ensureQueueItem();
        itemId = item.id;
        state = item.state;
      }
      if (state === "CANDIDATE") {
        setPostPhase("approving");
        await api(`/api/queue/${itemId}/approve`, { method: "POST" });
        state = "APPROVED";
      }
      await syncStoryToQueue(itemId);
      // Re-read server gates right before publishing (blur/DO_NOT_POST may have changed).
      setPostPhase("checking");
      const fresh = (await readiness.refetch()).data;
      const recheck = canPublishNowFromReadiness(fresh, selectedCaptionId, connectivity);
      if (!recheck.ok) throw new ApiError(409, recheck.reason);
      setPostPhase("publishing");
      const result = await api<QueueItem & { permalink?: string | null; published_caption?: string | null }>(
        `/api/queue/${itemId}/publish`,
        { method: "POST", body: JSON.stringify({ caption_id: selectedCaptionId }) },
      );
      setPublishResult({ permalink: result.permalink, published_caption: result.published_caption });
      setPostPhase("done");
      onStatus(result.permalink ? `Published to ${target}: ${result.permalink}` : `Published to ${target}.`);
      onImageUpdated({ id: image.id, queued: true, queue_state: "PUBLISHED" });
    } catch (err) {
      const message = describeError(err, "Publish failed");
      setPostError(message);
      setError(message);
      setPostPhase("error");
    } finally {
      setBusy("");
      void refreshAll().catch(() => undefined);
    }
  }

  const selectedCaption = captions.data?.find((row) => row.id === selectedCaptionId);
  const previewSrc = ready?.feed_preview_url ?? image.preview_url ?? image.thumbnail_url;
  const storyPreviewSrc = storyShare.story_share_enabled ? storyPreviewUrl(image.id, storyShare) : null;

  return (
    <section className="classify-publish" aria-label="Publish actions">
      <div className="classify-publish-badges">
        {ready?.queue_state ? <span className="badge queue-badge">{ready.queue_state}</span> : null}
        {ready?.scheduled_at ? (
          <span className="badge queue-badge">scheduled {new Date(ready.scheduled_at).toLocaleString()}</span>
        ) : null}
        {ready?.published_at ? (
          <span className="badge queue-badge">published {new Date(ready.published_at).toLocaleString()}</span>
        ) : null}
        {ready?.uses_approved_crop ? <span className="badge crop-approved">feed crop</span> : null}
      </div>
      <div className="row classify-publish-actions">
        <button
          type="button"
          className="button"
          title={queueGate.reason || undefined}
          aria-describedby={queueGate.ok ? undefined : `queue-reason-${image.id}`}
          disabled={!queueGate.ok || busy !== ""}
          onClick={() => void queueOnly()}
        >
          {busy === "queue" ? (
            <>
              <ButtonSpinner /> Queueing…
            </>
          ) : (
            <ActionLabel label="Queue" shortcut="Q" />
          )}
        </button>
        <button
          type="button"
          className="ghost"
          title={approveGate.reason || "Queue and approve"}
          disabled={!approveGate.ok || busy !== ""}
          onClick={() => void queueAndApprove()}
        >
          <PendingButtonLabel pending={busy === "approve"} pendingLabel="Saving…" label="Queue & approve" />
        </button>
        <button
          type="button"
          className="ghost"
          title={scheduleGate.reason || "Auto schedule slot"}
          disabled={!scheduleGate.ok || busy !== ""}
          onClick={() => void scheduleAuto()}
        >
          <PendingButtonLabel
            pending={busy === "schedule"}
            pendingLabel="Scheduling…"
            label="Schedule (auto)"
          />
        </button>
        <button
          type="button"
          className="button"
          title={publishGate.reason || `Post now to ${target}`}
          aria-describedby={publishGate.ok ? undefined : `post-now-reason-${image.id}`}
          disabled={!publishGate.ok || busy !== ""}
          onClick={openPostConfirm}
        >
          Post now
        </button>
      </div>
      {ready && !queueGate.ok && queueGate.reason ? (
        <p className="help classify-gate-reason" id={`queue-reason-${image.id}`} data-testid="queue-reason">
          Queue unavailable: {queueGate.reason}
          {!image.human_rating && !ready.published_at
            ? " Rate 3 (safe) or 4 (excellent) first — rating moves to the next photo; ← comes back."
            : ""}
        </p>
      ) : null}
      {!connectivity.ok && !connectivity.loading ? (
        <p className="error classify-ig-disconnected" role="alert">
          {connectivity.reason}{" "}
          <Link to="/instagram" className="button">
            Reconnect Instagram
          </Link>
        </p>
      ) : !publishGate.ok && publishGate.reason ? (
        <p className="help classify-gate-reason" id={`post-now-reason-${image.id}`}>
          Post now unavailable: {publishGate.reason}
        </p>
      ) : null}
      <div className="row classify-publish-schedule">
        <input
          type="datetime-local"
          aria-label="Schedule at"
          value={scheduleAt}
          onChange={(event) => setScheduleAt(event.target.value)}
        />
        <button
          type="button"
          className="ghost"
          disabled={!scheduleGate.ok || !scheduleAt || busy !== ""}
          onClick={() => void scheduleExplicit()}
        >
          Schedule at…
        </button>
      </div>
      <div className="row">
        <button type="button" className="ghost" disabled={busy === "captions"} onClick={() => void generateCaptions()}>
          {busy === "captions" ? "Generating…" : "Generate captions"}
        </button>
        {geminiPolicy === "manual_only" ? (
          <label className="help">
            <input
              type="checkbox"
              checked={geminiConfirmed}
              onChange={(event) => setGeminiConfirmed(event.target.checked)}
            />{" "}
            Confirm Gemini request
          </label>
        ) : null}
      </div>
      {!ready?.caption_generation_allowed && ready?.caption_generation_reasons.length ? (
        <p className="help">{ready.caption_generation_reasons.join(" ")}</p>
      ) : null}
      <StoryShareControls value={storyShare} onChange={setStoryShare} disabled={busy !== ""} compact />
      <div className="caption-pick compact">
        {captions.data?.map((caption) => (
          <label key={caption.id} className={selectedCaptionId === caption.id ? "picked" : ""}>
            <input
              type="radio"
              name={`classify-caption-${image.id}`}
              checked={selectedCaptionId === caption.id}
              onChange={() => setSelectedCaptionId(caption.id)}
            />
            <span>
              <strong>{caption.style} · {caption.language}</strong>
              <p>{caption.text}</p>
            </span>
          </label>
        ))}
      </div>
      {error ? <p className="error">{error}</p> : null}
      {publishResult?.permalink ? (
        <p className="help">
          Live:{" "}
          <a href={publishResult.permalink} target="_blank" rel="noreferrer">
            {publishResult.permalink}
          </a>
        </p>
      ) : null}
      {confirmPostOpen ? (
        <div
          className="overlay classify-post-confirm-overlay"
          role="presentation"
          onClick={(event) => {
            event.stopPropagation();
            if (!posting) setConfirmPostOpen(false);
          }}
        >
          <div
            ref={confirmRef}
            className="dialog classify-post-confirm"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby={`post-confirm-title-${image.id}`}
            onClick={(event) => event.stopPropagation()}
          >
            <h3 id={`post-confirm-title-${image.id}`}>Post now to {target}?</h3>
            {postPhase !== "done" ? (
              <p className="banner">
                This publishes immediately and publicly to <strong>{target}</strong>
                {storyShare.story_share_enabled ? " and will also publish an Instagram Story" : ""}. It cannot be
                undone from here.
              </p>
            ) : null}
            {previewSrc ? <img src={previewSrc} alt="" className="classify-post-preview" /> : null}
            {storyPreviewSrc ? (
              <img src={storyPreviewSrc} alt="" className="classify-story-preview" />
            ) : null}
            <p className="classify-meta">
              {image.relative_path}
              {ready?.uses_approved_crop ? " · approved crop derivative" : " · full preview"}
              {!ready?.queue_item_id ? " · will be queued and approved first" : ""}
            </p>
            <p className="classify-post-caption">{selectedCaption?.text ?? "No caption selected."}</p>
            {posting ? (
              <p className="help" role="status" aria-live="polite">
                <span className="spinner" aria-hidden="true" /> {PHASE_LABEL[postPhase]}
              </p>
            ) : null}
            {postPhase === "done" ? (
              <p className="classify-post-success" role="status">
                Published to {target}.{" "}
                {publishResult?.permalink ? (
                  <a href={publishResult.permalink} target="_blank" rel="noreferrer">
                    {publishResult.permalink}
                  </a>
                ) : (
                  <span className="muted">Permalink not returned yet — check the Queue page.</span>
                )}
              </p>
            ) : null}
            {postPhase === "error" && postError ? (
              <p className="error" role="alert">
                {postError}
              </p>
            ) : null}
            <div className="row">
              {postPhase === "done" ? (
                <button type="button" className="button" data-autofocus onClick={() => setConfirmPostOpen(false)}>
                  Close
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    className="ghost"
                    data-autofocus
                    disabled={posting}
                    onClick={() => setConfirmPostOpen(false)}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="button"
                    disabled={posting || !publishGate.ok}
                    onClick={() => void publishNow()}
                  >
                    {posting ? "Publishing…" : postPhase === "error" ? `Retry publish to ${target}` : `Publish to ${target}`}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
