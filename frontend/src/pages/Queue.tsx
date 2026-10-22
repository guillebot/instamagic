import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api";
import { lifecycleLabel } from "../lifecycle";
import { publishConfirmPreviewUrl } from "../publishPresentation";
import {
  captionFirstLine,
  readPublishedSectionExpanded,
  splitQueueItems,
  writePublishedSectionExpanded,
} from "../queueGrouping";
import type { AppConfig, CaptionItem, QueueItem } from "../types";

export function QueuePage() {
  const queryClient = useQueryClient();
  const [carouselPick, setCarouselPick] = useState<string[]>([]);
  const [publishedOpen, setPublishedOpen] = useState(() => readPublishedSectionExpanded());
  const queue = useQuery({ queryKey: ["queue"], queryFn: () => api<QueueItem[]>("/api/queue") });
  const { active, published } = useMemo(() => splitQueueItems(queue.data ?? []), [queue.data]);
  const config = useQuery({ queryKey: ["config"], queryFn: () => api<AppConfig>("/api/config") });
  const ig = useQuery({
    queryKey: ["instagram-status"],
    queryFn: () =>
      api<{ configured: boolean; provider: string; auto_publish: boolean }>("/api/instagram/status"),
  });

  async function approve(id: string) {
    await api(`/api/queue/${id}/approve`, { method: "POST" });
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
      <div className="row carousel-builder">
        <button
          className="button"
          type="button"
          disabled={carouselPick.length < 2}
          onClick={() => void createCarousel()}
        >
          Queue carousel ({carouselPick.length} selected)
        </button>
        <p className="muted">Select 2–10 approved-ready images below (checkbox), reorder after queueing.</p>
      </div>
      <div className="card-list">
        {active.map((item) => (
          <QueueCard
            key={item.id}
            item={item}
            policy={config.data?.gemini_policy ?? "text_only"}
            instagramReady={Boolean(ig.data?.configured)}
            carouselPick={carouselPick}
            onToggleCarouselPick={toggleCarouselPick}
            onApprove={() => void approve(item.id)}
            onChanged={() => void queryClient.invalidateQueries({ queryKey: ["queue"] })}
          />
        ))}
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
}: {
  item: QueueItem;
  policy: string;
  instagramReady: boolean;
  carouselPick: string[];
  onToggleCarouselPick: (imageId: string) => void;
  onApprove: () => void;
  onChanged: () => void;
}) {
  const [selectedCaptionId, setSelectedCaptionId] = useState<string | null>(item.caption_id ?? null);
  const [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");

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
        body: JSON.stringify({ confirmed: true }),
      }),
    onError: (err) => setError(err instanceof ApiError ? err.message : "Hashtag request failed."),
  });

  const scheduleAuto = useMutation({
    mutationFn: () =>
      api(`/api/queue/${item.id}/schedule`, {
        method: "POST",
        body: JSON.stringify({
          mode: "auto_slot",
          caption_id: selectedCaptionId,
        }),
      }),
    onSuccess: () => onChanged(),
    onError: (err) => setError(err instanceof ApiError ? err.message : "Schedule failed."),
  });

  const publish = useMutation({
    mutationFn: (captionId: string) =>
      api(`/api/queue/${item.id}/publish`, {
        method: "POST",
        body: JSON.stringify({ caption_id: captionId }),
      }),
    onSuccess: () => {
      setError("");
      onChanged();
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Publish failed."),
  });

  async function saveEdit(caption: CaptionItem) {
    await api(`/api/captions/${caption.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "modified", text: editText }),
    });
    setEditingId(null);
    await captions.refetch();
  }

  const canPublish =
    (item.state === "APPROVED" || item.state === "FAILED") && Boolean(selectedCaptionId) && instagramReady;

  const isCarousel = item.media_type === "CAROUSEL" && (item.slides?.length ?? 0) > 1;

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

  return (
    <article className="card queue-card">
      {isCarousel ? (
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
        <strong>
          {isCarousel ? `Carousel (${item.slides?.length ?? 0})` : item.image.filename}
        </strong>
        <p>
          {item.state}
          {item.scheduled_at ? ` · scheduled ${new Date(item.scheduled_at).toLocaleString()}` : ""}
          {item.last_error ? ` · ${item.last_error}` : ""} · {lifecycleLabel(item.image.effective_state)} ·
          model {item.image.model_state}
        </p>
        <div className="row">
          {item.state === "CANDIDATE" && item.media_type !== "CAROUSEL" ? (
            <label>
              <input
                type="checkbox"
                checked={carouselPick.includes(item.image.id)}
                onChange={() => onToggleCarouselPick(item.image.id)}
              />
              Add to new carousel
            </label>
          ) : null}
          {item.state === "CANDIDATE" ? (
            <button className="button" onClick={onApprove}>
              Approve for publish
            </button>
          ) : null}
          {item.state !== "PUBLISHED" && item.state !== "PUBLISHING" ? (
            <>
              <button className="button" onClick={() => generate.mutate()} disabled={generate.isPending}>
                {generate.isPending ? "Generating…" : "Generate captions"}
              </button>
              <button className="ghost" onClick={() => hashtags.mutate()} disabled={hashtags.isPending}>
                Suggest hashtags
              </button>
            </>
          ) : null}
          {canPublish ? (
            <>
              {publishConfirmPreviewUrl(item.image) ? (
                <figure className="publish-confirm-preview">
                  <img src={publishConfirmPreviewUrl(item.image) ?? ""} alt="Upload preview" />
                  <figcaption className="muted">Exact file uploaded on publish</figcaption>
                </figure>
              ) : null}
              <button
                className="button"
                disabled={publish.isPending}
                onClick={() => {
                  if (!selectedCaptionId) return;
                  const previewUrl = publishConfirmPreviewUrl(item.image);
                  const ok = window.confirm(
                    previewUrl
                      ? "Publish this exact preview to Instagram now?"
                      : "Publish this frame to Instagram now?",
                  );
                  if (!ok) return;
                  publish.mutate(selectedCaptionId);
                }}
              >
                {publish.isPending ? "Publishing…" : "Publish to Instagram"}
              </button>
            </>
          ) : null}
          {item.state === "APPROVED" && selectedCaptionId ? (
            <button
              className="ghost"
              type="button"
              disabled={scheduleAuto.isPending}
              onClick={() => scheduleAuto.mutate()}
            >
              Add to auto-slot schedule
            </button>
          ) : null}
          {item.scheduled_at ? (
            <Link className="ghost" to="/queue/schedule">
              Edit on schedule
            </Link>
          ) : null}
        </div>
        {policy === "manual_only" ? (
          <label>
            <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> I
            confirm this Gemini request.
          </label>
        ) : null}
        {error ? <p className="error">{error}</p> : null}
        {hashtags.data?.tags?.length ? (
          <p className="muted">#{hashtags.data.tags.map((tag) => tag).join(" #")}</p>
        ) : null}
        <div className="caption-pick">
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
                    <button className="ghost" type="button" onClick={() => void saveEdit(caption)}>
                      Save
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
