import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { MouseEvent as ReactMouseEvent, useCallback, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api";
import { lifecycleLabel } from "../lifecycle";
import type {
  CandidateBulkQueueResult,
  CandidateItem,
  CandidatePage,
  GeminiLabelJob,
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

  async function pollLabelJob(jobId: string, total: number) {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      try {
        const job = await api<GeminiLabelJob>(`/api/gemini/label/${jobId}`);
        const processed = job.processed ?? job.results?.length ?? 0;
        setLabelProgress(`Gemini labeling… ${processed}/${job.total ?? total}`);
        if (job.status === "succeeded" || job.status === "failed") {
          return job;
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          throw err;
        }
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
      const started = await api<{ job_id: string }>("/api/gemini/label", {
        method: "POST",
        body: JSON.stringify({ image_ids: ids }),
      });
      setLabelProgress(`Gemini labeling… 0/${ids.length}`);
      const job = await pollLabelJob(started.job_id, ids.length);
      if (job?.results?.length) {
        const failed = job.results.filter((row) => row.status !== "ok");
        setMessage(
          failed.length
            ? `Labeling finished with ${failed.length} issue(s). Refreshing list.`
            : `Generated labels and captions for ${job.results.length} image(s).`,
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
