import { useMutation } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api";
import {
  isCurateTypingTarget,
  isPendingCuratePick,
  neighborPickId,
} from "../curatePickUx";
import { lifecycleLabel } from "../lifecycle";
import { displayPreviewUrl, publishVariantLabel } from "../publishPresentation";
import type { CaptionItem, CurationPick, QueueItem } from "../types";

export type CurateModalAction = "queue" | "reject" | "skip";

type Props = {
  pick: CurationPick;
  orderedPicks: CurationPick[];
  geminiPolicy: string;
  scheduleAfterQueue: boolean;
  onScheduleAfterQueueChange: (value: boolean) => void;
  busy: boolean;
  onClose: () => void;
  onBusyChange: (busy: boolean) => void;
  onPickPatched: (pick: CurationPick) => void;
  onNavigateToPick: (pickId: string) => void;
  onAfterAction: (action: CurateModalAction, previousPickId: string) => void;
  onToast: (message: string, undo?: () => Promise<void>) => void;
};

function captionTextForPick(pick: CurationPick): string {
  const preferred =
    pick.captions.find((row) => row.id === pick.preferred_caption_id) ?? pick.captions[0];
  return preferred?.text ?? "";
}

function preferredCaptionId(pick: CurationPick): string | null {
  return pick.preferred_caption_id ?? pick.captions[0]?.id ?? null;
}

export function CuratePickModal({
  pick,
  orderedPicks,
  geminiPolicy,
  scheduleAfterQueue,
  onScheduleAfterQueueChange,
  busy,
  onClose,
  onBusyChange,
  onPickPatched,
  onNavigateToPick,
  onAfterAction,
  onToast,
}: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [captionId, setCaptionId] = useState(() => preferredCaptionId(pick));
  const [captionText, setCaptionText] = useState(() => captionTextForPick(pick));
  const [tags, setTags] = useState<string[]>(() => [...pick.suggested_tags]);
  const [tagInput, setTagInput] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setCaptionId(preferredCaptionId(pick));
    setCaptionText(captionTextForPick(pick));
    setTags([...pick.suggested_tags]);
    setTagInput("");
    setError("");
    dialogRef.current?.focus();
  }, [pick.id]);

  const hashtagSuggest = useMutation({
    mutationFn: () =>
      api<{ tags: string[] }>(`/api/images/${pick.image.id}/hashtags`, {
        method: "POST",
        body: JSON.stringify({ confirmed: geminiPolicy !== "manual_only" }),
      }),
    onSuccess: (body) => {
      const merged = [...tags];
      for (const raw of body.tags ?? []) {
        const name = raw.trim().replace(/^#/, "").toLowerCase();
        if (name && !merged.includes(name)) merged.push(name);
      }
      setTags(merged.slice(0, 12));
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Hashtag suggestion failed."),
  });

  const navIndex = useMemo(
    () => orderedPicks.findIndex((row) => row.id === pick.id),
    [orderedPicks, pick.id],
  );

  async function saveDraft(): Promise<void> {
    const body: Record<string, unknown> = {
      suggested_tags: tags,
    };
    if (captionId) {
      body.preferred_caption_id = captionId;
      body.caption_text = captionText.trim();
    }
    const updated = await api<CurationPick>(`/api/curation/picks/${pick.id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    onPickPatched(updated);
  }

  async function runQueue() {
    if (!isPendingCuratePick(pick)) {
      setError("This pick is no longer pending.");
      return;
    }
    onBusyChange(true);
    setError("");
    try {
      await saveDraft();
      const queued = await api<QueueItem[]>("/api/curation/queue", {
        method: "POST",
        body: JSON.stringify({ pick_ids: [pick.id] }),
      });
      if (scheduleAfterQueue && queued[0]) {
        await api(`/api/queue/${queued[0].id}/schedule`, {
          method: "POST",
          body: JSON.stringify({
            mode: "auto_slot",
            caption_id: captionId,
          }),
        });
      }
      onPickPatched({ ...pick, status: "queued", suggested_tags: tags });
      onToast("Sent to publish queue", async () => {
        const undone = await api<CurationPick>(`/api/curation/picks/${pick.id}/undo-queue`, {
          method: "POST",
        });
        onPickPatched(undone);
      });
      onAfterAction("queue", pick.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Send to queue failed.");
    } finally {
      onBusyChange(false);
    }
  }

  async function runReject() {
    if (!isPendingCuratePick(pick)) {
      setError("This pick is no longer pending.");
      return;
    }
    onBusyChange(true);
    setError("");
    try {
      const updated = await api<CurationPick>(`/api/curation/picks/${pick.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "rejected" }),
      });
      onPickPatched(updated);
      onToast("Rejected", async () => {
        const undone = await api<CurationPick>(`/api/curation/picks/${pick.id}`, {
          method: "PATCH",
          body: JSON.stringify({ status: "suggested" }),
        });
        onPickPatched(undone);
      });
      onAfterAction("reject", pick.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Reject failed.");
    } finally {
      onBusyChange(false);
    }
  }

  function runSkip() {
    onAfterAction("skip", pick.id);
  }

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (isCurateTypingTarget(event.target)) return;
      const key = event.key.toLowerCase();
      if (key === "escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (busy) return;
      if (key === "arrowleft") {
        event.preventDefault();
        const prev = neighborPickId(orderedPicks, pick.id, -1);
        if (prev) onNavigateToPick(prev);
        return;
      }
      if (key === "arrowright") {
        event.preventDefault();
        const next = neighborPickId(orderedPicks, pick.id, 1);
        if (next) onNavigateToPick(next);
        return;
      }
      if (key === "q") {
        event.preventDefault();
        void runQueue();
        return;
      }
      if (key === "r") {
        event.preventDefault();
        void runReject();
        return;
      }
      if (key === "s") {
        event.preventDefault();
        runSkip();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pick.id, orderedPicks, busy, onClose]);

  function addTagFromInput() {
    const name = tagInput.trim().replace(/^#/, "").toLowerCase();
    if (!name || tags.includes(name)) {
      setTagInput("");
      return;
    }
    setTags((prev) => [...prev, name].slice(0, 12));
    setTagInput("");
  }

  function selectCaption(caption: CaptionItem) {
    setCaptionId(caption.id);
    setCaptionText(caption.text);
  }

  const previewUrl = displayPreviewUrl(pick.image, false);
  const safetyLine =
    pick.image.blur_unlock_message ??
    pick.image.publish_variant_label ??
    publishVariantLabel(pick.image.publish_variant);

  return (
    <div className="overlay" role="presentation" onClick={onClose}>
      <div
        ref={dialogRef}
        className="dialog dialog-curate"
        role="dialog"
        aria-modal="true"
        aria-label="Curate pick"
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="curate-modal-toolbar">
          <p className="classify-meta">
            #{pick.sort_order} · Gemini #{pick.gemini_rank} · local {pick.local_score}
            {navIndex >= 0 ? ` · ${navIndex + 1}/${orderedPicks.length}` : ""}
          </p>
          <p className="muted curate-shortcuts">
            ← → prev/next · Q queue · R reject · S skip · Esc close
          </p>
          <button type="button" className="ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="curate-modal-body">
          <div className="curate-modal-preview">
            {previewUrl ? <img src={previewUrl} alt="" /> : <span className="tile-empty">No preview</span>}
            {safetyLine ? <p className="muted publish-variant-caption">{safetyLine}</p> : null}
          </div>
          <div className="curate-modal-panel">
            <p className="muted">
              {pick.image.filename} · {lifecycleLabel(pick.image.effective_state)} · model{" "}
              {pick.image.model_state ?? "—"} · {pick.status}
            </p>
            <ul className="curate-reasons">
              {pick.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
            <label>
              Caption
              <textarea
                aria-label="Caption"
                rows={4}
                value={captionText}
                onChange={(event) => setCaptionText(event.target.value)}
                disabled={!captionId}
              />
            </label>
            {pick.captions.length > 1 ? (
              <div className="caption-pick curate-caption-alts">
                {pick.captions.map((caption) => (
                  <button
                    key={caption.id}
                    type="button"
                    className={captionId === caption.id ? "ghost active" : "ghost"}
                    onClick={() => selectCaption(caption)}
                  >
                    {caption.style} · {caption.language}
                  </button>
                ))}
              </div>
            ) : null}
            <div className="curate-tags">
              <div className="row">
                <span>Hashtags</span>
                <button
                  type="button"
                  className="ghost"
                  disabled={hashtagSuggest.isPending}
                  onClick={() => hashtagSuggest.mutate()}
                >
                  Suggest
                </button>
              </div>
              <div className="curate-tag-chips">
                {tags.map((tag) => (
                  <button
                    key={tag}
                    type="button"
                    className="curate-tag-chip"
                    onClick={() => setTags((prev) => prev.filter((row) => row !== tag))}
                    title="Remove"
                  >
                    #{tag} ×
                  </button>
                ))}
              </div>
              <div className="row">
                <input
                  value={tagInput}
                  placeholder="Add tag"
                  onChange={(event) => setTagInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      addTagFromInput();
                    }
                  }}
                />
                <button type="button" className="ghost" onClick={addTagFromInput}>
                  Add
                </button>
              </div>
            </div>
            <label className="row curate-schedule-opt">
              <input
                type="checkbox"
                checked={scheduleAfterQueue}
                onChange={(event) => onScheduleAfterQueueChange(event.target.checked)}
              />
              Add to auto-slot schedule after sending
            </label>
            {error ? <p className="error">{error}</p> : null}
            <div className="curate-modal-actions">
              <button
                type="button"
                className="button"
                disabled={busy || !isPendingCuratePick(pick)}
                onClick={() => void runQueue()}
              >
                {busy ? "Working…" : "Send to queue"}
              </button>
              <button
                type="button"
                className="button danger"
                disabled={busy || !isPendingCuratePick(pick)}
                onClick={() => void runReject()}
              >
                Reject
              </button>
              <button type="button" className="ghost" disabled={busy} onClick={runSkip}>
                Skip
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function resolveAfterCurateAction(
  allOrderedPicks: CurationPick[],
  filteredNavPicks: CurationPick[],
  previousPickId: string,
  action: CurateModalAction,
): string | null {
  if (action === "skip") {
    const next = neighborPickId(filteredNavPicks, previousPickId, 1);
    return next ?? neighborPickId(filteredNavPicks, previousPickId, -1);
  }
  const pending = allOrderedPicks.filter(isPendingCuratePick);
  const index = pending.findIndex((row) => row.id === previousPickId);
  if (index < 0) return pending[0]?.id ?? null;
  return pending[index + 1]?.id ?? null;
}
