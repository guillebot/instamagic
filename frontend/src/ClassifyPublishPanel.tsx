import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, api } from "./api";
import {
  canApproveFromReadiness,
  canPublishNowFromReadiness,
  canQueueFromReadiness,
  canScheduleFromReadiness,
  instagramHandle,
  pickDefaultCaption,
} from "./reviewPublish";
import type { CaptionItem, ImageItem, ImagePublishReadiness, QueueItem } from "./types";

type PublishOutcome = {
  permalink?: string;
  published_caption?: string;
};

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
    queryKey: ["publish-readiness", image.id],
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

  useEffect(() => {
    setSelectedCaptionId(pickDefaultCaption(captions.data));
  }, [captions.data, image.id]);

  const ready = readiness.data;
  const queueGate = canQueueFromReadiness(ready);
  const approveGate = canApproveFromReadiness(ready);
  const scheduleGate = canScheduleFromReadiness(ready, selectedCaptionId);
  const publishGate = canPublishNowFromReadiness(ready, selectedCaptionId);
  const target = instagramHandle(ready);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key.toLowerCase() !== "q") return;
      const targetEl = event.target as HTMLElement | null;
      if (targetEl && ["INPUT", "TEXTAREA", "SELECT"].includes(targetEl.tagName)) return;
      event.preventDefault();
      if (queueGate.ok && busy === "") void queueOnly();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [image.id, queueGate.ok, busy]);

  async function refreshAll() {
    await Promise.all([
      readiness.refetch(),
      captions.refetch(),
      queryClient.invalidateQueries({ queryKey: ["queue"] }),
    ]);
  }

  async function ensureQueueItem(): Promise<QueueItem> {
    const item = await api<QueueItem>(`/api/queue`, {
      method: "POST",
      body: JSON.stringify({ image_id: image.id }),
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
      setError(err instanceof ApiError ? err.message : "Could not queue.");
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

  async function publishNow() {
    if (!ready?.queue_item_id || !selectedCaptionId) return;
    setBusy("publish");
    setError("");
    try {
      let itemId = ready.queue_item_id;
      if (ready.queue_state === "CANDIDATE") {
        const item = await ensureQueueItem();
        itemId = item.id;
        await api(`/api/queue/${itemId}/approve`, { method: "POST" });
      } else if (ready.queue_state !== "APPROVED" && ready.queue_state !== "FAILED") {
        throw new ApiError(409, "Approve the queue item before publishing.");
      }
      const result = await api<QueueItem & { permalink?: string; published_caption?: string }>(
        `/api/queue/${itemId}/publish`,
        {
        method: "POST",
        body: JSON.stringify({ caption_id: selectedCaptionId }),
      });
      setPublishResult({ permalink: result.permalink, published_caption: result.published_caption });
      onStatus("Published to Instagram.");
      onImageUpdated({ id: image.id, queued: true, queue_state: "PUBLISHED" });
      setConfirmPostOpen(false);
      await refreshAll();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Publish failed.");
    } finally {
      setBusy("");
    }
  }

  const selectedCaption = captions.data?.find((row) => row.id === selectedCaptionId);
  const previewSrc = ready?.feed_preview_url ?? image.preview_url ?? image.thumbnail_url;

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
          title={queueGate.reason || "Queue (Q)"}
          disabled={!queueGate.ok || busy !== ""}
          onClick={() => void queueOnly()}
        >
          {busy === "queue" ? "Queueing…" : "Queue"}
        </button>
        <button
          type="button"
          className="ghost"
          title={approveGate.reason || "Queue and approve"}
          disabled={!approveGate.ok || busy !== ""}
          onClick={() => void queueAndApprove()}
        >
          {busy === "approve" ? "Saving…" : "Queue & approve"}
        </button>
        <button
          type="button"
          className="ghost"
          title={scheduleGate.reason || "Auto schedule slot"}
          disabled={!scheduleGate.ok || busy !== ""}
          onClick={() => void scheduleAuto()}
        >
          Schedule (auto)
        </button>
        <button
          type="button"
          className="button"
          title={publishGate.reason || "Post now"}
          disabled={!publishGate.ok || busy !== ""}
          onClick={() => setConfirmPostOpen(true)}
        >
          Post now
        </button>
      </div>
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
        <div className="classify-post-confirm" role="dialog" aria-modal="true" aria-label="Confirm post">
          <div className="classify-post-confirm-body">
            <h3>Post now to {target}?</h3>
            {previewSrc ? <img src={previewSrc} alt="" className="classify-post-preview" /> : null}
            <p className="classify-meta">
              {image.relative_path}
              {ready?.uses_approved_crop ? " · approved crop derivative" : " · full preview"}
            </p>
            <p>{selectedCaption?.text ?? "No caption selected."}</p>
            <div className="row">
              <button type="button" className="ghost" disabled={busy === "publish"} onClick={() => setConfirmPostOpen(false)}>
                Cancel
              </button>
              <button type="button" className="button" disabled={busy === "publish"} onClick={() => void publishNow()}>
                {busy === "publish" ? "Publishing…" : `Publish to ${target}`}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
