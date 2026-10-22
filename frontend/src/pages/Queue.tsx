import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api";
import { lifecycleLabel } from "../lifecycle";
import type { AppConfig, CaptionItem, QueueItem } from "../types";

export function QueuePage() {
  const queryClient = useQueryClient();
  const queue = useQuery({ queryKey: ["queue"], queryFn: () => api<QueueItem[]>("/api/queue") });
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

  return (
    <main className="page">
      <h1>Publishing queue</h1>
      <p>
        Last step after Review → Curate: reorder is done upstream; here you approve, edit captions, and Publish.
        Auto publish stays {config.data?.auto_publish ? "enabled (ack required at boot)" : "off"}. Instagram:{" "}
        {ig.data?.configured ? `ready (${ig.data.provider})` : "not configured"}.{" "}
        <Link to="/instagram">Connection helper</Link>.
      </p>
      <div className="card-list">
        {queue.data?.map((item) => (
          <QueueCard
            key={item.id}
            item={item}
            policy={config.data?.gemini_policy ?? "text_only"}
            instagramReady={Boolean(ig.data?.configured)}
            onApprove={() => void approve(item.id)}
            onChanged={() => void queryClient.invalidateQueries({ queryKey: ["queue"] })}
          />
        ))}
        {queue.data && queue.data.length === 0 ? <p>Nothing queued yet. Press P on a reviewed photograph.</p> : null}
      </div>
    </main>
  );
}

function QueueCard({
  item,
  policy,
  instagramReady,
  onApprove,
  onChanged,
}: {
  item: QueueItem;
  policy: string;
  instagramReady: boolean;
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

  return (
    <article className="card queue-card">
      {item.image.thumbnail_url ? <img src={item.image.thumbnail_url} alt="" /> : <div />}
      <div className="queue-body">
        <strong>{item.image.filename}</strong>
        <p>
          {item.state}
          {item.scheduled_at ? ` · scheduled ${new Date(item.scheduled_at).toLocaleString()}` : ""}
          {item.last_error ? ` · ${item.last_error}` : ""} · {lifecycleLabel(item.image.effective_state)} ·
          model {item.image.model_state}
        </p>
        <div className="row">
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
            <button
              className="button"
              disabled={publish.isPending}
              onClick={() => {
                if (!selectedCaptionId) return;
                if (!window.confirm("Publish this frame to Instagram now?")) return;
                publish.mutate(selectedCaptionId);
              }}
            >
              {publish.isPending ? "Publishing…" : "Publish to Instagram"}
            </button>
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
