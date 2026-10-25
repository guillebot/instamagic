import { StagePill } from "../photoStage";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api";
import {
  isBlurVerifiedPick,
  isCurateTypingTarget,
  isPendingCuratePick,
  neighborPickId,
} from "../curatePickUx";
import { lifecycleLabel } from "../lifecycle";
import { displayPreviewUrl, publishVariantLabel } from "../publishPresentation";
import { ActionStatus, PendingButtonLabel, useBriefStatus } from "../actionButton";
import { countNewHashtags, hashtagSuccessMessage } from "../hashtagFeedback";
import { ActionLabel, KeyHint } from "../KeyHint";
import {
  defaultStoryShareValue,
  StoryShareControls,
  type StoryShareValue,
} from "../StoryShareControls";
import type { CaptionItem, CurationPick, PostingScheduleConfig, QueueItem } from "../types";

export type CurateModalAction = "queue" | "reject" | "skip";

type Props = {
  pick: CurationPick;
  orderedPicks: CurationPick[];
  geminiPolicy: string;
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
  const { status: hashtagStatus, setStatus: setHashtagStatus, clearStatus: clearHashtagStatus } = useBriefStatus();
  const scheduleDefaults = useQuery({
    queryKey: ["posting-schedule-config"],
    queryFn: () => api<PostingScheduleConfig>("/api/posting-schedule/config"),
    staleTime: 60_000,
  });
  const [storyShare, setStoryShare] = useState<StoryShareValue>(() => defaultStoryShareValue());

  useEffect(() => {
    setCaptionId(preferredCaptionId(pick));
    setCaptionText(captionTextForPick(pick));
    setTags([...pick.suggested_tags]);
    setTagInput("");
    setError("");
    clearHashtagStatus();
    if (scheduleDefaults.data) {
      setStoryShare(
        defaultStoryShareValue({
          story_share_enabled: scheduleDefaults.data.story_share_default ?? false,
          story_render_mode: scheduleDefaults.data.story_render_mode_default ?? "blur_backdrop",
          story_border_color: scheduleDefaults.data.story_border_color_default ?? "black",
        }),
      );
    }
    dialogRef.current?.focus();
  }, [pick.id, scheduleDefaults.data, clearHashtagStatus]);

  const hashtagSuggest = useMutation({
    mutationFn: () =>
      api<{ tags: string[] }>(`/api/images/${pick.image.id}/hashtags`, {
        method: "POST",
        body: JSON.stringify({ confirmed: geminiPolicy !== "manual_only", refresh: true }),
      }),
    onMutate: () => {
      clearHashtagStatus();
      return { previousTags: tags };
    },
    onSuccess: (body, _vars, context) => {
      const merged = [...tags];
      for (const raw of body.tags ?? []) {
        const name = raw.trim().replace(/^#/, "").toLowerCase();
        if (name && !merged.includes(name)) merged.push(name);
      }
      const added = countNewHashtags(context?.previousTags ?? tags, body.tags ?? []);
      setTags(merged.slice(0, 12));
      setHashtagStatus(hashtagSuccessMessage(added));
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
    let effectiveCaptionId = captionId;
    if (!effectiveCaptionId && captionText.trim()) {
      // No generated caption (pending, or Gemini declined): save the operator's own text, no Gemini call.
      const created = await api<CaptionItem>(`/api/images/${pick.image.id}/captions/manual`, {
        method: "POST",
        body: JSON.stringify({ text: captionText.trim() }),
      });
      effectiveCaptionId = created.id;
      setCaptionId(created.id);
    }
    if (effectiveCaptionId) {
      body.preferred_caption_id = effectiveCaptionId;
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
    if (!captionText.trim()) {
      setError("Write a caption first (or wait for the background caption).");
      return;
    }
    onBusyChange(true);
    setError("");
    try {
      await saveDraft();
      // Only adds a CANDIDATE queue row; approving/scheduling/publishing stays a separate human step.
      await api<QueueItem[]>("/api/curation/queue", {
        method: "POST",
        body: JSON.stringify({ pick_ids: [pick.id], ...storyShare }),
      });
      onPickPatched({ ...pick, status: "queued", suggested_tags: tags });
      onToast("Added to queue (approve it on Queue to publish)", async () => {
        const undone = await api<CurationPick>(`/api/curation/picks/${pick.id}/undo-queue`, {
          method: "POST",
        });
        onPickPatched(undone);
      });
      onAfterAction("queue", pick.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Add to queue failed.");
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

  const prevPickId = neighborPickId(orderedPicks, pick.id, -1);
  const nextPickId = neighborPickId(orderedPicks, pick.id, 1);
  const previewUrl = displayPreviewUrl(pick.image, false);
  const geminiRefused = Boolean(pick.image.gemini_refused);
  const blurVerified = isBlurVerifiedPick(pick);
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
          <div className="curate-modal-nav">
            <button
              type="button"
              className="ghost"
              disabled={busy || !prevPickId}
              onClick={() => prevPickId && onNavigateToPick(prevPickId)}
            >
              <ActionLabel label="Prev" shortcut="←" />
            </button>
            <button
              type="button"
              className="ghost"
              disabled={busy || !nextPickId}
              onClick={() => nextPickId && onNavigateToPick(nextPickId)}
            >
              <ActionLabel label="Next" shortcut="→" />
            </button>
          </div>
          <button type="button" className="ghost" onClick={onClose}>
            <ActionLabel label="Close" shortcut="Esc" />
          </button>
        </div>
        <div className="curate-modal-body">
          <div className="curate-modal-preview">
            {previewUrl ? <img src={previewUrl} alt="" /> : <span className="tile-empty">No preview</span>}
            {blurVerified ? <span className="curate-blur-badge static">Blurred (verified)</span> : null}
            {safetyLine ? <p className="muted publish-variant-caption">{safetyLine}</p> : null}
          </div>
          <div className="curate-modal-panel">
            <p className="muted">
              <StagePill image={pick.image} /> {pick.image.filename} · {lifecycleLabel(pick.image.effective_state)} · model{" "}
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
                placeholder={captionId ? undefined : "Write the caption"}
              />
            </label>
            {!captionId ? (
              <p className="muted curate-caption-note">
                {geminiRefused
                  ? "Gemini declined this photo, so it is never sent again. Write the caption yourself."
                  : "Caption is being generated in the background from the blurred version. You can also write one now."}
              </p>
            ) : null}
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
                  disabled={hashtagSuggest.isPending || geminiRefused}
                  title={geminiRefused ? "Gemini declined this photo; add hashtags by hand." : undefined}
                  onClick={() => hashtagSuggest.mutate()}
                >
                  <PendingButtonLabel
                    pending={hashtagSuggest.isPending}
                    pendingLabel="Suggesting…"
                    label="Suggest"
                  />
                </button>
              </div>
              {hashtagStatus ? <ActionStatus message={hashtagStatus} /> : null}
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
            <StoryShareControls value={storyShare} onChange={setStoryShare} disabled={busy} compact />
            {error ? <p className="error">{error}</p> : null}
            <div className="curate-modal-actions">
              <button
                type="button"
                className="button"
                disabled={busy || !isPendingCuratePick(pick)}
                onClick={() => void runQueue()}
              >
                {busy ? "Working…" : <ActionLabel label="Add to queue" shortcut="Q" />}
              </button>
              <button
                type="button"
                className="ghost"
                disabled={busy || !isPendingCuratePick(pick)}
                onClick={() => void runReject()}
              >
                <ActionLabel label="Reject" shortcut="R" />
              </button>
              <button type="button" className="ghost" disabled={busy} onClick={runSkip}>
                <ActionLabel label="Skip" shortcut="S" />
              </button>
            </div>
          </div>
        </div>
        <p className="modal-shortcut-legend" aria-label="Keyboard shortcuts">
          <span className="legend-item">
            <KeyHint>←</KeyHint>
            <KeyHint>→</KeyHint>
            prev/next
          </span>
          <span className="legend-item">
            <KeyHint>Q</KeyHint>
            add to queue
          </span>
          <span className="legend-item">
            <KeyHint>R</KeyHint>
            reject
          </span>
          <span className="legend-item">
            <KeyHint>S</KeyHint>
            skip
          </span>
          <span className="legend-item">
            <KeyHint>Esc</KeyHint>
            close
          </span>
        </p>
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
