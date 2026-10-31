import { StageChipBar, StagePill, type PhotoStage } from "../photoStage";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ActionStatus, PendingButtonLabel, useBriefStatus } from "../actionButton";
import { ApiError, api } from "../api";
import { countNewHashtags, hashtagSuccessMessage } from "../hashtagFeedback";
import { lifecycleLabel } from "../lifecycle";
import { publishConfirmPreviewUrl } from "../publishPresentation";
import {
  captionFirstLine,
  captionSummary,
  formatArt,
  groupActiveQueue,
  hashtagCount,
  isAutoScheduled,
  nextSlotLabel,
  readGroupCollapsed,
  type QueueGroupKey,
  queuePostTypeLabel,
  readCompactView,
  readPublishedSectionExpanded,
  splitQueueItems,
  writeCompactView,
  writeGroupCollapsed,
  writePublishedSectionExpanded,
} from "../queueGrouping";
import {
  STORY_RENDER_MODES,
  formatStoryStatus,
  storyShareFromQueueItem,
  StoryShareControls,
} from "../StoryShareControls";
import type { AppConfig, CaptionItem, CarouselSuggestion, QueueItem } from "../types";

export function QueuePage() {
  const queryClient = useQueryClient();
  const [carouselPick, setCarouselPick] = useState<string[]>([]);
  const [publishedOpen, setPublishedOpen] = useState(() => readPublishedSectionExpanded());
  const [groupCollapsed, setGroupCollapsed] = useState<Record<QueueGroupKey, boolean>>(() => ({
    scheduled: readGroupCollapsed("scheduled"),
    needsAction: readGroupCollapsed("needsAction"),
  }));
  const toggleGroup = (group: QueueGroupKey) =>
    setGroupCollapsed((prev) => {
      const next = !prev[group];
      writeGroupCollapsed(group, next);
      return { ...prev, [group]: next };
    });
  const queue = useQuery({
    queryKey: ["queue"],
    queryFn: () => api<QueueItem[]>("/api/queue"),
    // Background enrichment fills hashtags for newly queued photos; poll while any are pending.
    refetchInterval: (query) =>
      (query.state.data ?? []).some((item) => item.state !== "PUBLISHED" && hashtagsPending(item)) ? 10_000 : false,
  });
  const { active, published } = useMemo(() => splitQueueItems(queue.data ?? []), [queue.data]);
  const [stageFilter, setStageFilter] = useState("");
  const visibleActive = useMemo(
    () => (stageFilter && stageFilter !== "posted" ? active.filter((item) => item.stage === stageFilter) : active),
    [active, stageFilter],
  );
  const { scheduled, needsAction } = useMemo(() => groupActiveQueue(visibleActive), [visibleActive]);
  const stageCounts = useMemo(() => {
    const counts: Record<string, number> = { posted: published.length };
    for (const item of active) counts[item.stage ?? "candidate"] = (counts[item.stage ?? "candidate"] ?? 0) + 1;
    return counts;
  }, [active, published]);
  const queueStages: PhotoStage[] = ["to_review", "candidate", "scheduled", "posted"];
  const [compactView, setCompactView] = useState(() => readCompactView());
  /** Per-item manual override: true = collapsed, false = expanded. Absent = default. */
  const [collapsedOverride, setCollapsedOverride] = useState<Record<string, boolean>>({});
  const isCollapsed = (item: QueueItem) => collapsedOverride[item.id] ?? (compactView || isAutoScheduled(item));
  const setAllCollapsed = (collapsed: boolean) =>
    setCollapsedOverride(Object.fromEntries(active.map((item) => [item.id, collapsed])));
  const config = useQuery({ queryKey: ["config"], queryFn: () => api<AppConfig>("/api/config") });
  const ig = useQuery({
    queryKey: ["instagram-status"],
    queryFn: () =>
      api<{ configured: boolean; provider: string; auto_publish: boolean }>("/api/instagram/status"),
  });

  const [justCollapsed, setJustCollapsed] = useState<string | null>(null);
  const [focusAfter, setFocusAfter] = useState<string | null>(null);

  /** Scheduled/approved: minimise that card and move on to the next item that needs action. */
  function markScheduled(id: string) {
    setCollapsedOverride((prev) => ({ ...prev, [id]: true }));
    setJustCollapsed(id);
    setFocusAfter(id);
  }

  useEffect(() => {
    if (!focusAfter || !queue.data) return;
    const next = needsAction.find((item) => item.id !== focusAfter && !isCollapsed(item));
    setFocusAfter(null);
    // A collapsed Needs action group is the user's choice: don't scroll into it.
    if (!next || groupCollapsed.needsAction) return;
    window.requestAnimationFrame(() => {
      const el = document.getElementById(`queue-item-${next.id}`);
      el?.scrollIntoView?.({ behavior: "smooth", block: "start" });
      el?.focus?.({ preventScroll: true });
    });
  }, [focusAfter, queue.data, needsAction, groupCollapsed.needsAction]);

  async function approve(id: string) {
    try {
      await api(`/api/queue/${id}/approve`, { method: "POST" });
      // Approved without a slot still needs scheduling: keep the card open.
      setCollapsedOverride((prev) => ({ ...prev, [id]: false }));
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : "Could not approve this item.");
    }
    await queryClient.invalidateQueries({ queryKey: ["queue"] });
  }

  async function createFromSelection(kind: "story" | "reel") {
    if (carouselPick.length < 1) return;
    try {
      await api(`/api/queue/${kind}`, {
        method: "POST",
        body: JSON.stringify({ image_ids: carouselPick }),
      });
      setCarouselPick([]);
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : `Could not create the ${kind}.`);
    }
    await queryClient.invalidateQueries({ queryKey: ["queue"] });
  }

  async function createCarousel() {
    if (carouselPick.length < 2 || carouselPick.length > 10) {
      window.alert("Pick 2–10 images for a carousel.");
      return;
    }
    await api("/api/queue/carousel", {
      method: "POST",
      body: JSON.stringify({ image_ids: carouselPick }),
    });
    setCarouselPick([]);
    await queryClient.invalidateQueries({ queryKey: ["queue"] });
  }

  function toggleCarouselPick(imageId: string) {
    setCarouselPick((prev) =>
      prev.includes(imageId) ? prev.filter((id) => id !== imageId) : prev.length >= 10 ? prev : [...prev, imageId],
    );
  }

  return (
    <main className="page">
      <h1>Publishing queue</h1>
      <p>
        Last step after Review → Curate: reorder is done upstream; here you approve, edit captions, and Publish.
        Auto publish stays {config.data?.auto_publish ? "enabled (ack required at boot)" : "off"}. Instagram:{" "}
        {ig.data?.configured ? `ready (${ig.data.provider})` : "not configured"}.{" "}
        <Link to="/instagram">Connection helper</Link>.
      </p>
      <CarouselSuggestions />
      <div className="row carousel-builder">
        <button
          className="button"
          type="button"
          disabled={carouselPick.length < 2}
          onClick={() => void createCarousel()}
        >
          Queue carousel ({carouselPick.length} selected)
        </button>
        <button
          className="button"
          type="button"
          disabled={carouselPick.length < 1}
          onClick={() => void createFromSelection("reel")}
        >
          Create Reel ({carouselPick.length} selected)
        </button>
        <button
          className="button"
          type="button"
          disabled={carouselPick.length < 1}
          onClick={() => void createFromSelection("story")}
        >
          Create Story ({carouselPick.length} selected)
        </button>
        <p className="muted">
          Select images below (checkbox): carousel needs 2–10, a Reel takes 1–10 (one photo = slow zoom, several =
          crossfade slideshow), Story creates one 9:16 story per photo.
        </p>
      </div>
      <div className="row queue-view-controls">
        <button className="ghost" type="button" disabled={active.length === 0} onClick={() => setAllCollapsed(true)}>
          Collapse all
        </button>
        <button className="ghost" type="button" disabled={active.length === 0} onClick={() => setAllCollapsed(false)}>
          Expand all
        </button>
        <label className="queue-compact-toggle">
          <input
            type="checkbox"
            checked={compactView}
            onChange={(event) => {
              const next = event.target.checked;
              setCompactView(next);
              writeCompactView(next);
              setCollapsedOverride({});
            }}
          />{" "}
          Compact view
        </label>
        <span className="muted">Auto-scheduled items start collapsed; Compact view collapses everything.</span>
      </div>
      <StageChipBar
        counts={stageCounts}
        stages={queueStages}
        value={stageFilter}
        allLabel="Active"
        allCount={active.length}
        onChange={(stage) => {
          setStageFilter(stage);
          if (stage === "posted") setPublishedOpen(true);
        }}
      />
      <div className="card-list">
        {stageFilter === "posted" ? null : (
          [
            ["scheduled", "Scheduled", scheduled],
            ["needsAction", "Needs action", needsAction],
          ] as const
        ).map(([key, title, items]) => {
          if (items.length === 0) return null;
          const groupIsCollapsed = groupCollapsed[key];
          const nextSlot = key === "scheduled" ? nextSlotLabel(items) : null;
          const bodyId = `queue-group-body-${key}`;
          return (
            <section key={key} className="queue-group" aria-label={title}>
              <h2 className="queue-group-title">
                <button
                  type="button"
                  className="queue-group-toggle ghost"
                  aria-expanded={!groupIsCollapsed}
                  aria-controls={bodyId}
                  onClick={() => toggleGroup(key)}
                >
                  <span className="queue-group-chevron" aria-hidden="true">
                    {groupIsCollapsed ? "▸" : "▾"}
                  </span>
                  {title} ({items.length})
                  {nextSlot ? <span className="queue-group-next muted">next: {nextSlot}</span> : null}
                </button>
              </h2>
              {groupIsCollapsed ? null : (
                <div id={bodyId} className="queue-group-body">
                {items.map((item) => (
                  <QueueCard
                    key={item.id}
                    item={item}
                    policy={config.data?.gemini_policy ?? "text_only"}
                    instagramReady={Boolean(ig.data?.configured)}
                    carouselPick={carouselPick}
                    onToggleCarouselPick={toggleCarouselPick}
                    onApprove={() => void approve(item.id)}
                    onChanged={() => void queryClient.invalidateQueries({ queryKey: ["queue"] })}
                    onScheduled={() => markScheduled(item.id)}
                    justCollapsed={justCollapsed === item.id}
                    collapsed={isCollapsed(item)}
                    onToggleCollapsed={() =>
                      setCollapsedOverride((prev) => ({ ...prev, [item.id]: !isCollapsed(item) }))
                    }
                  />
                ))}
                </div>
              )}
            </section>
          );
        })}
        {queue.data && queue.data.length === 0 ? <p>Nothing queued yet. Press P on a reviewed photograph.</p> : null}
      </div>
      {published.length > 0 ? (
        <section className="queue-published-section">
          <button
            type="button"
            className="queue-published-toggle ghost"
            aria-expanded={publishedOpen}
            onClick={() => {
              setPublishedOpen((open) => {
                const next = !open;
                writePublishedSectionExpanded(next);
                return next;
              });
            }}
          >
            {publishedOpen ? "▾" : "▸"} Published ({published.length})
          </button>
          {publishedOpen ? (
            <ul className="queue-published-list">
              {published.map((item) => (
                <PublishedQueueRow key={item.id} item={item} />
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}
    </main>
  );
}

function PublishedQueueRow({ item }: { item: QueueItem }) {
  const isCarousel = item.media_type === "CAROUSEL" && (item.slides?.length ?? 0) > 1;
  const thumb =
    (isCarousel ? item.slides?.[0]?.image.thumbnail_url : item.image.thumbnail_url) ?? null;
  const when = item.published_at ? new Date(item.published_at).toLocaleString() : "—";
  const caption = captionFirstLine(item.published_caption);
  const label = isCarousel ? `Carousel (${item.slides?.length ?? 0})` : item.image.filename;

  return (
    <li className="queue-published-row">
      {thumb ? <img src={thumb} alt="" className="queue-published-thumb" loading="lazy" /> : <span className="queue-published-thumb empty" />}
      <div className="queue-published-body">
        <strong className="queue-published-title">{label}</strong>
        <p className="queue-published-caption">{caption}</p>
        <p className="queue-published-meta muted">
          {when}
          {item.permalink ? (
            <>
              {" · "}
              <a href={item.permalink} target="_blank" rel="noreferrer">
                Instagram
              </a>
            </>
          ) : null}
        </p>
      </div>
    </li>
  );
}

function QueueCard({
  item,
  policy,
  instagramReady,
  carouselPick,
  onToggleCarouselPick,
  onApprove,
  onChanged,
  collapsed = false,
  onToggleCollapsed,
  onScheduled,
  justCollapsed = false,
}: {
  item: QueueItem;
  policy: string;
  instagramReady: boolean;
  carouselPick: string[];
  onToggleCarouselPick: (imageId: string) => void;
  onApprove: () => void | Promise<void>;
  onChanged: () => void;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
  onScheduled?: () => void;
  justCollapsed?: boolean;
}) {
  const [selectedCaptionId, setSelectedCaptionId] = useState<string | null>(item.caption_id ?? null);
  const [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [approveBusy, setApproveBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [suggestedTags, setSuggestedTags] = useState<string[]>(item.image.enrich_hashtags ?? []);
  const storedTagsKey = (item.image.enrich_hashtags ?? []).join(" ");
  useEffect(() => {
    // Background enrichment filled (or refreshed) hashtags: show them pre-filled.
    if (storedTagsKey) setSuggestedTags(storedTagsKey.split(" "));
  }, [storedTagsKey]);
  const autoTagsPending = suggestedTags.length === 0 && hashtagsPending(item);
  const { status: hashtagStatus, setStatus: setHashtagStatus, clearStatus: clearHashtagStatus } = useBriefStatus();
  const [storyShare, setStoryShare] = useState(() => storyShareFromQueueItem(item));

  useEffect(() => {
    setStoryShare(storyShareFromQueueItem(item));
  }, [item.id, item.story_share_enabled, item.story_render_mode]);

  const captions = useQuery({
    queryKey: ["captions", item.image.id],
    queryFn: () => api<CaptionItem[]>(`/api/images/${item.image.id}/captions`),
  });

  const generate = useMutation({
    mutationFn: () =>
      api<CaptionItem[]>(`/api/images/${item.image.id}/captions`, {
        method: "POST",
        body: JSON.stringify({ confirmed: policy !== "manual_only" || confirmed }),
      }),
    onSuccess: async (rows) => {
      setError("");
      await captions.refetch();
      if (rows[0]) setSelectedCaptionId(rows[0].id);
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Caption request failed."),
  });

  const hashtags = useMutation({
    mutationFn: () =>
      api<{ tags: string[] }>(`/api/images/${item.image.id}/hashtags`, {
        method: "POST",
        body: JSON.stringify({ confirmed: true, refresh: true }),
      }),
    onMutate: () => {
      clearHashtagStatus();
      setError("");
      return { previousTags: suggestedTags };
    },
    onSuccess: (body, _vars, context) => {
      const tags = body.tags ?? [];
      setSuggestedTags(tags);
      const added = countNewHashtags(context?.previousTags ?? [], tags);
      setHashtagStatus(hashtagSuccessMessage(added));
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Hashtag request failed."),
  });

  const [pickOpen, setPickOpen] = useState(false);
  const [pickAt, setPickAt] = useState("");
  /** Approve (if still a candidate) and schedule: auto slot, or an explicit time. */
  const scheduleWith = useMutation({
    mutationFn: async ({ approveFirst, at }: { approveFirst: boolean; at: string | null }) => {
      if (approveFirst) await api(`/api/queue/${item.id}/approve`, { method: "POST" });
      const captionId = isStory ? null : selectedCaptionId;
      return api<QueueItem>(`/api/queue/${item.id}/schedule`, {
        method: "POST",
        body: JSON.stringify(
          at
            ? { mode: "explicit", scheduled_at: new Date(at).toISOString(), caption_id: captionId }
            : { mode: "auto_slot", caption_id: captionId },
        ),
      });
    },
    onMutate: () => setError(""),
    onSuccess: (body) => {
      if (body?.scheduled_at) {
        onScheduled?.();
      } else {
        setError("Approved, but no free auto-slot was found. Pick a time instead.");
        setPickOpen(true);
      }
      onChanged();
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Schedule failed.");
      onChanged();
    },
  });

  const saveStory = useMutation({
    mutationFn: () =>
      api(`/api/queue/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify(storyShare),
      }),
    onSuccess: () => onChanged(),
    onError: (err) => setError(err instanceof ApiError ? err.message : "Could not save story options."),
  });

  const publish = useMutation({
    mutationFn: (captionId: string | null) =>
      api(`/api/queue/${item.id}/publish`, {
        method: "POST",
        body: JSON.stringify(captionId ? { caption_id: captionId } : {}),
      }),
    onSuccess: () => {
      setError("");
      onChanged();
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Publish failed."),
  });

  async function saveEdit(caption: CaptionItem) {
    if (saveBusy) return;
    setSaveBusy(true);
    setError("");
    try {
      await api(`/api/captions/${caption.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "modified", text: editText }),
      });
      setEditingId(null);
      await captions.refetch();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save caption.");
    } finally {
      setSaveBusy(false);
    }
  }

  const isStory = item.media_type === "STORY";
  const isReel = item.media_type === "REELS";
  const isCarousel = item.media_type === "CAROUSEL" && (item.slides?.length ?? 0) > 1;
  const reelSlides = isReel ? (item.slides?.length ?? 1) : 0;
  const editable = item.state === "CANDIDATE" || item.state === "FAILED";

  const isCandidate = item.state === "CANDIDATE";
  const needsSchedule = isCandidate || (item.state === "APPROVED" && !item.scheduled_at);
  const needsCaption = !isStory && !selectedCaptionId;
  const scheduleBusy = scheduleWith.isPending || approveBusy;

  const canPublish =
    (item.state === "APPROVED" || item.state === "FAILED") &&
    (isStory || Boolean(selectedCaptionId)) &&
    instagramReady &&
    (!isReel || item.reel_status === "ready");

  const postType = useMutation({
    mutationFn: (mediaType: string) =>
      api(`/api/queue/${item.id}/post-type`, {
        method: "POST",
        body: JSON.stringify({ media_type: mediaType }),
      }),
    onSuccess: () => {
      setError("");
      onChanged();
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Could not change post type."),
  });

  const renderReel = useMutation({
    mutationFn: () => api(`/api/queue/${item.id}/reel-render`, { method: "POST" }),
    onSuccess: () => {
      setError("");
      onChanged();
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Reel render failed to start."),
  });

  const [lookBusy, setLookBusy] = useState(false);
  async function saveLook(mode: string) {
    setLookBusy(true);
    try {
      await api(`/api/queue/${item.id}`, { method: "PATCH", body: JSON.stringify({ story_render_mode: mode }) });
      setError("");
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change the 9:16 fit.");
    } finally {
      setLookBusy(false);
    }
  }

  const reelRendering = isReel && item.reel_status === "rendering";
  useEffect(() => {
    if (!reelRendering) return;
    const timer = window.setInterval(() => onChanged(), 3000);
    return () => window.clearInterval(timer);
  }, [reelRendering, onChanged]);

  async function moveSlide(imageId: string, direction: -1 | 1) {
    if (!item.slides?.length) return;
    const order = item.slides.map((s) => s.image_id);
    const index = order.indexOf(imageId);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= order.length) return;
    const next = [...order];
    const [row] = next.splice(index, 1);
    next.splice(target, 0, row);
    await api(`/api/queue/${item.id}/carousel-order`, {
      method: "PUT",
      body: JSON.stringify({ image_ids: next }),
    });
    onChanged();
  }

  const selectedCaption = captions.data?.find((c) => c.id === selectedCaptionId) ?? null;
  const captionText = selectedCaption?.text ?? item.published_caption ?? null;
  const title = isCarousel
    ? `Carousel (${item.slides?.length ?? 0})`
    : isReel
      ? `Reel (${reelSlides} photo${reelSlides === 1 ? "" : "s"})`
      : isStory
        ? `Story · ${item.image.filename}`
        : item.image.filename;

  if (collapsed) {
    const thumb =
      (isCarousel || isReel ? item.slides?.[0]?.image.thumbnail_url : null) ?? item.image.thumbnail_url ?? null;
    const tags = hashtagCount(captionText);
    return (
      <article
        id={`queue-item-${item.id}`}
        className={`queue-row-compact${justCollapsed ? " queue-row-just-collapsed" : ""}`}
        data-testid="queue-row-compact"
      >
        {thumb ? <img src={thumb} alt="" className="queue-compact-thumb" loading="lazy" /> : <span className="queue-compact-thumb empty" />}
        <div className="queue-compact-body">
          <div className="queue-compact-line">
            <strong className="queue-compact-title">{isCarousel || isReel ? title : item.image.filename}</strong>
            <span className="queue-badge">{queuePostTypeLabel(item)}</span>
            <StagePill image={item} />
            <span className={`queue-badge state-${item.state.toLowerCase()}`}>{item.state}</span>
            <span className="queue-compact-when">{formatArt(item.scheduled_at)}</span>
          </div>
          <p className="queue-compact-caption">
            {isStory ? "Story — no caption" : captionSummary(captionText)}
            {!isStory ? <span className="muted"> · {tags} hashtag{tags === 1 ? "" : "s"}</span> : null}
            {item.last_error ? <span className="error"> · {item.last_error}</span> : null}
          </p>
        </div>
        <button
          type="button"
          className="ghost queue-chevron"
          aria-expanded={false}
          aria-label={`Expand ${item.image.filename}`}
          onClick={onToggleCollapsed}
        >
          ▸
        </button>
      </article>
    );
  }

  return (
    <article id={`queue-item-${item.id}`} className="card queue-card" tabIndex={-1}>
      {isStory && item.story_preview_url ? (
        <figure className="publish-confirm-preview story-preview-9x16">
          <img
            src={`${item.story_preview_url}?v=${item.story_render_mode ?? ""}-${item.story_border_color ?? ""}-${item.state}`}
            alt="Story preview (9:16)"
          />
          <figcaption className="muted">Exact 1080×1920 Story uploaded on publish</figcaption>
        </figure>
      ) : isReel ? (
        <figure className="publish-confirm-preview reel-preview">
          {item.reel_status === "ready" && item.reel_video_url ? (
            <video src={item.reel_video_url} controls muted loop playsInline preload="metadata" />
          ) : (
            <div className="carousel-thumbs">
              {(item.slides?.length ? item.slides.map((slide) => slide.image) : [item.image]).map((img, idx) => (
                <div key={`${img.id}-${idx}`} className="carousel-thumb">
                  {img.thumbnail_url ? <img src={img.thumbnail_url} alt="" /> : <span />}
                </div>
              ))}
            </div>
          )}
          <figcaption className="muted">
            {item.reel_status === "ready"
              ? `Exact ${item.reel_duration_s?.toFixed(1) ?? ""}s 1080×1920 MP4 uploaded on publish`
              : "Render the reel to preview the video"}
          </figcaption>
        </figure>
      ) : isCarousel ? (
        <div className="carousel-thumbs">
          {item.slides?.map((slide) => (
            <div key={slide.id} className="carousel-thumb">
              {slide.image.thumbnail_url ? <img src={slide.image.thumbnail_url} alt="" /> : <span />}
              <div className="row">
                <button className="ghost" type="button" onClick={() => void moveSlide(slide.image_id, -1)}>
                  ↑
                </button>
                <button className="ghost" type="button" onClick={() => void moveSlide(slide.image_id, 1)}>
                  ↓
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : item.image.thumbnail_url ? (
        <img src={item.image.thumbnail_url} alt="" />
      ) : (
        <div />
      )}
      <div className="queue-body">
        <div className="queue-card-head">
          <strong>{title}</strong>
          <StagePill image={item} />
          <GrowthBadges item={item} />
          {onToggleCollapsed ? (
            <button
              type="button"
              className="ghost queue-chevron"
              aria-expanded
              aria-label={`Collapse ${item.image.filename}`}
              onClick={onToggleCollapsed}
            >
              ▾
            </button>
          ) : null}
        </div>
        <p>
          {item.state}
          {item.scheduled_at ? ` · scheduled ${formatArt(item.scheduled_at)}` : ""}
          {item.last_error ? ` · ${item.last_error}` : ""} · {lifecycleLabel(item.image.effective_state)} ·
          model {item.image.model_state}
        </p>
        {!isStory && !isReel && formatStoryStatus(item) ? <p className="muted">{formatStoryStatus(item)}</p> : null}
        {item.reel_for_item_id ? (
          <p className="muted">
            Reel version prepared for the Reel slot. Approve it to post it as a Reel in place of the photo/carousel
            (the original is kept, unscheduled).
          </p>
        ) : null}
        {item.reel_version_id && !item.scheduled_at ? (
          <p className="muted">Its Reel version took the Reel slot. Schedule this again if you want to post both.</p>
        ) : null}
        {!isStory ? <CollaboratorsField item={item} /> : null}
        {!isCarousel && editable && reelSlides <= 1 ? (
          <label className="post-type-select">
            Post type{" "}
            <select
              value={isStory ? "STORY" : isReel ? "REELS" : "FEED"}
              disabled={postType.isPending}
              onChange={(event) => postType.mutate(event.target.value)}
            >
              <option value="FEED">Feed</option>
              <option value="STORY">Story</option>
              <option value="REELS">Reel</option>
            </select>
          </label>
        ) : null}
        {(isStory || isReel) && editable ? (
          <label className="post-type-select">
            Fit to 9:16{" "}
            <select
              value={item.story_render_mode === "fit_border" ? "fit_border" : "blur_backdrop"}
              disabled={lookBusy}
              onChange={(event) => void saveLook(event.target.value)}
            >
              {STORY_RENDER_MODES.filter((mode) => mode.value !== "crop_fill").map((mode) => (
                <option key={mode.value} value={mode.value}>
                  {mode.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {isReel ? (
          <div className="reel-controls">
            {editable ? (
              <button
                type="button"
                className="button"
                disabled={renderReel.isPending || reelRendering}
                onClick={() => renderReel.mutate()}
              >
                <PendingButtonLabel
                  pending={renderReel.isPending || reelRendering}
                  pendingLabel="Rendering video…"
                  label={item.reel_status === "ready" ? "Re-render reel video" : "Render reel video"}
                />
              </button>
            ) : null}
            {item.reel_status === "failed" && item.reel_error ? <p className="error">{item.reel_error}</p> : null}
            {item.reel_status === "stale" ? (
              <p className="error">Photos, blurs or look changed since rendering — render the reel again.</p>
            ) : null}
            <p className="muted">
              Instagram&apos;s music library can&apos;t be added through the API — reels publish with a silent track.
            </p>
          </div>
        ) : null}
        <div className="row">
          {item.state === "CANDIDATE" && !isCarousel && !isStory && !isReel ? (
            <label>
              <input
                type="checkbox"
                checked={carouselPick.includes(item.image.id)}
                onChange={() => onToggleCarouselPick(item.image.id)}
              />
              Select
            </label>
          ) : null}
          {needsSchedule ? (
            <div className="queue-schedule-actions" data-testid="queue-schedule-actions">
              <button
                type="button"
                className="button"
                disabled={scheduleBusy || needsCaption}
                title={needsCaption ? "Choose a caption first" : "Next free posting slot"}
                onClick={() => scheduleWith.mutate({ approveFirst: isCandidate, at: null })}
              >
                <PendingButtonLabel
                  pending={scheduleWith.isPending && !pickOpen}
                  pendingLabel="Scheduling…"
                  label={isCandidate ? "Approve & auto-schedule" : "Add to auto-slot schedule"}
                />
              </button>
              <button
                type="button"
                className="ghost"
                aria-expanded={pickOpen}
                disabled={scheduleBusy}
                onClick={() => setPickOpen((open) => !open)}
              >
                Pick time…
              </button>
              {isCandidate ? (
                <button
                  type="button"
                  className="ghost"
                  disabled={scheduleBusy}
                  onClick={() => {
                    if (approveBusy) return;
                    setApproveBusy(true);
                    void Promise.resolve(onApprove()).finally(() => setApproveBusy(false));
                  }}
                >
                  <PendingButtonLabel pending={approveBusy} pendingLabel="Approving…" label="Approve only" />
                </button>
              ) : (
                <span className="muted">Approved — not scheduled yet.</span>
              )}
              {needsCaption ? <span className="muted">Choose a caption below to schedule.</span> : null}
              {pickOpen ? (
                <span className="queue-pick-time">
                  <input
                    type="datetime-local"
                    aria-label="Publish time (your local time, ART)"
                    value={pickAt}
                    onChange={(event) => setPickAt(event.target.value)}
                  />
                  <button
                    type="button"
                    className="button"
                    disabled={!pickAt || scheduleBusy || needsCaption}
                    onClick={() => scheduleWith.mutate({ approveFirst: isCandidate, at: pickAt })}
                  >
                    <PendingButtonLabel
                      pending={scheduleWith.isPending && pickOpen}
                      pendingLabel="Scheduling…"
                      label={isCandidate ? "Approve & schedule at this time" : "Schedule at this time"}
                    />
                  </button>
                </span>
              ) : null}
            </div>
          ) : null}
          {item.state !== "PUBLISHED" && item.state !== "PUBLISHING" && !isStory ? (
            <>
              <button
                type="button"
                className="button"
                onClick={() => generate.mutate()}
                disabled={generate.isPending}
              >
                <PendingButtonLabel
                  pending={generate.isPending}
                  pendingLabel="Generating…"
                  label="Generate captions"
                />
              </button>
              <button
                type="button"
                className="ghost"
                onClick={() => hashtags.mutate()}
                disabled={hashtags.isPending}
              >
                <PendingButtonLabel
                  pending={hashtags.isPending}
                  pendingLabel="Regenerating…"
                  label="Regenerate hashtags"
                />
              </button>
            </>
          ) : null}
          {canPublish ? (
            <>
              {!isStory && !isReel && publishConfirmPreviewUrl(item.image) ? (
                <figure className="publish-confirm-preview">
                  <img src={publishConfirmPreviewUrl(item.image) ?? ""} alt="Upload preview" />
                  <figcaption className="muted">Exact file uploaded on publish</figcaption>
                </figure>
              ) : null}
              <button
                type="button"
                className="button"
                disabled={publish.isPending}
                onClick={() => {
                  if (isStory || isReel) {
                    const ok = window.confirm(
                      isStory
                        ? "Publish this exact 9:16 Story to @foto.sch now? Stories disappear after 24 hours."
                        : "Publish this exact Reel video to @foto.sch now? Instagram may take a few minutes to process it.",
                    );
                    if (!ok) return;
                    publish.mutate(isStory ? null : selectedCaptionId);
                    return;
                  }
                  if (!selectedCaptionId) return;
                  const previewUrl = publishConfirmPreviewUrl(item.image);
                  const storyLine = storyShare.story_share_enabled
                    ? " This will also publish an Instagram Story."
                    : "";
                  const ok = window.confirm(
                    previewUrl
                      ? `Publish this exact preview to Instagram now?${storyLine}`
                      : `Publish this frame to Instagram now?${storyLine}`,
                  );
                  if (!ok) return;
                  publish.mutate(selectedCaptionId);
                }}
              >
                <PendingButtonLabel
                  pending={publish.isPending}
                  pendingLabel="Publishing…"
                  label={isStory ? "Publish Story" : isReel ? "Publish Reel" : "Publish to Instagram"}
                />
              </button>
            </>
          ) : null}
          {item.scheduled_at ? (
            <Link className="ghost" to="/queue/schedule">
              Edit on schedule
            </Link>
          ) : null}
        </div>
        {!isCarousel && !isStory && !isReel && item.state !== "PUBLISHED" && item.state !== "PUBLISHING" ? (
          <>
            <StoryShareControls
              value={storyShare}
              onChange={setStoryShare}
              disabled={saveStory.isPending}
            />
            <button
              type="button"
              className="ghost"
              disabled={saveStory.isPending}
              onClick={() => saveStory.mutate()}
            >
              Save story options
            </button>
          </>
        ) : null}
        {policy === "manual_only" ? (
          <label>
            <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> I
            confirm this Gemini request.
          </label>
        ) : null}
        {error ? <p className="error">{error}</p> : null}
        {hashtagStatus ? <ActionStatus message={hashtagStatus} /> : null}
        {!isStory ? (
          suggestedTags.length ? (
            <p className="muted queue-hashtags">
              Hashtags (auto, appended on publish): #{suggestedTags.join(" #")}
            </p>
          ) : autoTagsPending ? (
            <p className="muted queue-hashtags" role="status">
              <span className="spinner" aria-hidden="true" /> Generating hashtags…
            </p>
          ) : item.image.gemini_refused ? (
            <p className="muted queue-hashtags">
              No hashtags: Gemini declined this photo. Try Regenerate hashtags or add tags to the caption.
            </p>
          ) : null
        ) : null}
        {isStory ? <p className="muted">Stories publish without a caption (Graph API: no stickers or links).</p> : null}
        <div className="caption-pick" hidden={isStory}>
          {captions.data?.map((caption) => (
            <label key={caption.id} className={selectedCaptionId === caption.id ? "picked" : ""}>
              <input
                type="radio"
                name={`caption-${item.id}`}
                checked={selectedCaptionId === caption.id}
                onChange={() => setSelectedCaptionId(caption.id)}
              />
              <span>
                <strong>
                  {caption.style} · {caption.language}
                </strong>
                {editingId === caption.id ? (
                  <textarea value={editText} onChange={(event) => setEditText(event.target.value)} rows={3} />
                ) : (
                  <p>{caption.text}</p>
                )}
                <span className="row">
                  {editingId === caption.id ? (
                    <button
                      className="ghost"
                      type="button"
                      disabled={saveBusy}
                      onClick={() => void saveEdit(caption)}
                    >
                      <PendingButtonLabel pending={saveBusy} pendingLabel="Saving…" label="Save" />
                    </button>
                  ) : (
                    <button
                      className="ghost"
                      type="button"
                      onClick={() => {
                        setEditingId(caption.id);
                        setEditText(caption.text);
                      }}
                    >
                      Edit
                    </button>
                  )}
                </span>
              </span>
            </label>
          ))}
        </div>
        {!instagramReady && (item.state === "APPROVED" || item.state === "FAILED") ? (
          <p className="muted">Configure Instagram Graph credentials to enable Publish.</p>
        ) : null}
      </div>
    </article>
  );
}

/** Hashtags not stored yet and background enrichment can still produce them. */
function hashtagsPending(item: QueueItem): boolean {
  if (item.media_type === "STORY") return false;
  if ((item.image.enrich_hashtags ?? []).length > 0) return false;
  if (item.image.gemini_refused) return false;
  return item.image.human_rating === "safe" || item.image.human_rating === "excellent";
}

export function GrowthBadges({ item }: { item: QueueItem }) {
  const isReel = item.media_type === "REELS";
  return (
    <>
      {isReel ? <span className="badge badge-reel">Reel</span> : null}
      {!isReel && item.is_reel_slot && item.state !== "PUBLISHED" ? (
        <span className="badge" title="This time is the daily Reel slot. A Reel version is prepared for you to approve.">
          Reel slot
        </span>
      ) : null}
      {item.content_mix ? (
        <span className="badge muted" title="Local content-mix class used for ranking and the 1-in-5 skin/body cap">
          {item.content_mix}
        </span>
      ) : null}
      {(item.collaborators?.length ?? 0) > 0 ? (
        <span className="badge" title="Instagram collaborators (they must accept the invite)">
          with {item.collaborators!.map((handle) => `@${handle}`).join(", ")}
        </span>
      ) : null}
    </>
  );
}

export function CollaboratorsField({ item }: { item: QueueItem }) {
  const queryClient = useQueryClient();
  const initial = (item.collaborators ?? []).map((handle) => `@${handle}`).join(" ");
  const [text, setText] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setText(initial), [initial]);
  const locked = item.collaborators_allowed === false || item.state === "PUBLISHING" || item.state === "PUBLISHED";
  const save = useMutation({
    mutationFn: () =>
      api<QueueItem>(`/api/queue/${item.id}/collaborators`, {
        method: "PUT",
        body: JSON.stringify({
          usernames: text
            .split(/[\s,]+/)
            .map((handle) => handle.replace(/^@/, "").trim())
            .filter(Boolean),
        }),
      }),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["queue"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Could not save collaborators."),
  });
  if (locked && !(item.collaborators?.length ?? 0)) return null;
  return (
    <div className="collaborators-field">
      <label>
        Collaborators (model&apos;s Instagram, max 3){" "}
        <input
          value={text}
          placeholder="@model_handle"
          disabled={locked || save.isPending}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      {!locked ? (
        <button className="ghost" type="button" disabled={save.isPending || text === initial} onClick={() => save.mutate()}>
          Save collaborators
        </button>
      ) : null}
      {error ? <p className="error">{error}</p> : null}
      <p className="muted">
        Saved for the whole shoot and filled in for its other photos. The post goes out on its own; it appears on their
        profile only after they accept the invite (public accounts only).
      </p>
    </div>
  );
}

export function CarouselSuggestions() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const suggestions = useQuery({
    queryKey: ["carousel-suggestions"],
    queryFn: () => api<{ suggestions: CarouselSuggestion[] }>("/api/carousel-suggestions"),
    enabled: open,
  });
  const [edits, setEdits] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  function order(group: CarouselSuggestion): string[] {
    return edits[group.shoot_key] ?? group.images.map((image) => image.id);
  }
  function update(group: CarouselSuggestion, ids: string[]) {
    setEdits((prev) => ({ ...prev, [group.shoot_key]: ids }));
  }
  function move(group: CarouselSuggestion, id: string, direction: -1 | 1) {
    const ids = [...order(group)];
    const at = ids.indexOf(id);
    const to = at + direction;
    if (at < 0 || to < 0 || to >= ids.length) return;
    [ids[at], ids[to]] = [ids[to], ids[at]];
    update(group, ids);
  }
  async function create(group: CarouselSuggestion, kind: "carousel" | "reel") {
    const ids = order(group);
    if (kind === "carousel" && ids.length < 2) return;
    setBusy(`${group.shoot_key}:${kind}`);
    setMessage(null);
    try {
      await api(`/api/queue/${kind}`, { method: "POST", body: JSON.stringify({ image_ids: ids }) });
      setMessage(
        kind === "carousel"
          ? `Carousel from ${group.shoot} added to the queue. Approve and schedule it below.`
          : `Reel slideshow from ${group.shoot} added. Render it, then approve.`,
      );
      setEdits((prev) => ({ ...prev, [group.shoot_key]: [] }));
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Could not create it.");
    }
    setBusy(null);
    await queryClient.invalidateQueries({ queryKey: ["queue"] });
    await queryClient.invalidateQueries({ queryKey: ["carousel-suggestions"] });
  }

  return (
    <section className="card carousel-suggestions">
      <div className="row">
        <h2>Carousel builder</h2>
        <button className="ghost" type="button" onClick={() => setOpen((value) => !value)}>
          {open ? "Hide suggestions" : "Suggest carousels from the same shoot"}
        </button>
      </div>
      {open ? (
        <>
          <p className="muted">
            3–5 photos from one shoot, with a strong cover first. Every photo is SAFE or EXCELLENT, or uses its verified
            blurred render. Skin/body shots are kept as slides, not covers.
          </p>
          {suggestions.isLoading ? <p className="muted">Finding shoots…</p> : null}
          {suggestions.data?.suggestions.length === 0 ? <p className="muted">No same-shoot sets ready right now.</p> : null}
          {message ? <p className="muted">{message}</p> : null}
          {suggestions.data?.suggestions.map((group) => {
            const ids = order(group);
            if (ids.length === 0) return null;
            const byId = new Map(group.images.map((image) => [image.id, image]));
            return (
              <div key={group.shoot_key} className="carousel-suggestion">
                <strong>{group.shoot}</strong>
                {group.reasons.length ? <span className="muted"> · {group.reasons.join(" · ")}</span> : null}
                <div className="carousel-thumbs">
                  {ids.map((id, idx) => {
                    const image = byId.get(id);
                    if (!image) return null;
                    return (
                      <div key={id} className="carousel-thumb">
                        {image.thumbnail_url ? <img src={image.thumbnail_url} alt={image.filename} /> : <span />}
                        <span className="muted">{idx === 0 ? "Cover" : `Slide ${idx + 1}`}</span>
                        <div className="row">
                          <button className="ghost" type="button" aria-label="Move left" onClick={() => move(group, id, -1)}>
                            ←
                          </button>
                          <button className="ghost" type="button" aria-label="Move right" onClick={() => move(group, id, 1)}>
                            →
                          </button>
                          <button
                            className="ghost"
                            type="button"
                            aria-label={`Remove ${image.filename}`}
                            onClick={() => update(group, ids.filter((other) => other !== id))}
                          >
                            ✕
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="row">
                  <button
                    className="button"
                    type="button"
                    disabled={busy !== null || ids.length < 2}
                    onClick={() => void create(group, "carousel")}
                  >
                    Create carousel ({ids.length})
                  </button>
                  <button className="ghost" type="button" disabled={busy !== null} onClick={() => void create(group, "reel")}>
                    Make Reel slideshow
                  </button>
                </div>
              </div>
            );
          })}
        </>
      ) : null}
    </section>
  );
}
