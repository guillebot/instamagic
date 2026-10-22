import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { ApiError, api } from "../api";
import type {
  AppConfig,
  CaptionItem,
  CurationBatch,
  CurationBatchSummary,
  CurationPick,
  LibraryItem,
} from "../types";

export function CuratePage() {
  const queryClient = useQueryClient();
  const config = useQuery({ queryKey: ["config"], queryFn: () => api<AppConfig>("/api/config") });
  const libraries = useQuery({
    queryKey: ["libraries"],
    queryFn: () => api<LibraryItem[]>("/api/libraries"),
  });
  const batches = useQuery({
    queryKey: ["curation-batches"],
    queryFn: () => api<CurationBatchSummary[]>("/api/curation"),
  });

  const [libraryId, setLibraryId] = useState("");
  const [folder, setFolder] = useState("");
  const [limit, setLimit] = useState(12);
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const activeBatch = useQuery({
    queryKey: ["curation-batch", activeBatchId],
    queryFn: () => api<CurationBatch>(`/api/curation/${activeBatchId}`),
    enabled: Boolean(activeBatchId),
  });

  const run = useMutation({
    mutationFn: () =>
      api<CurationBatch>("/api/curation", {
        method: "POST",
        body: JSON.stringify({
          library_id: libraryId || null,
          folder: folder.trim() || null,
          limit,
          confirmed: true,
        }),
      }),
    onSuccess: async (batch) => {
      setError("");
      setActiveBatchId(batch.id);
      await queryClient.invalidateQueries({ queryKey: ["curation-batches"] });
      await queryClient.invalidateQueries({ queryKey: ["curation-batch", batch.id] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Curation failed."),
  });

  return (
    <main className="page">
      <h1>Curate</h1>
      <p>
        Gemini ranks SAFE / EXCELLENT frames and drafts tags + captions. Nothing is queued or published until you
        choose. Policy: {config.data?.gemini_policy ?? "…"}. Auto publish stays off.
      </p>

      <section className="panel">
        <div className="row">
          <label>
            Library
            <select value={libraryId} onChange={(event) => setLibraryId(event.target.value)}>
              <option value="">All attested</option>
              {libraries.data?.map((library) => (
                <option key={library.id} value={library.id}>
                  {library.name}
                  {library.attested ? "" : " (not attested)"}
                </option>
              ))}
            </select>
          </label>
          <label>
            Folder
            <input
              value={folder}
              onChange={(event) => setFolder(event.target.value)}
              placeholder="optional relative folder"
            />
          </label>
          <label>
            Limit
            <input
              type="number"
              min={1}
              max={24}
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value) || 12)}
            />
          </label>
          <button className="button" type="button" disabled={run.isPending} onClick={() => run.mutate()}>
            {run.isPending ? "Curating…" : "Run Gemini curation"}
          </button>
        </div>
        {error ? <p className="error">{error}</p> : null}
      </section>

      <section className="row" style={{ marginBottom: 12 }}>
        {batches.data?.map((batch) => (
          <button
            key={batch.id}
            type="button"
            className={activeBatchId === batch.id ? "button" : "ghost"}
            onClick={() => setActiveBatchId(batch.id)}
          >
            {new Date(batch.created_at).toLocaleString()} · {batch.pick_count} picks · {batch.status}
          </button>
        ))}
        {batches.data && batches.data.length === 0 ? (
          <p className="muted">No curation batches yet. Rate frames safe/excellent, then run curation.</p>
        ) : null}
      </section>

      {activeBatch.data ? (
        <CurationBatchView
          batch={activeBatch.data}
          onChanged={() => void queryClient.invalidateQueries({ queryKey: ["curation-batch", activeBatch.data.id] })}
        />
      ) : null}
    </main>
  );
}

function CurationBatchView({ batch, onChanged }: { batch: CurationBatch; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState("");

  const ordered = useMemo(
    () => [...batch.picks].sort((a, b) => a.sort_order - b.sort_order),
    [batch.picks],
  );

  async function reorder(pickIds: string[]) {
    setError("");
    try {
      await api(`/api/curation/${batch.id}/reorder`, {
        method: "PATCH",
        body: JSON.stringify({ pick_ids: pickIds }),
      });
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Reorder failed.");
    }
  }

  function move(pickId: string, direction: -1 | 1) {
    const ids = ordered.map((pick) => pick.id);
    const index = ids.indexOf(pickId);
    const next = index + direction;
    if (index < 0 || next < 0 || next >= ids.length) return;
    const copy = [...ids];
    [copy[index], copy[next]] = [copy[next], copy[index]];
    void reorder(copy);
  }

  const send = useMutation({
    mutationFn: (pickIds: string[]) =>
      api("/api/curation/queue", {
        method: "POST",
        body: JSON.stringify({ pick_ids: pickIds }),
      }),
    onSuccess: async () => {
      setError("");
      setSelected(new Set());
      onChanged();
      await queryClient.invalidateQueries({ queryKey: ["queue"] });
      await queryClient.invalidateQueries({ queryKey: ["curation-batches"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Send to queue failed."),
  });

  function toggle(pickId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(pickId)) next.delete(pickId);
      else next.add(pickId);
      return next;
    });
  }

  const selectable = ordered.filter((pick) => pick.status !== "rejected" && pick.status !== "queued");

  return (
    <section>
      <div className="row" style={{ marginBottom: 12 }}>
        <button
          className="ghost"
          type="button"
          onClick={() => setSelected(new Set(selectable.map((pick) => pick.id)))}
        >
          Select all open
        </button>
        <button className="ghost" type="button" onClick={() => setSelected(new Set())}>
          Clear
        </button>
        <button
          className="button"
          type="button"
          disabled={selected.size === 0 || send.isPending}
          onClick={() => send.mutate([...selected])}
        >
          {send.isPending ? "Sending…" : `Send ${selected.size || ""} to publish queue`}
        </button>
      </div>
      {error ? <p className="error">{error}</p> : null}
      <div className="card-list">
        {ordered.map((pick, index) => (
          <CurationPickCard
            key={pick.id}
            pick={pick}
            checked={selected.has(pick.id)}
            onToggle={() => toggle(pick.id)}
            onMoveUp={() => move(pick.id, -1)}
            onMoveDown={() => move(pick.id, 1)}
            canUp={index > 0}
            canDown={index < ordered.length - 1}
            onChanged={onChanged}
          />
        ))}
      </div>
    </section>
  );
}

function CurationPickCard({
  pick,
  checked,
  onToggle,
  onMoveUp,
  onMoveDown,
  canUp,
  canDown,
  onChanged,
}: {
  pick: CurationPick;
  checked: boolean;
  onToggle: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  canUp: boolean;
  canDown: boolean;
  onChanged: () => void;
}) {
  const [tagsText, setTagsText] = useState(pick.suggested_tags.join(", "));
  const [captionId, setCaptionId] = useState(pick.preferred_caption_id);
  const [captionText, setCaptionText] = useState(
    () => pick.captions.find((caption) => caption.id === pick.preferred_caption_id)?.text ?? pick.captions[0]?.text ?? "",
  );
  const [error, setError] = useState("");

  async function patch(body: Record<string, unknown>) {
    setError("");
    try {
      await api(`/api/curation/picks/${pick.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Update failed.");
    }
  }

  function selectCaption(caption: CaptionItem) {
    setCaptionId(caption.id);
    setCaptionText(caption.text);
    void patch({ preferred_caption_id: caption.id });
  }

  return (
    <article className="card curation-card">
      <div>
        {pick.image.thumbnail_url ? <img src={pick.image.thumbnail_url} alt="" /> : <div />}
        <label className="row" style={{ marginTop: 8 }}>
          <input
            type="checkbox"
            checked={checked}
            disabled={pick.status === "rejected" || pick.status === "queued"}
            onChange={onToggle}
          />
          Select
        </label>
      </div>
      <div className="queue-body">
        <div className="row">
          <strong>
            #{pick.sort_order} · local {pick.local_score} · Gemini #{pick.gemini_rank}
          </strong>
          <span className="muted">{pick.status}</span>
          <button className="ghost" type="button" disabled={!canUp} onClick={onMoveUp}>
            Up
          </button>
          <button className="ghost" type="button" disabled={!canDown} onClick={onMoveDown}>
            Down
          </button>
          <button className="ghost" type="button" onClick={() => void patch({ status: "accepted" })}>
            Accept
          </button>
          <button className="ghost" type="button" onClick={() => void patch({ status: "rejected" })}>
            Reject
          </button>
        </div>
        <p className="muted">
          {pick.image.filename} · {pick.image.effective_state} · model {pick.image.model_state ?? "—"}
        </p>
        <ul>
          {pick.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
        <label>
          Suggested tags
          <div className="row">
            <input value={tagsText} onChange={(event) => setTagsText(event.target.value)} style={{ flex: 1 }} />
            <button
              className="ghost"
              type="button"
              onClick={() =>
                void patch({
                  suggested_tags: tagsText
                    .split(",")
                    .map((tag) => tag.trim())
                    .filter(Boolean),
                })
              }
            >
              Save tags
            </button>
          </div>
        </label>
        <div className="caption-pick">
          {pick.captions.map((caption) => (
            <label key={caption.id} className={captionId === caption.id ? "picked" : ""}>
              <input
                type="radio"
                name={`caption-${pick.id}`}
                checked={captionId === caption.id}
                onChange={() => selectCaption(caption)}
              />
              <div>
                <div className="muted">
                  {caption.style} · {caption.language}
                </div>
                {captionId === caption.id ? (
                  <textarea
                    rows={3}
                    value={captionText}
                    onChange={(event) => setCaptionText(event.target.value)}
                    onBlur={() => {
                      if (captionText.trim() && captionText !== caption.text) {
                        void patch({ preferred_caption_id: caption.id, caption_text: captionText });
                      }
                    }}
                  />
                ) : (
                  <p>{caption.text}</p>
                )}
              </div>
            </label>
          ))}
        </div>
        {error ? <p className="error">{error}</p> : null}
      </div>
    </article>
  );
}
