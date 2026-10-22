import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { MouseEvent as ReactMouseEvent, useCallback, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api";
import { lifecycleLabel } from "../lifecycle";
import type {
  CandidateBulkQueueResult,
  CandidateItem,
  CandidatePage,
  CaptionItem,
  JobItem,
  QueueItem,
} from "../types";

const PAGE_SIZE = 20;

function statusBadges(candidate: CandidateItem) {
  const badges: string[] = [];
  if (candidate.has_tags) badges.push("tags");
  if (candidate.has_caption) badges.push("caption");
  if (candidate.image.queued) badges.push(`queue:${candidate.image.queue_state ?? "yes"}`);
  if (candidate.published) badges.push("published");
  if (candidate.gemini_label_status) badges.push(`gemini:${candidate.gemini_label_status}`);
  return badges;
}

export function CandidatesPage() {
  const queryClient = useQueryClient();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const anchorIndexRef = useRef(0);
  const [hideQueued, setHideQueued] = useState(true);
  const [hidePublished, setHidePublished] = useState(true);
  const [message, setMessage] = useState("");
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [labelProgress, setLabelProgress] = useState<string | null>(null);

  const queryKey = useMemo(
    () => ["candidates", hideQueued, hidePublished] as const,
    [hideQueued, hidePublished],
  );

  const candidates = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam = 0 }) => {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(pageParam),
        exclude_queued: hideQueued ? "true" : "false",
        exclude_published: hidePublished ? "true" : "false",
      });
      return api<CandidatePage>(`/api/candidates?${params.toString()}`);
    },
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
    refetchOnWindowFocus: false,
  });

  const rows = useMemo(
    () => candidates.data?.pages.flatMap((page) => page.items) ?? [],
    [candidates.data],
  );

  const toggleSelect = useCallback(
    (index: number, imageId: string, event: ReactMouseEvent) => {
      if (event.shiftKey) {
        const from = anchorIndexRef.current;
        const lo = Math.min(from, index);
        const hi = Math.max(from, index);
        setSelectedIds(new Set(rows.slice(lo, hi + 1).map((row) => row.image.id)));
        return;
      }
      anchorIndexRef.current = index;
      setSelectedIds((current) => {
        const next = new Set(current);
        if (next.has(imageId)) next.delete(imageId);
        else next.add(imageId);
        return next;
      });
    },
    [rows],
  );

  function selectAllVisible() {
    setSelectedIds(new Set(rows.map((row) => row.image.id)));
  }

  function clearSelection() {
    setSelectedIds(new Set());
  }

  async function refreshCandidates() {
    await queryClient.invalidateQueries({ queryKey });
  }

  async function addToQueue() {
    const ids = [...selectedIds];
    if (!ids.length) return;
    setBusy("queue");
    setMessage("");
    setActionErrors({});
    try {
      const result = await api<CandidateBulkQueueResult>("/api/candidates/queue", {
        method: "POST",
        body: JSON.stringify({ image_ids: ids }),
      });
      const errors: Record<string, string> = {};
      for (const row of result.results) {
        if (!row.ok && row.error) errors[row.image_id] = row.error;
      }
      setActionErrors(errors);
      setMessage(
        result.queued_count === ids.length
          ? `Added ${result.queued_count} to the publish queue.`
          : `Queued ${result.queued_count} of ${ids.length}. See per-item errors below.`,
      );
      await refreshCandidates();
      await queryClient.invalidateQueries({ queryKey: ["queue"] });
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Could not queue selected images.");
    } finally {
      setBusy(null);
    }
  }

  async function sendToCurate() {
    const ids = [...selectedIds];
    if (!ids.length) return;
    setBusy("curate");
    setMessage("");
    try {
      await api("/api/curation", {
        method: "POST",
        body: JSON.stringify({ image_ids: ids, limit: ids.length, include_images: false }),
      });
      setMessage(`Sent ${ids.length} image(s) to a new Curate batch.`);
      await queryClient.invalidateQueries({ queryKey: ["curation-batches"] });
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Curate batch failed.");
    } finally {
      setBusy(null);
    }
  }

  async function rejectSelected() {
    const ids = [...selectedIds];
    if (!ids.length) return;
    setBusy("reject");
    setMessage("");
    setActionErrors({});
    try {
      const results = await api<{ image_id: string; ok: boolean; error?: string | null }[]>(
        "/api/candidates/reject",
        { method: "POST", body: JSON.stringify({ image_ids: ids }) },
      );
      const errors: Record<string, string> = {};
      for (const row of results) {
        if (!row.ok && row.error) errors[row.image_id] = row.error;
      }
      setActionErrors(errors);
      const okCount = results.filter((row) => row.ok).length;
      setMessage(`Rejected ${okCount} of ${ids.length} from Next best posts.`);
      setSelectedIds((current) => {
        const next = new Set(current);
        for (const row of results) {
          if (row.ok) next.delete(row.image_id);
        }
        return next;
      });
      await refreshCandidates();
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Reject failed.");
    } finally {
      setBusy(null);
    }
  }

  async function pollLabelJob(jobId: string, imageCount: number) {
    for (let attempt = 0; attempt < 240; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      const job = await api<JobItem>(`/api/jobs/${jobId}`);
      const total = job.files_total ?? imageCount;
      const processed = job.files_processed ?? 0;
      setLabelProgress(`Gemini labeling… ${processed}/${total}`);
      if (job.status === "succeeded" || job.status === "failed" || job.status === "interrupted") {
        return job;
      }
    }
    return null;
  }

  async function generateLabels() {
    const ids = [...selectedIds];
    if (!ids.length) return;
    setBusy("label");
    setMessage("");
    setLabelProgress(null);
    try {
      const started = await api<{ job_id: string; status?: string }>("/api/gemini/label", {
        method: "POST",
        body: JSON.stringify({ image_ids: ids }),
      });
      setLabelProgress(`Gemini labeling… 0/${ids.length}`);
      const job = await pollLabelJob(started.job_id, ids.length);
      if (job?.status === "failed" && job.error) {
        setMessage(`Labeling failed: ${job.error}`);
      } else if (job?.status === "succeeded") {
        const skipped = job.files_skipped ?? 0;
        const failed = job.files_failed ?? 0;
        setMessage(
          skipped || failed
            ? `Labeling done (${job.files_processed ?? 0} ok, ${skipped} skipped, ${failed} failed).`
            : `Generated labels and captions for ${job.files_processed ?? ids.length} image(s).`,
        );
      } else {
        setMessage("Labeling job finished.");
      }
      await refreshCandidates();
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setMessage("Gemini bulk labeling is not available on this server yet.");
      } else {
        setMessage(err instanceof ApiError ? err.message : "Labeling request failed.");
      }
    } finally {
      setBusy(null);
      setLabelProgress(null);
    }
  }

  async function scheduleAutoSlot() {
    const ids = [...selectedIds];
    if (!ids.length) return;
    setBusy("schedule");
    setMessage("");
    setActionErrors({});
    try {
      const queueResult = await api<CandidateBulkQueueResult>("/api/candidates/queue", {
        method: "POST",
        body: JSON.stringify({ image_ids: ids }),
      });
      const errors: Record<string, string> = {};
      for (const row of queueResult.results) {
        if (!row.ok && row.error) errors[row.image_id] = row.error;
      }
      let queueItems = await api<QueueItem[]>("/api/queue");
      let scheduled = 0;
      for (const imageId of ids) {
        const gate = queueResult.results.find((row) => row.image_id === imageId);
        if (gate && !gate.ok) continue;
        let item = queueItems.find((row) => row.image.id === imageId);
        if (!item) {
          errors[imageId] = "Could not find queue item after enqueue.";
          continue;
        }
        try {
          if (item.state === "CANDIDATE") {
            item = await api<QueueItem>(`/api/queue/${item.id}/approve`, { method: "POST" });
          }
          if (item.state !== "APPROVED" && item.state !== "FAILED") {
            errors[imageId] = `Queue item is ${item.state}; approve before scheduling.`;
            continue;
          }
          let captionId = item.caption_id;
          if (!captionId) {
            const captions = await api<CaptionItem[]>(`/api/images/${imageId}/captions`);
            captionId =
              captions.find((row) => row.status !== "rejected")?.id ?? captions[0]?.id ?? null;
          }
          if (!captionId) {
            errors[imageId] = "No caption yet — run Generate labels + captions or add one in Queue.";
            continue;
          }
          await api(`/api/queue/${item.id}/schedule`, {
            method: "POST",
            body: JSON.stringify({ mode: "auto_slot", caption_id: captionId }),
          });
          scheduled += 1;
        } catch (err) {
          errors[imageId] = err instanceof ApiError ? err.message : "Schedule failed.";
        }
      }
      setActionErrors(errors);
      setMessage(
        scheduled === ids.length
          ? `Scheduled ${scheduled} in the next auto-slot.`
          : `Scheduled ${scheduled} of ${ids.length}. See per-item errors below.`,
      );
      await refreshCandidates();
      await queryClient.invalidateQueries({ queryKey: ["queue"] });
      await queryClient.invalidateQueries({ queryKey: ["posting-schedule"] });
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Could not schedule selected images.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="page candidates-page">
      <h1>Next best posts</h1>
      <p>Ranked from local sharpness, composition, your rating, and near-duplicate suppression.</p>

      <div className="filters">
        <label>
          <input
            type="checkbox"
            checked={hideQueued}
            onChange={(event) => setHideQueued(event.target.checked)}
          />
          Hide already queued
        </label>
        <label>
          <input
            type="checkbox"
            checked={hidePublished}
            onChange={(event) => setHidePublished(event.target.checked)}
          />
          Hide published
        </label>
        <button className="ghost" type="button" onClick={() => void refreshCandidates()}>
          Refresh
        </button>
      </div>

      {message ? <p className={message.includes("not available") ? "muted" : "ok"}>{message}</p> : null}
      {labelProgress ? <p className="muted">{labelProgress}</p> : null}

      <div className="card-list candidates-list">
        {rows.map((candidate, index) => {
          const id = candidate.image.id;
          const checked = selectedIds.has(id);
          const err = actionErrors[id];
          return (
            <article
              className={`card candidate-card${checked ? " candidate-selected" : ""}`}
              key={id}
              role="button"
              tabIndex={0}
              onClick={(event) => toggleSelect(index, id, event)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  toggleSelect(index, id, event as unknown as ReactMouseEvent);
                }
              }}
            >
              <div className="candidate-check">
                <input type="checkbox" checked={checked} readOnly tabIndex={-1} aria-label="Select candidate" />
              </div>
              <div className="candidate-thumb">
                {candidate.image.thumbnail_url ? (
                  <Link
                    to={`/?image=${id}`}
                    onClick={(event) => event.stopPropagation()}
                    title="Open in Review"
                  >
                    <img src={candidate.image.thumbnail_url} alt="" />
                  </Link>
                ) : (
                  <div className="candidate-thumb-empty" />
                )}
              </div>
              <div>
                <strong>Score {candidate.score}/100</strong>
                <p>
                  {lifecycleLabel(candidate.image.effective_state)} · model {candidate.model_state ?? "—"}
                  {candidate.crop_aspect ? ` · crop ${candidate.crop_aspect}` : ""}
                </p>
                {statusBadges(candidate).length ? (
                  <p className="candidate-badges">{statusBadges(candidate).join(" · ")}</p>
                ) : (
                  <p className="muted">No tags or captions yet</p>
                )}
                {err ? <p className="error">{err}</p> : null}
                <ul>
                  {candidate.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
            </article>
          );
        })}
        {candidates.isSuccess && rows.length === 0 ? (
          <p>Rate a few photographs safe or excellent before this list can rank them.</p>
        ) : null}
      </div>

      {candidates.hasNextPage ? (
        <button
          className="ghost"
          type="button"
          disabled={candidates.isFetchingNextPage}
          onClick={() => void candidates.fetchNextPage()}
        >
          {candidates.isFetchingNextPage ? "Loading…" : "Load more"}
        </button>
      ) : null}

      {selectedIds.size > 0 ? (
        <div className="candidates-action-bar" role="toolbar" aria-label="Candidate actions">
          <span className="candidates-action-count">{selectedIds.size} selected</span>
          <button className="ghost" type="button" onClick={selectAllVisible}>
            Select all visible
          </button>
          <button className="ghost" type="button" onClick={clearSelection}>
            Clear
          </button>
          <button className="button" type="button" disabled={busy !== null} onClick={() => void addToQueue()}>
            {busy === "queue" ? "Queueing…" : "Add to publish queue"}
          </button>
          <button className="button" type="button" disabled={busy !== null} onClick={() => void scheduleAutoSlot()}>
            {busy === "schedule" ? "Scheduling…" : "Schedule (auto-slot)"}
          </button>
          <button className="button" type="button" disabled={busy !== null} onClick={() => void generateLabels()}>
            {busy === "label" ? "Labeling…" : "Generate labels + captions"}
          </button>
          <button className="ghost" type="button" disabled={busy !== null} onClick={() => void sendToCurate()}>
            {busy === "curate" ? "Sending…" : "Send to Curate"}
          </button>
          <button className="ghost" type="button" disabled={busy !== null} onClick={() => void rejectSelected()}>
            {busy === "reject" ? "Rejecting…" : "Reject / hide"}
          </button>
        </div>
      ) : null}
    </main>
  );
}
