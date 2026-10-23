import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api";
import {
  chunkGridRows,
  columnCountForWidth,
  curateTabCounts,
  filterCuratePicks,
  pickTileBadge,
  type CurateFilterTab,
} from "../curatePickUx";
import { displayThumbnailUrl } from "../publishPresentation";
import { acquireThumbSlot } from "../thumbQueue";
import type {
  AppConfig,
  CurationBatch,
  CurationBatchSummary,
  CurationPick,
  JobItem,
  LibraryItem,
} from "../types";
import { CuratePickModal, resolveAfterCurateAction, type CurateModalAction } from "./CuratePickModal";

const GRID_GAP = 8;
const ROW_HEIGHT = 148;

function CurateGridThumb({ src }: { src: string }) {
  const [activeSrc, setActiveSrc] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const releaseRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoaded(false);
    setActiveSrc(null);
    releaseRef.current?.();
    releaseRef.current = null;
    void acquireThumbSlot().then((done) => {
      if (cancelled) {
        done();
        return;
      }
      releaseRef.current = done;
      setActiveSrc(src);
    });
    return () => {
      cancelled = true;
      releaseRef.current?.();
      releaseRef.current = null;
    };
  }, [src]);

  function finish() {
    setLoaded(true);
    releaseRef.current?.();
    releaseRef.current = null;
  }

  if (!activeSrc) {
    return <span className="tile-empty tile-pending" aria-hidden />;
  }
  return (
    <img
      src={activeSrc}
      alt=""
      draggable={false}
      loading="lazy"
      decoding="async"
      className={loaded ? "thumb-loaded" : "thumb-loading"}
      onLoad={finish}
      onError={finish}
    />
  );
}

type ToastState = { message: string; undo?: () => Promise<void> };

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

  const pixelsAllowed = config.data?.gemini_pixels_allowed ?? false;
  const [libraryId, setLibraryId] = useState("");
  const labelStats = useQuery({
    queryKey: ["gemini-label-stats", libraryId],
    queryFn: () =>
      api<{ eligible: number; labeled: number; pending: number; blocked: number }>(
        `/api/gemini/label/stats${libraryId ? `?library_id=${libraryId}` : ""}`,
      ),
  });
  const [labelJobId, setLabelJobId] = useState<string | null>(null);
  const labelJob = useQuery({
    queryKey: ["gemini-label-job", labelJobId],
    queryFn: () => api<JobItem>(`/api/jobs/${labelJobId}`),
    enabled: Boolean(labelJobId),
    refetchInterval: (query) =>
      query.state.data?.status === "running" || query.state.data?.status === "queued" ? 1500 : false,
  });
  const [folder, setFolder] = useState("");
  const [allEligible, setAllEligible] = useState(true);
  const [limit, setLimit] = useState(48);
  const [includeImages, setIncludeImages] = useState(true);
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [runPanelOpen, setRunPanelOpen] = useState(false);

  useEffect(() => {
    if (config.data) {
      setIncludeImages(Boolean(config.data.gemini_pixels_allowed));
    }
  }, [config.data?.gemini_pixels_allowed]);

  useEffect(() => {
    if (activeBatchId || !batches.data?.length) return;
    setActiveBatchId(batches.data[0].id);
  }, [activeBatchId, batches.data]);

  const activeBatch = useQuery({
    queryKey: ["curation-batch", activeBatchId],
    queryFn: () => api<CurationBatch>(`/api/curation/${activeBatchId}`),
    enabled: Boolean(activeBatchId),
    refetchInterval: (query) => (query.state.data?.status === "running" ? 1500 : false),
  });

  const labelAll = useMutation({
    mutationFn: () =>
      api<{ job_id: string; status: string }>("/api/gemini/label", {
        method: "POST",
        body: JSON.stringify({
          library_id: libraryId || null,
          all_eligible: true,
        }),
      }),
    onSuccess: async (payload) => {
      setLabelJobId(payload.job_id);
      await queryClient.invalidateQueries({ queryKey: ["gemini-label-stats"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Labeling failed."),
  });

  const run = useMutation({
    mutationFn: () =>
      api<CurationBatch>("/api/curation", {
        method: "POST",
        body: JSON.stringify({
          library_id: libraryId || null,
          folder: folder.trim() || null,
          limit: allEligible ? 0 : limit,
          include_images: pixelsAllowed ? includeImages : false,
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

  const curationBusy =
    run.isPending ||
    Boolean(
      batches.data?.some(
        (batch) =>
          batch.status === "running" &&
          (!libraryId || batch.library_id === libraryId || batch.library_id == null),
      ),
    );

  return (
    <main className="page">
      <h1>Curate</h1>
      <p className="muted">
        Browse curation picks as thumbnails, tune caption and hashtags, then send to the publish queue. Nothing
        publishes until you approve on Queue.
      </p>

      <section className="panel curate-run-panel">
        <button type="button" className="ghost curate-run-toggle" onClick={() => setRunPanelOpen((open) => !open)}>
          {runPanelOpen ? "▾" : "▸"} Run Gemini curation / labeling
        </button>
        {runPanelOpen ? (
          <div className="curate-run-body">
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
              <label className="row" style={{ alignItems: "center", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={allEligible}
                  onChange={(event) => setAllEligible(event.target.checked)}
                />
                All eligible
              </label>
              {!allEligible ? (
                <label>
                  Limit
                  <input
                    type="number"
                    min={1}
                    max={2000}
                    value={limit}
                    onChange={(event) => setLimit(Number(event.target.value) || 48)}
                  />
                </label>
              ) : null}
              <label
                className="row"
                style={{ alignItems: "center", gap: 8 }}
                title={
                  !pixelsAllowed ? "Server GEMINI_IMAGE_POLICY forbids image bytes (text_only / manual_only)." : undefined
                }
              >
                <input
                  type="checkbox"
                  checked={pixelsAllowed && includeImages}
                  disabled={!pixelsAllowed}
                  onChange={(event) => setIncludeImages(event.target.checked)}
                />
                Send image previews to Gemini
              </label>
              <button className="button" type="button" disabled={curationBusy} onClick={() => run.mutate()}>
                {curationBusy ? "Curating…" : "Run Gemini curation"}
              </button>
              <button
                className="button ghost"
                type="button"
                disabled={labelAll.isPending}
                onClick={() => labelAll.mutate()}
              >
                {labelAll.isPending ? "Labeling…" : "Label all safe photos"}
              </button>
            </div>
            {labelStats.data ? (
              <p className="muted">
                Gemini labels: {labelStats.data.labeled} labeled · {labelStats.data.pending} pending ·{" "}
                {labelStats.data.blocked} blocked ({labelStats.data.eligible} eligible)
              </p>
            ) : null}
            {labelJob.data && (labelJob.data.status === "running" || labelJob.data.status === "queued") ? (
              <p className="muted">
                Labeling job {labelJob.data.status}
                {labelJob.data.files_total != null
                  ? ` — ${labelJob.data.files_processed ?? 0}/${labelJob.data.files_total}`
                  : ""}
              </p>
            ) : null}
            {run.isPending || activeBatch.data?.status === "running" ? (
              <p className="muted">{activeBatch.data?.detail ?? "Ranking in chunks…"}</p>
            ) : null}
            {!pixelsAllowed ? (
              <p className="muted">
                Image previews are disabled by GEMINI_IMAGE_POLICY={config.data?.gemini_policy ?? "text_only"}.
              </p>
            ) : null}
            {error ? <p className="error">{error}</p> : null}
          </div>
        ) : null}
      </section>

      <section className="row curate-batch-row">
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
        <CurationPicksWorkspace
          batch={activeBatch.data}
          geminiPolicy={config.data?.gemini_policy ?? "text_only"}
          onRefreshBatch={async () => {
            await queryClient.invalidateQueries({ queryKey: ["curation-batch", activeBatch.data!.id] });
            await queryClient.invalidateQueries({ queryKey: ["curation-batches"] });
            await queryClient.invalidateQueries({ queryKey: ["queue"] });
          }}
          serverPickCount={activeBatch.data.pick_count}
        />
      ) : null}
    </main>
  );
}

export function CurationPicksWorkspace({
  batch,
  geminiPolicy,
  onRefreshBatch,
  serverPickCount,
}: {
  batch: CurationBatch;
  geminiPolicy: string;
  onRefreshBatch: () => Promise<void>;
  serverPickCount: number;
}) {
  const [picks, setPicks] = useState<CurationPick[]>(() => [...batch.picks]);
  const [filterTab, setFilterTab] = useState<CurateFilterTab>("pending");
  const [modalPickId, setModalPickId] = useState<string | null>(null);
  const [modalBusy, setModalBusy] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  const parentRef = useRef<HTMLDivElement>(null);
  const scrollSnapshotRef = useRef(0);
  const [width, setWidth] = useState(960);

  const ordered = useMemo(
    () => [...picks].sort((a, b) => a.sort_order - b.sort_order),
    [picks],
  );
  const filtered = useMemo(() => filterCuratePicks(ordered, filterTab), [ordered, filterTab]);
  const counts = useMemo(() => curateTabCounts(ordered), [ordered]);
  const modalPick = modalPickId ? ordered.find((row) => row.id === modalPickId) ?? null : null;

  useEffect(() => {
    setPicks([...batch.picks]);
  }, [batch.id, batch.picks]);

  const pendingNewCount = Math.max(0, serverPickCount - picks.length);

  useEffect(() => {
    const element = parentRef.current;
    if (!element) return;
    const observer = new ResizeObserver(() => {
      const next = element.clientWidth;
      setWidth((prev) => (prev === next ? prev : next));
    });
    observer.observe(element);
    setWidth(element.clientWidth);
    return () => observer.disconnect();
  }, []);

  const columnCount = columnCountForWidth(width);
  const gridRows = useMemo(() => chunkGridRows(filtered, columnCount), [filtered, columnCount]);
  const virtualizer = useVirtualizer({
    count: gridRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT + GRID_GAP,
    overscan: 3,
  });

  useEffect(() => {
    virtualizer.measure();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gridRows, columnCount]);

  const patchPick = useCallback((updated: CurationPick) => {
    setPicks((rows) => rows.map((row) => (row.id === updated.id ? updated : row)));
  }, []);

  function openModal(pickId: string) {
    if (parentRef.current) scrollSnapshotRef.current = parentRef.current.scrollTop;
    setModalPickId(pickId);
  }

  function closeModal() {
    setModalPickId(null);
  }

  useLayoutEffect(() => {
    if (modalPickId) return;
    const element = parentRef.current;
    if (!element) return;
    element.scrollTop = scrollSnapshotRef.current;
  }, [modalPickId]);

  function handleAfterAction(action: CurateModalAction, previousPickId: string) {
    const nextId = resolveAfterCurateAction(ordered, filtered, previousPickId, action);
    if (nextId && ordered.some((row) => row.id === nextId)) {
      setModalPickId(nextId);
    } else {
      setModalPickId(null);
    }
  }

  async function applyRefresh() {
    await onRefreshBatch();
  }

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 6000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const heldBack = batch.held_back ?? 0;

  return (
    <section className="curate-workspace">
      <div className="curate-tabs row">
        {(
          [
            ["pending", "Pending"],
            ["queued", "Queued"],
            ["rejected", "Rejected"],
            ["all", "All"],
          ] as const
        ).map(([tab, label]) => (
          <button
            key={tab}
            type="button"
            className={filterTab === tab ? "button" : "ghost"}
            onClick={() => setFilterTab(tab)}
          >
            {label} ({counts[tab]})
          </button>
        ))}
        {heldBack > 0 ? <span className="muted curate-held-back">{heldBack} held back (near-duplicates)</span> : null}
      </div>
      {pendingNewCount > 0 ? (
        <div className="grid-refresh-banner" role="status">
          <span>{pendingNewCount} new pick{pendingNewCount === 1 ? "" : "s"} in this batch — </span>
          <button type="button" className="ghost" onClick={() => void applyRefresh()}>
            Refresh
          </button>
        </div>
      ) : null}
      {batch.detail ? <p className="muted">{batch.detail}</p> : null}
      {filtered.length === 0 ? (
        <p className="muted">No picks in this tab.</p>
      ) : (
        <div className="grid-scroll curate-grid-scroll" ref={parentRef}>
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((virtualRow) => {
              const row = gridRows[virtualRow.index];
              if (!row) return null;
              return (
                <div
                  key={virtualRow.key}
                  className="curate-grid-row"
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: ROW_HEIGHT,
                    transform: `translateY(${virtualRow.start}px)`,
                    gap: GRID_GAP,
                    gridTemplateColumns: `repeat(${columnCount}, minmax(0, 1fr))`,
                  }}
                >
                  {row.map((pick) => {
                    const thumb = displayThumbnailUrl(pick.image);
                    const badge = pickTileBadge(pick);
                    const selected = pick.id === modalPickId;
                    return (
                      <button
                        key={pick.id}
                        type="button"
                        className={`curate-tile${selected ? " selected" : ""}`}
                        aria-pressed={selected}
                        aria-label={`${pick.image.filename}, ${badge}`}
                        onClick={() => openModal(pick.id)}
                      >
                        {thumb ? <CurateGridThumb src={thumb} /> : <span className="tile-empty" />}
                        <span className="curate-tile-badge">{badge}</span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>
      )}
      {toast ? (
        <div className="curate-toast" role="status">
          <span>{toast.message}</span>
          {toast.undo ? (
            <button
              type="button"
              className="ghost"
              onClick={() => {
                void toast.undo?.().then(
                  () => setToast(null),
                  (err: unknown) =>
                    setToast({
                      message: err instanceof Error && err.message ? `Undo failed: ${err.message}` : "Undo failed.",
                    }),
                );
              }}
            >
              Undo
            </button>
          ) : null}
        </div>
      ) : null}
      {modalPick ? (
        <CuratePickModal
          pick={modalPick}
          orderedPicks={filtered}
          geminiPolicy={geminiPolicy}
          busy={modalBusy}
          onClose={closeModal}
          onBusyChange={setModalBusy}
          onPickPatched={patchPick}
          onNavigateToPick={setModalPickId}
          onAfterAction={handleAfterAction}
          onToast={(message, undo) => setToast({ message, undo })}
        />
      ) : null}
    </section>
  );
}
