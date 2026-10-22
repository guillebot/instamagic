import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  FormEvent,
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, api } from "../api";
import {
  clampCrop,
  containedLayout,
  cropToCss,
  moveCrop,
  parseAspectRatio,
  resizeCropAspect,
  type ContainedLayout,
  type CropRect,
} from "../cropGeometry";
import { acquireThumbSlot } from "../thumbQueue";
import type {
  AppConfig,
  BlurItem,
  CaptionItem,
  CropItem,
  FolderListing,
  ImageItem,
  JobItem,
  LibraryItem,
  QueuesStatus,
} from "../types";

/** Contact-sheet thumb: gated concurrency + lazy decode; aspect comes from DB, not naturalWidth. */
function GridThumb({ src }: { src: string }) {
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
      decoding="async"
      className={loaded ? "thumb-loaded" : "thumb-loading"}
      onLoad={finish}
      onError={finish}
    />
  );
}

type Page = { items: ImageItem[]; next_cursor: string | null };
type Filters = {
  model_state: string;
  human_rating: string;
  unreviewed: boolean;
  session_name: string;
  camera: string;
  orientation: string;
  queued: string;
  posted: string;
  date_from: string;
  date_to: string;
};

const EMPTY_FILTERS: Filters = {
  model_state: "",
  human_rating: "",
  unreviewed: false,
  session_name: "",
  camera: "",
  orientation: "",
  queued: "",
  posted: "",
  date_from: "",
  date_to: "",
};

const RATINGS: Record<string, string> = { "1": "reject", "2": "review", "3": "safe", "4": "excellent" };
const RATING_KEYS = [
  { key: "1", rating: "reject", label: "Reject" },
  { key: "2", rating: "review", label: "Review" },
  { key: "3", rating: "safe", label: "Safe" },
  { key: "4", rating: "excellent", label: "Excellent" },
] as const;
const GRID_GAP = 8;
const TARGET_ROW_HEIGHT = 160;

type JustifiedRow = {
  startIndex: number;
  items: ImageItem[];
  height: number;
};

function imageAspect(image: ImageItem): number {
  if (image.width && image.height && image.height > 0) return image.width / image.height;
  if (image.orientation === "portrait") return 3 / 4;
  if (image.orientation === "landscape") return 4 / 3;
  return 1;
}

/** Pack images into same-height rows; tile width follows aspect so the full photo is visible. */
function buildJustifiedRows(items: ImageItem[], containerWidth: number, rowHeight: number, gap: number): JustifiedRow[] {
  if (containerWidth <= 0 || items.length === 0) return [];
  const rows: JustifiedRow[] = [];
  let index = 0;
  while (index < items.length) {
    const startIndex = index;
    const rowItems: ImageItem[] = [];
    let aspectSum = 0;
    while (index < items.length) {
      const aspect = imageAspect(items[index]);
      const nextCount = rowItems.length + 1;
      const widthNeeded = (aspectSum + aspect) * rowHeight + gap * (nextCount - 1);
      if (rowItems.length > 0 && widthNeeded > containerWidth) break;
      rowItems.push(items[index]);
      aspectSum += aspect;
      index += 1;
      if (rowItems.length >= 14) break;
    }
    const gaps = gap * Math.max(0, rowItems.length - 1);
    const isLast = index >= items.length;
    const scale = aspectSum > 0 ? (containerWidth - gaps) / (aspectSum * rowHeight) : 1;
    const height = Math.max(
      96,
      Math.round(rowHeight * (isLast ? Math.min(scale, 1) : scale)),
    );
    rows.push({ startIndex, items: rowItems, height });
  }
  return rows;
}

function rowIndexForItem(rows: JustifiedRow[], itemIndex: number): number {
  return rows.findIndex(
    (row) => itemIndex >= row.startIndex && itemIndex < row.startIndex + row.items.length,
  );
}

function neighborIndex(rows: JustifiedRow[], itemIndex: number, direction: -1 | 1): number {
  const rowIdx = rowIndexForItem(rows, itemIndex);
  if (rowIdx < 0) return itemIndex;
  const row = rows[rowIdx];
  const col = itemIndex - row.startIndex;
  const nextRow = rows[rowIdx + direction];
  if (!nextRow) return itemIndex;
  return nextRow.startIndex + Math.min(col, nextRow.items.length - 1);
}

export function ReviewPage() {
  const queryClient = useQueryClient();
  const libraries = useQuery({ queryKey: ["libraries"], queryFn: () => api<LibraryItem[]>("/api/libraries") });
  const config = useQuery({ queryKey: ["config"], queryFn: () => api<AppConfig>("/api/config") });
  const [libraryId, setLibraryId] = useState("");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [items, setItems] = useState<ImageItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [multiIds, setMultiIds] = useState<Set<string>>(() => new Set());
  const [preview, setPreview] = useState(false);
  const [ratingBusy, setRatingBusy] = useState(false);
  const [cropsOpen, setCropsOpen] = useState(false);
  const [blurOpen, setBlurOpen] = useState(false);
  const [captionsOpen, setCaptionsOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [folderRefresh, setFolderRefresh] = useState(0);
  const [searchParams, setSearchParams] = useSearchParams();
  const folder = searchParams.get("folder") ?? "";
  const recursive = searchParams.get("recursive") === "1";
  const parentRef = useRef<HTMLDivElement>(null);
  const classifyDialogRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(960);

  function openFolder(next: string) {
    setSearchParams(
      (current) => {
        const params = new URLSearchParams(current);
        if (next) params.set("folder", next);
        else params.delete("folder");
        return params;
      },
      { replace: false },
    );
  }

  function setRecursive(next: boolean) {
    setSearchParams(
      (current) => {
        const params = new URLSearchParams(current);
        if (next) params.set("recursive", "1");
        else params.delete("recursive");
        return params;
      },
      { replace: true },
    );
  }

  useEffect(() => {
    if (!libraryId && libraries.data && libraries.data.length > 0) {
      setLibraryId(libraries.data[0].id);
    }
  }, [libraries.data, libraryId]);

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    if (libraryId) params.set("library_id", libraryId);
    params.set("folder", folder);
    if (recursive) params.set("recursive", "true");
    Object.entries(filters).forEach(([key, value]) => {
      if (typeof value === "boolean") {
        if (value) params.set(key, "true");
      } else if (value) {
        params.set(key, value);
      }
    });
    return params.toString();
  }, [filters, libraryId, folder, recursive]);

  async function load(reset: boolean, keepSelection = false) {
    const params = new URLSearchParams(queryString);
    if (!reset && cursor) params.set("cursor", cursor);
    const page = await api<Page>(`/api/images?${params.toString()}`);
    setItems((current) => (reset ? page.items : [...current, ...page.items]));
    setCursor(page.next_cursor);
    if (reset && !keepSelection) {
      setSelected(0);
      setMultiIds(new Set());
    }
  }

  useEffect(() => {
    if (!libraryId) return;
    void load(true).catch((err: unknown) => setMessage(err instanceof Error ? err.message : "Could not load images."));
    // cursor is intentionally read only for appends
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryString, libraryId]);

  useEffect(() => {
    const element = parentRef.current;
    if (!element) return;
    const observer = new ResizeObserver(() => {
      const next = element.clientWidth;
      setWidth((prev) => (prev === next ? prev : next));
    });
    observer.observe(element);
    setWidth((prev) => (prev === element.clientWidth ? prev : element.clientWidth));
    return () => observer.disconnect();
  }, [items.length]);

  const justifiedRows = useMemo(
    () => buildJustifiedRows(items, width, TARGET_ROW_HEIGHT, GRID_GAP),
    [items, width],
  );
  const virtualizer = useVirtualizer({
    count: justifiedRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (index) => (justifiedRows[index]?.height ?? TARGET_ROW_HEIGHT) + GRID_GAP,
    overscan: 2,
  });

  useEffect(() => {
    virtualizer.measure();
    // Only remeasure when row geometry changes — not on every virtualizer identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [justifiedRows]);

  useEffect(() => {
    if (items.length === 0) return;
    if (selected >= items.length) setSelected(items.length - 1);
  }, [items.length, selected]);

  useEffect(() => {
    if (!items.length || preview) return;
    const rowIndex = rowIndexForItem(justifiedRows, selected);
    if (rowIndex < 0) return;
    virtualizer.scrollToIndex(rowIndex, { align: "auto" });
    // Scroll only when keyboard/click selection moves — not when row packing recomputes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, preview]);

  const loadingMore = useRef(false);
  useEffect(() => {
    const el = parentRef.current;
    if (!el || !cursor) return;
    function onScroll() {
      if (!el || !cursor || loadingMore.current) return;
      const remaining = el.scrollHeight - el.scrollTop - el.clientHeight;
      if (remaining > 600) return;
      loadingMore.current = true;
      void load(false)
        .catch((err: unknown) => setMessage(err instanceof Error ? err.message : "Could not load more."))
        .finally(() => {
          loadingMore.current = false;
        });
    }
    el.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => el.removeEventListener("scroll", onScroll);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursor, items.length]);

  const current = items[selected];
  const multiActive = multiIds.size > 0;

  const classifyIndices = useMemo(() => {
    if (!multiActive) return items.map((_, index) => index);
    return items.reduce<number[]>((acc, item, index) => {
      if (multiIds.has(item.id)) acc.push(index);
      return acc;
    }, []);
  }, [items, multiActive, multiIds]);

  const applyRating = useCallback(async (imageId: string, rating: string) => {
    const updated = await api<ImageItem>(`/api/images/${imageId}/rating`, {
      method: "POST",
      body: JSON.stringify({ rating }),
    });
    setItems((rows) => rows.map((row) => (row.id === updated.id ? updated : row)));
    return updated;
  }, []);

  async function rate(rating: string) {
    if (!current || ratingBusy) return;
    setRatingBusy(true);
    try {
      await applyRating(current.id, rating);
    } finally {
      setRatingBusy(false);
    }
  }

  function advanceInClassifySet() {
    const pos = classifyIndices.indexOf(selected);
    if (pos < 0) return;
    if (pos < classifyIndices.length - 1) {
      setSelected(classifyIndices[pos + 1]);
      setMessage("");
      return;
    }
    setMessage(
      multiActive
        ? "Last selected image rated — Esc to close."
        : "Last image in the list rated — Esc to close.",
    );
  }

  async function rateAndAdvance(rating: string) {
    if (!current || ratingBusy) return;
    setRatingBusy(true);
    try {
      await applyRating(current.id, rating);
      advanceInClassifySet();
    } finally {
      setRatingBusy(false);
    }
  }

  function moveInClassifySet(delta: number) {
    const pos = classifyIndices.indexOf(selected);
    if (pos < 0) return;
    const next = classifyIndices[pos + delta];
    if (next !== undefined) setSelected(next);
  }

  function openClassify(atIndex?: number) {
    const index = atIndex ?? selected;
    if (atIndex !== undefined) setSelected(atIndex);
    if (multiIds.size > 0) {
      const image = items[index];
      if (!image || !multiIds.has(image.id)) {
        const first = items.findIndex((item) => multiIds.has(item.id));
        if (first >= 0) setSelected(first);
      }
    }
    setMessage("");
    setPreview(true);
  }

  function selectTile(index: number, event: ReactMouseEvent) {
    const image = items[index];
    if (!image) return;
    if (event.metaKey || event.ctrlKey) {
      setSelected(index);
      setMultiIds((currentIds) => {
        const next = new Set(currentIds);
        if (next.has(image.id)) next.delete(image.id);
        else next.add(image.id);
        return next;
      });
      return;
    }
    if (event.shiftKey) {
      const from = selected;
      const lo = Math.min(from, index);
      const hi = Math.max(from, index);
      setMultiIds(new Set(items.slice(lo, hi + 1).map((item) => item.id)));
      setSelected(index);
      return;
    }
    setSelected(index);
    setMultiIds(new Set());
  }

  async function queueCurrent() {
    if (!current) return;
    try {
      await api(`/api/queue`, { method: "POST", body: JSON.stringify({ image_id: current.id }) });
      setMessage("Added to the publishing queue.");
      setItems((rows) => rows.map((row) => (row.id === current.id ? { ...row, queued: true, queue_state: "CANDIDATE" } : row)));
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Could not queue this image.");
    }
  }

  useEffect(() => {
    if (!preview || !classifyDialogRef.current) return;
    const dialog = classifyDialogRef.current;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    dialog.focus();

    function onFocusIn(event: FocusEvent) {
      if (!dialog.contains(event.target as Node)) {
        event.preventDefault();
        dialog.focus();
      }
    }

    function onTab(event: KeyboardEvent) {
      if (event.key !== "Tab") return;
      const focusable = dialog.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("focusin", onFocusIn);
    dialog.addEventListener("keydown", onTab);
    return () => {
      document.removeEventListener("focusin", onFocusIn);
      dialog.removeEventListener("keydown", onTab);
      // preventScroll avoids grid scrollbar thrash (width oscillation → remount flicker).
      if (previouslyFocused && document.contains(previouslyFocused)) {
        previouslyFocused.focus({ preventScroll: true });
      }
    };
  }, [preview, current?.id]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.key === "Escape") {
        setPreview(false);
        setCropsOpen(false);
        setBlurOpen(false);
        setCaptionsOpen(false);
        return;
      }
      if (preview) {
        if (RATINGS[event.key]) {
          event.preventDefault();
          void rateAndAdvance(RATINGS[event.key]);
          return;
        }
        if (event.key === "ArrowRight") {
          event.preventDefault();
          moveInClassifySet(1);
        }
        if (event.key === "ArrowLeft") {
          event.preventDefault();
          moveInClassifySet(-1);
        }
        return;
      }
      if (cropsOpen || blurOpen || captionsOpen) return;
      if (RATINGS[event.key]) {
        event.preventDefault();
        void rate(RATINGS[event.key]);
        return;
      }
      if (event.key === "ArrowRight") setSelected((index) => Math.min(items.length - 1, index + 1));
      if (event.key === "ArrowLeft") setSelected((index) => Math.max(0, index - 1));
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSelected((index) => neighborIndex(justifiedRows, index, 1));
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setSelected((index) => neighborIndex(justifiedRows, index, -1));
      }
      if (event.key === " ") {
        event.preventDefault();
        openClassify();
      }
      if (event.key.toLowerCase() === "c") setCropsOpen(true);
      if (event.key.toLowerCase() === "b") setBlurOpen(true);
      if (event.key.toLowerCase() === "p") void queueCurrent();
      if (event.key.toLowerCase() === "g") setCaptionsOpen(true);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <main className="page">
      {config.data && !config.data.detector_available ? (
        <p className="banner">
          Safety model file is not installed. Images stay in review until a local detector is configured. Nothing is sent out.
        </p>
      ) : null}
      {config.data?.safety_classifier === "fake" ? (
        <p className="banner">Fake safety classifier is on. Use it only with the fixture library, never with real photographs.</p>
      ) : null}
      {!libraries.data?.length ? (
        <LibrarySetup
          suggested={config.data?.suggested_library_root ?? ""}
          statement={config.data?.attestation_statement ?? ""}
          onCreated={() => void queryClient.invalidateQueries({ queryKey: ["libraries"] })}
        />
      ) : (
        <div className="filters">
          <select value={libraryId} onChange={(event) => setLibraryId(event.target.value)}>
            {libraries.data.map((library) => (
              <option key={library.id} value={library.id}>
                {library.name} {library.attested ? "" : "(not attested)"}
              </option>
            ))}
          </select>
          <ScanPanel
            libraryId={libraryId}
            imageCount={libraries.data.find((library) => library.id === libraryId)?.image_count ?? 0}
            detectorAvailable={config.data?.detector_available ?? false}
            safetyClassifier={config.data?.safety_classifier ?? ""}
            onImages={() => {
              void load(true, true);
              setFolderRefresh((value) => value + 1);
            }}
            onLibraryRefresh={() => void queryClient.invalidateQueries({ queryKey: ["libraries"] })}
          />
          <select value={filters.model_state} onChange={(event) => setFilters({ ...filters, model_state: event.target.value })}>
            <option value="">Any model state</option>
            {["SAFE", "SAFE_AFTER_CROP", "REVIEW", "DO_NOT_POST"].map((state) => (
              <option key={state}>{state}</option>
            ))}
          </select>
          <select value={filters.human_rating} onChange={(event) => setFilters({ ...filters, human_rating: event.target.value })}>
            <option value="">Any rating</option>
            {["reject", "review", "safe", "excellent"].map((rating) => (
              <option key={rating}>{rating}</option>
            ))}
          </select>
          <label>
            <input
              type="checkbox"
              checked={filters.unreviewed}
              onChange={(event) => setFilters({ ...filters, unreviewed: event.target.checked })}
            />{" "}
            Unreviewed
          </label>
          <select value={filters.orientation} onChange={(event) => setFilters({ ...filters, orientation: event.target.value })}>
            <option value="">Any orientation</option>
            <option>portrait</option>
            <option>landscape</option>
            <option>square</option>
          </select>
          <select value={filters.queued} onChange={(event) => setFilters({ ...filters, queued: event.target.value })}>
            <option value="">Queued or not</option>
            <option value="true">Queued</option>
            <option value="false">Not queued</option>
          </select>
          <select value={filters.posted} onChange={(event) => setFilters({ ...filters, posted: event.target.value })}>
            <option value="">Posted or not</option>
            <option value="false">Not posted</option>
            <option value="true">Posted</option>
          </select>
          <input
            placeholder="Session folder"
            value={filters.session_name}
            onChange={(event) => setFilters({ ...filters, session_name: event.target.value })}
          />
          <input
            placeholder="Camera"
            value={filters.camera}
            onChange={(event) => setFilters({ ...filters, camera: event.target.value })}
          />
          <input
            type="date"
            aria-label="From date"
            value={filters.date_from}
            onChange={(event) => setFilters({ ...filters, date_from: event.target.value })}
          />
          <input
            type="date"
            aria-label="To date"
            value={filters.date_to}
            onChange={(event) => setFilters({ ...filters, date_to: event.target.value })}
          />
        </div>
      )}
      {libraryId ? (
        <FolderBrowser
          libraryId={libraryId}
          folder={folder}
          recursive={recursive}
          refreshKey={folderRefresh}
          onOpen={openFolder}
          onRecursive={setRecursive}
        />
      ) : null}
      {message ? <p className="help">{message}</p> : null}
      {libraryId && items.length === 0 ? (
        <p className="help">
          {recursive || !folder ? "No images here yet." : "No images directly in this folder. Open a subfolder or include subfolders."}
        </p>
      ) : null}
      <div className="grid-scroll" ref={parentRef}>
        <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const row = justifiedRows[virtualRow.index];
            if (!row) return null;
            return (
              <div
                key={virtualRow.key}
                className="grid-row"
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: `${row.height}px`,
                  transform: `translateY(${virtualRow.start}px)`,
                }}
              >
                {row.items.map((image, offset) => {
                  const index = row.startIndex + offset;
                  const aspect = imageAspect(image);
                  const isSelected = index === selected;
                  const isMulti = multiIds.has(image.id);
                  const tileClass = [
                    "tile",
                    isSelected ? "selected" : "",
                    isMulti ? "multi" : "",
                  ]
                    .filter(Boolean)
                    .join(" ");
                  return (
                    <button
                      key={image.id}
                      type="button"
                      className={tileClass}
                      style={{ aspectRatio: `${aspect}`, height: "100%" }}
                      aria-pressed={isSelected || isMulti}
                      aria-label={`${image.relative_path}, ${image.human_rating ?? "unrated"}`}
                      onClick={(event) => selectTile(index, event)}
                      onDoubleClick={() => openClassify(index)}
                    >
                      {image.thumbnail_url ? (
                        <GridThumb src={image.thumbnail_url} />
                      ) : (
                        <span className="tile-empty">No preview</span>
                      )}
                      <div className="meta meta-top">
                        <span className={`badge ${image.effective_state}`}>{image.effective_state}</span>
                        {image.has_approved_crop ? <span className="badge crop-approved">crop</span> : <span />}
                      </div>
                      <div className="meta">
                        <span>{image.human_rating ?? "unrated"}</span>
                        <span>{image.aesthetic_score ?? "—"}</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
      {cursor ? (
        <button className="ghost" onClick={() => void load(false)}>
          Load more
        </button>
      ) : null}
      <p className="help">
        Keys: 1 reject, 2 review, 3 safe, 4 excellent, arrows move, space classify, C crops, B blur, P queue, G
        captions. ⌘/Ctrl-click multi-select, Shift-click range. In classify: 1–4 rate and advance, Esc close.
        {multiActive ? ` · ${multiIds.size} selected for classify` : ""}
      </p>
      {preview && current ? (
        <div
          className="overlay"
          role="presentation"
          onClick={() => setPreview(false)}
        >
          <div
            ref={classifyDialogRef}
            className="dialog dialog-classify"
            role="dialog"
            aria-modal="true"
            aria-label="Classify image"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="classify-toolbar">
              <p className="classify-meta" title={current.relative_path}>
                {current.relative_path}
                {multiActive
                  ? ` · ${classifyIndices.indexOf(selected) + 1}/${classifyIndices.length} selected`
                  : ` · ${selected + 1}/${items.length}`}
                {ratingBusy ? " · saving…" : ""}
              </p>
              <button type="button" className="ghost" onClick={() => setPreview(false)}>
                Close
              </button>
            </div>
            <div className="classify-stage">
              {current.preview_url ? (
                <img key={current.id} src={current.preview_url} alt="" />
              ) : current.thumbnail_url ? (
                <img key={current.id} src={current.thumbnail_url} alt="" />
              ) : (
                <span className="tile-empty">No preview</span>
              )}
            </div>
            <div className="classify-toolbar">
              <div className="classify-ratings">
                {RATING_KEYS.map(({ key, rating, label }) => (
                  <button
                    key={rating}
                    type="button"
                    className={current.human_rating === rating ? "button active-rating" : "ghost"}
                    disabled={ratingBusy}
                    onClick={() => void rateAndAdvance(rating)}
                  >
                    {key} {label}
                  </button>
                ))}
              </div>
              <p className="classify-meta">
                model {current.model_state ?? "unreviewed"} · rating {current.human_rating ?? "none"}
                {current.has_gps ? " · GPS on original only" : ""}
              </p>
            </div>
          </div>
        </div>
      ) : null}
      {cropsOpen && current ? (
        <CropDialog
          image={current}
          onClose={() => setCropsOpen(false)}
          onApproved={(updated) => {
            setItems((rows) => rows.map((row) => (row.id === updated.id ? { ...row, ...updated } : row)));
            setCropsOpen(false);
          }}
        />
      ) : null}
      {blurOpen && current ? (
        <BlurDialog image={current} onClose={() => setBlurOpen(false)} />
      ) : null}
      {captionsOpen && current ? (
        <CaptionDialog
          image={current}
          policy={config.data?.gemini_policy ?? "text_only"}
          onClose={() => setCaptionsOpen(false)}
        />
      ) : null}
    </main>
  );
}

function scanActive(status: string | undefined): boolean {
  return status === "queued" || status === "running";
}

function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function jobStartMs(job: JobItem): number {
  const fromApi = Date.parse(job.started_at || job.created_at || "");
  if (Number.isFinite(fromApi)) return fromApi;
  const key = `instamagic.scanStart.${job.id}`;
  const stored = Number(window.sessionStorage.getItem(key) || "");
  if (Number.isFinite(stored) && stored > 0) return stored;
  const now = Date.now();
  window.sessionStorage.setItem(key, String(now));
  return now;
}

function formatElapsed(job: JobItem, now: number): string {
  const start = jobStartMs(job);
  const running = scanActive(job.status);
  const finished = Date.parse(job.finished_at || "");
  const end = running ? now : Number.isFinite(finished) ? finished : now;
  const label = formatDuration(end - start);
  return job.status === "queued" && !job.started_at ? `waiting ${label}` : label;
}

function formatRate(done: number, job: JobItem, now: number): string {
  const start = jobStartMs(job);
  const minutes = (now - start) / 60000;
  if (!(minutes > 0) || done <= 0) return "";
  const rate = done / minutes;
  if (rate >= 10) return `${Math.round(rate)}/min`;
  return `${rate.toFixed(1)}/min`;
}

const STAGE_LABELS: Record<string, string> = {
  listing: "Walking folders",
  checking: "Checking if changed",
  hashing: "Hashing",
  reading: "Reading image",
  derivatives: "Thumbnails / preview",
  classify: "ONNX safety classify",
  crops: "Crops / blurs",
  scoring: "Scoring",
  uniqueness: "Similarity pass",
  scanning: "Scanning",
  failed: "File failed",
  done: "Done",
};

function stageLabel(stage: string | null | undefined): string {
  if (!stage) return "Working";
  return STAGE_LABELS[stage] ?? stage;
}

function ScanPanel({
  libraryId,
  imageCount,
  detectorAvailable,
  safetyClassifier,
  onImages,
  onLibraryRefresh,
}: {
  libraryId: string;
  imageCount: number;
  detectorAvailable: boolean;
  safetyClassifier: string;
  onImages: () => void;
  onLibraryRefresh: () => void;
}) {
  const [job, setJob] = useState<JobItem | null>(null);
  const [recent, setRecent] = useState<JobItem[]>([]);
  const [queues, setQueues] = useState<QueuesStatus | null>(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const onImagesRef = useRef(onImages);
  const onLibraryRef = useRef(onLibraryRefresh);
  onImagesRef.current = onImages;
  onLibraryRef.current = onLibraryRefresh;

  async function refreshJobs() {
    const jobs = await api<JobItem[]>(`/api/jobs?library_id=${encodeURIComponent(libraryId)}`);
    const active = jobs.find((item) => scanActive(item.status));
    const latest =
      active ??
      jobs.find(
        (item) => item.started_at || (item.files_seen ?? 0) > 0 || item.status === "failed" || item.status === "succeeded",
      ) ??
      null;
    setJob(latest);
    setRecent(jobs.slice(0, 5));
    return { jobs, active: latest };
  }

  async function refreshQueues() {
    try {
      setQueues(await api<QueuesStatus>("/api/queues"));
    } catch {
      setQueues({ available: false, detail: "queue unavailable", queues: [] });
    }
  }

  useEffect(() => {
    if (!libraryId) return;
    let cancelled = false;
    setJob(null);
    setRecent([]);
    setError("");
    Promise.all([refreshJobs(), refreshQueues()])
      .then(([result]) => {
        if (cancelled) return;
        if (result.active && scanActive(result.active.status)) {
          onImagesRef.current();
          onLibraryRef.current();
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load scan status.");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [libraryId]);

  const active = scanActive(job?.status);

  useEffect(() => {
    // Elapsed clock only while a scan is running — idle setState every 1s was wasteful.
    if (!active || !libraryId) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, libraryId]);

  useEffect(() => {
    if (!libraryId) return;
    let stopped = false;
    let timer = 0;
    let polls = 0;
    const watchActive = active;

    async function poll() {
      let still = false;
      try {
        const result = await refreshJobs();
        await refreshQueues();
        if (stopped) return;
        setError("");
        polls += 1;
        still = result.active ? scanActive(result.active.status) : false;
        // Reload the grid only while a scan is active (or just finished) — never on idle polls.
        if (still && (polls === 1 || polls % 4 === 0)) {
          onImagesRef.current();
          onLibraryRef.current();
        }
        if (watchActive && !still) {
          onImagesRef.current();
          onLibraryRef.current();
        }
      } catch (err: unknown) {
        if (!stopped) setError(err instanceof Error ? err.message : "Could not refresh scan status.");
      }
      if (!stopped) {
        // Fast while scanning; slow status-only poll when idle (no image refetch above).
        timer = window.setTimeout(() => void poll(), still || watchActive ? 1500 : 12000);
      }
    }

    timer = window.setTimeout(() => void poll(), active ? 1500 : 12000);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [libraryId, active, job?.id]);

  async function scan() {
    setError("");
    try {
      const started = await api<{ job_id: string; status: string }>(`/api/libraries/${libraryId}/scan`, {
        method: "POST",
      });
      const result = await refreshJobs();
      await refreshQueues();
      const next = result.jobs.find((item) => item.id === started.job_id) ?? result.active;
      const visible: JobItem = next ?? {
        id: started.job_id,
        kind: "scan",
        status: started.status,
        detail: null,
        error: null,
        library_id: libraryId,
        files_seen: 0,
        files_processed: 0,
        files_skipped: 0,
        files_failed: 0,
        files_total: null,
        current_path: null,
        current_stage: null,
        classified_count: imageCount,
        pending_count: 0,
        created_at: new Date().toISOString(),
        started_at: null,
        finished_at: null,
      };
      setJob(visible);
      onLibraryRef.current();
      if (!scanActive(visible.status)) onImagesRef.current();
    } catch (err: unknown) {
      if (err instanceof Error) setError(err.message);
      else setError("Scan could not start.");
    }
  }

  const seen = job?.files_seen ?? 0;
  const processed = job?.files_processed ?? 0;
  const skipped = job?.files_skipped ?? 0;
  const failed = job?.files_failed ?? 0;
  const total = job?.files_total ?? null;
  const usingJobCounters = seen > 0 || processed > 0 || skipped > 0 || failed > 0;
  const progressLabel = usingJobCounters
    ? total != null && total > 0
      ? `${seen} of ${total} · ${Math.min(100, Math.round((seen / total) * 100))}%`
      : `seen ${seen}`
    : active
      ? `${imageCount} ingested so far`
      : `${imageCount} in library`;
  const elapsed = job ? formatElapsed(job, now) : "";
  const rate = job ? formatRate(usingJobCounters ? seen : imageCount, job, now) : "";
  const classified = job?.classified_count ?? imageCount;
  const pending = job?.pending_count ?? 0;
  const queueLine =
    queues == null
      ? "Queues…"
      : !queues.available
        ? queues.detail || "queue unavailable"
        : queues.queues
            .map((queue) => `${queue.name}: q${queue.queued} run${queue.started} fail${queue.failed} w${queue.workers}`)
            .join(" · ");

  return (
    <div className="scan-panel">
      <button className="button" disabled={!libraryId || active} onClick={() => void scan()}>
        {active ? "Scanning…" : job?.status === "interrupted" ? "Resume scan" : "Scan"}
      </button>
      {job ? (
        <div className="scan-detail">
          <strong>{job.status}</strong>
          {active ? <span>Stage: {stageLabel(job.current_stage)}</span> : null}
          {elapsed ? <span>{elapsed}</span> : null}
          {rate ? <span>{rate}</span> : null}
          <span>{progressLabel}</span>
          {usingJobCounters ? (
            <span>
              processed {processed} · skipped {skipped} · failed {failed}
            </span>
          ) : null}
          {job.current_path ? <span className="scan-path">Current: {job.current_path}</span> : null}
          {job.detail && job.status !== "failed" ? <span>{job.detail}</span> : null}
          {job.status === "failed" ? <span className="error">{job.error || job.detail || "Scan failed."}</span> : null}
        </div>
      ) : null}
      <div className="scan-status">
        <span>
          ONNX {detectorAvailable ? "available" : "unavailable"}
          {safetyClassifier ? ` (${safetyClassifier})` : ""}
        </span>
        <span>
          Classified {classified}
          {pending ? ` · pending ${pending}` : ""}
        </span>
        <span>{queueLine}</span>
        {recent.length ? (
          <ul className="scan-jobs">
            {recent.map((item) => (
              <li key={item.id}>
                {item.status}
                {(item.files_seen ?? 0) > 0
                  ? ` · seen ${item.files_seen} proc ${item.files_processed ?? 0}`
                  : ""}
                {item.started_at ? ` · started ${new Date(item.started_at).toLocaleTimeString()}` : ""}
                {item.finished_at ? ` · finished ${new Date(item.finished_at).toLocaleTimeString()}` : ""}
                {item.current_stage ? ` · ${stageLabel(item.current_stage)}` : ""}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      {error ? <span className="error">{error}</span> : null}
    </div>
  );
}

function FolderBrowser({
  libraryId,
  folder,
  recursive,
  refreshKey,
  onOpen,
  onRecursive,
}: {
  libraryId: string;
  folder: string;
  recursive: boolean;
  refreshKey: number;
  onOpen: (path: string) => void;
  onRecursive: (value: boolean) => void;
}) {
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams();
    if (folder) params.set("path", folder);
    api<FolderListing>(`/api/libraries/${libraryId}/folders?${params.toString()}`)
      .then((next) => {
        if (cancelled) return;
        setListing(next);
        setError("");
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load folders.");
      });
    return () => {
      cancelled = true;
    };
  }, [libraryId, folder, refreshKey]);

  const crumbs = listing?.breadcrumbs ?? [{ name: "Library", path: "" }];
  return (
    <section className="folders">
      <div className="row">
        <button className="ghost" disabled={!folder} onClick={() => onOpen(listing?.parent ?? "")}>
          ↑ Up
        </button>
        <nav className="crumbs" aria-label="Folder path">
          {crumbs.map((crumb, index) => (
            <span key={crumb.path}>
              {index > 0 ? <span className="crumb-sep">/</span> : null}
              {crumb.path === folder ? (
                <strong>{crumb.name}</strong>
              ) : (
                <button className="crumb" onClick={() => onOpen(crumb.path)}>
                  {crumb.name}
                </button>
              )}
            </span>
          ))}
        </nav>
        <label className="help">
          <input type="checkbox" checked={recursive} onChange={(event) => onRecursive(event.target.checked)} /> Include
          subfolders
        </label>
        {listing ? (
          <span className="help">
            {listing.image_count} here · {listing.folders.length} folders
            {listing.source === "filesystem" ? " · not scanned yet" : ""}
          </span>
        ) : null}
      </div>
      {error ? <p className="error">{error}</p> : null}
      {listing && listing.folders.length ? (
        <div className="folder-grid">
          {listing.folders.map((entry) => (
            <button key={entry.path} className="folder-tile" onClick={() => onOpen(entry.path)} title={entry.path}>
              {entry.thumbnail_url ? (
                <img src={entry.thumbnail_url} alt="" loading="lazy" decoding="async" />
              ) : (
                <span className="folder-empty">▢</span>
              )}
              <span className="folder-name">{entry.name}</span>
              <span className="folder-count">{entry.image_count}</span>
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function LibrarySetup({
  suggested,
  statement,
  onCreated,
}: {
  suggested: string;
  statement: string;
  onCreated: () => void;
}) {
  const [name, setName] = useState("Archive");
  const [rootPath, setRootPath] = useState(suggested);
  const [pathEdited, setPathEdited] = useState(false);
  const [confirmed, setConfirmed] = useState(false);

  useEffect(() => {
    if (!pathEdited && suggested) setRootPath(suggested);
  }, [pathEdited, suggested]);
  const [error, setError] = useState("");

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await api("/api/libraries", {
        method: "POST",
        body: JSON.stringify({ name, root_path: rootPath, confirmed, statement: confirmed ? statement : "" }),
      });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add the library.");
    }
  }

  return (
    <form className="panel" onSubmit={onSubmit}>
      <h2>Add a photo library</h2>
      <p>The folder is read only. Instamagic stores paths and makes its own thumbnails.</p>
      <input value={name} onChange={(event) => setName(event.target.value)} aria-label="Library name" />
      <input
        value={rootPath}
        onChange={(event) => {
          setPathEdited(true);
          setRootPath(event.target.value);
        }}
        aria-label="Library path"
      />
      <label>
        <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> {statement}
      </label>
      {error ? <p className="error">{error}</p> : null}
      <button className="button" type="submit">
        Save library
      </button>
    </form>
  );
}

function CropDialog({
  image,
  onClose,
  onApproved,
}: {
  image: ImageItem;
  onClose: () => void;
  onApproved: (updated: Pick<ImageItem, "id" | "has_approved_crop" | "model_state" | "effective_state">) => void;
}) {
  const crops = useQuery({
    queryKey: ["crops", image.id],
    queryFn: () => api<CropItem[]>(`/api/images/${image.id}/crops`),
  });
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState<CropRect | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const frameRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const [layout, setLayout] = useState<ContainedLayout | null>(null);
  const dragRef = useRef<{
    mode: "move" | "nw" | "ne" | "sw" | "se";
    startX: number;
    startY: number;
    origin: CropRect;
    pointerId: number;
  } | null>(null);

  const shown =
    (activeId ? crops.data?.find((crop) => crop.id === activeId) : null) ??
    crops.data?.find((crop) => crop.aspect_ratio === "4:5") ??
    crops.data?.[0];
  const modelState = image.model_state ?? "";
  const isDoNotPost = modelState === "DO_NOT_POST";
  const isSafeAfterCrop = modelState === "SAFE_AFTER_CROP";

  function selectCrop(crop: CropItem) {
    setActiveId(crop.id);
    setDraft({ x: crop.x, y: crop.y, width: crop.width, height: crop.height });
    setError("");
  }

  useEffect(() => {
    if (!shown) {
      setDraft(null);
      return;
    }
    setDraft({ x: shown.x, y: shown.y, width: shown.width, height: shown.height });
    setError("");
    // Reset only when the selected suggestion identity changes (not on every nudge).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown?.id]);

  function measureLayout() {
    const img = imgRef.current;
    if (!img || !image.width || !image.height) {
      setLayout(null);
      return;
    }
    setLayout(containedLayout(img.clientWidth, img.clientHeight, image.width, image.height));
  }

  useEffect(() => {
    measureLayout();
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver(() => measureLayout());
    observer.observe(frame);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [image.id, image.width, image.height, image.preview_url]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!image.width || !image.height) return;
      const step = event.shiftKey ? 10 : 1;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setDraft((current) => (current ? moveCrop(current, -step, 0, image.width!, image.height!) : current));
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        setDraft((current) => (current ? moveCrop(current, step, 0, image.width!, image.height!) : current));
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setDraft((current) => (current ? moveCrop(current, 0, -step, image.width!, image.height!) : current));
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        setDraft((current) => (current ? moveCrop(current, 0, step, image.width!, image.height!) : current));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [image.width, image.height]);

  async function approve(crop: CropItem) {
    if (!draft || !image.width || !image.height || isDoNotPost) return;
    setBusy(true);
    setError("");
    try {
      const rect = clampCrop(draft, image.width, image.height);
      await api<CropItem>(`/api/images/${image.id}/crops/${crop.id}/approve`, {
        method: "POST",
        body: JSON.stringify({
          x: rect.x,
          y: rect.y,
          width: rect.width,
          height: rect.height,
        }),
      });
      const refreshed = await api<ImageItem>(`/api/images/${image.id}`);
      onApproved({
        id: refreshed.id,
        has_approved_crop: refreshed.has_approved_crop ?? true,
        model_state: refreshed.model_state,
        effective_state: refreshed.effective_state,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not approve this crop.");
    } finally {
      setBusy(false);
    }
  }

  function beginDrag(mode: "move" | "nw" | "ne" | "sw" | "se", event: ReactPointerEvent<HTMLElement>) {
    if (!draft || !layout || !image.width || !image.height || !shown) return;
    event.preventDefault();
    event.stopPropagation();
    const origin = draft;
    const pointerId = event.pointerId;
    const startX = event.clientX;
    const startY = event.clientY;
    const scale = layout.scale;
    const imageW = image.width;
    const imageH = image.height;
    const aspect = parseAspectRatio(shown.aspect_ratio, origin.width, origin.height);
    dragRef.current = { mode, startX, startY, origin, pointerId };

    function onMove(ev: PointerEvent) {
      if (ev.pointerId !== pointerId) return;
      const dx = (ev.clientX - startX) / scale;
      const dy = (ev.clientY - startY) / scale;
      if (mode === "move") {
        setDraft(moveCrop(origin, dx, dy, imageW, imageH));
      } else {
        setDraft(resizeCropAspect(origin, mode, dx, dy, aspect, imageW, imageH));
      }
    }
    function onUp(ev: PointerEvent) {
      if (ev.pointerId !== pointerId) return;
      dragRef.current = null;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }

  const boxCss = draft && layout ? cropToCss(draft, layout) : null;
  const canApprove = Boolean(shown && draft && image.width && image.height && !isDoNotPost);

  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog" onClick={(event) => event.stopPropagation()}>
        <h2>Crops</h2>
        <p className="help">Drag the frame to reposition. Corner handles resize with the aspect locked. Arrows nudge.</p>
        {isDoNotPost ? (
          <p className="error">
            This frame is DO_NOT_POST. Crop approval cannot override that — rate it reject or leave it blocked.
          </p>
        ) : isSafeAfterCrop ? (
          <p className="help">
            Approving a crop marks the publishing window for this SAFE_AFTER_CROP frame. Model state stays
            SAFE_AFTER_CROP until you rate it safe/excellent (keys 3/4).
          </p>
        ) : (
          <p className="help">
            You can save a crop on this {modelState || "REVIEW"} frame; model_state stays {modelState || "REVIEW"}.
            Human rating (keys 3/4) is what marks it safe — crop approval alone does not change the safety badge.
          </p>
        )}
        <div className="preview-frame crop-stage" ref={frameRef}>
          {image.preview_url ? (
            <img ref={imgRef} src={image.preview_url} alt="" onLoad={measureLayout} draggable={false} />
          ) : null}
          {boxCss ? (
            <div
              className="crop-box interactive"
              role="slider"
              aria-label="Crop frame"
              aria-valuetext={
                draft
                  ? `${draft.width}×${draft.height} at ${draft.x},${draft.y}`
                  : undefined
              }
              tabIndex={0}
              style={{
                left: boxCss.left,
                top: boxCss.top,
                width: boxCss.width,
                height: boxCss.height,
              }}
              onPointerDown={(event) => beginDrag("move", event)}
            >
              {(["nw", "ne", "sw", "se"] as const).map((corner) => (
                <span
                  key={corner}
                  className={`crop-handle ${corner}`}
                  aria-hidden="true"
                  onPointerDown={(event) => beginDrag(corner, event)}
                />
              ))}
            </div>
          ) : null}
        </div>
        <div className="row">
          {crops.data?.map((crop) => (
            <button
              key={crop.id}
              className={shown?.id === crop.id ? "ghost active" : "ghost"}
              onClick={() => selectCrop(crop)}
            >
              {crop.aspect_ratio}
              {crop.approved ? " approved" : ""}
            </button>
          ))}
        </div>
        {shown ? (
          <>
            <p>{shown.reason}</p>
            {draft ? (
              <p className="help">
                {draft.width}×{draft.height} at {draft.x},{draft.y}
                {shown.aspect_ratio !== "original" ? ` · ${shown.aspect_ratio}` : ""}
              </p>
            ) : null}
            {error ? <p className="error">{error}</p> : null}
            <button className="button" disabled={!canApprove || busy} onClick={() => void approve(shown)}>
              {busy ? "Saving…" : isSafeAfterCrop ? "Approve this crop" : "Save this crop"}
            </button>
          </>
        ) : (
          <p>No crops yet.</p>
        )}
      </div>
    </div>
  );
}

function BlurDialog({ image, onClose }: { image: ImageItem; onClose: () => void }) {
  const blurs = useQuery({
    queryKey: ["blurs", image.id],
    queryFn: () => api<BlurItem[]>(`/api/images/${image.id}/blurs`),
  });
  const [strength, setStrength] = useState("standard");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const items = blurs.data ?? [];
  const previewUrl = items.find((item) => item.preview_url)?.preview_url ?? null;
  const approved = items.some((item) => item.approved);

  useEffect(() => {
    const current = blurs.data?.[0]?.strength;
    if (current) setStrength(current);
  }, [blurs.data]);

  async function applyStrength(next: string) {
    setBusy(true);
    setError("");
    try {
      await api(`/api/images/${image.id}/blurs/strength`, {
        method: "POST",
        body: JSON.stringify({ strength: next }),
      });
      setStrength(next);
      await blurs.refetch();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update blur strength.");
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    setBusy(true);
    setError("");
    try {
      await api(`/api/images/${image.id}/blurs/approve`, { method: "POST" });
      await blurs.refetch();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not approve blur covers.");
    } finally {
      setBusy(false);
    }
  }

  async function addManual() {
    if (!image.width || !image.height) return;
    setBusy(true);
    setError("");
    try {
      const w = Math.round(image.width * 0.28);
      const h = Math.round(image.height * 0.22);
      await api(`/api/images/${image.id}/blurs`, {
        method: "POST",
        body: JSON.stringify({
          x: Math.round((image.width - w) / 2),
          y: Math.round((image.height - h) / 2),
          width: w,
          height: h,
          strength,
          reason: "Manual ellipse cover.",
        }),
      });
      await blurs.refetch();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add a manual cover.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog" onClick={(event) => event.stopPropagation()}>
        <h2>Blur covers</h2>
        <p className="help">
          Covers are written only as derivatives. The original stays read-only. An approved set must fully cover every
          clear exposed region before it can stand in for a crop.
        </p>
        <div className="preview-frame">
          {previewUrl || image.preview_url ? <img src={previewUrl ?? image.preview_url ?? ""} alt="" /> : null}
          {items.map((blur) =>
            image.width && image.height ? (
              <div
                key={blur.id}
                className={blur.approved ? "blur-ellipse approved" : "blur-ellipse"}
                style={{
                  left: `${(blur.x / image.width) * 100}%`,
                  top: `${(blur.y / image.height) * 100}%`,
                  width: `${(blur.width / image.width) * 100}%`,
                  height: `${(blur.height / image.height) * 100}%`,
                }}
              />
            ) : null,
          )}
        </div>
        <div className="row">
          <label>
            Strength{" "}
            <select
              value={strength}
              disabled={busy || items.length === 0}
              onChange={(event) => void applyStrength(event.target.value)}
            >
              <option value="subtle">Subtle</option>
              <option value="standard">Standard</option>
              <option value="strong">Strong</option>
            </select>
          </label>
          <button className="ghost" disabled={busy} onClick={() => void addManual()}>
            Add manual ellipse
          </button>
          <button className="button" disabled={busy || items.length === 0} onClick={() => void approve()}>
            {approved ? "Approved" : "Approve covers"}
          </button>
        </div>
        {error ? <p className="error">{error}</p> : null}
        {items.length ? (
          <ul className="blur-list">
            {items.map((blur) => (
              <li key={blur.id}>
                {blur.shape} · σ {blur.radius.toFixed(1)} · feather {blur.feather}px
                {blur.covers_unsafe ? " · covers unsafe" : " · manual"}
                {blur.approved ? " · approved" : ""}
                <div className="help">{blur.reason}</div>
              </li>
            ))}
          </ul>
        ) : (
          <p>No blur suggestions yet. Add a manual ellipse, or scan an image with a detected unsafe region.</p>
        )}
      </div>
    </div>
  );
}

function CaptionDialog({ image, policy, onClose }: { image: ImageItem; policy: string; onClose: () => void }) {
  const captions = useQuery({
    queryKey: ["captions", image.id],
    queryFn: () => api<CaptionItem[]>(`/api/images/${image.id}/captions`),
  });
  const [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false);

  async function generate() {
    setError("");
    try {
      await api(`/api/images/${image.id}/captions`, {
        method: "POST",
        body: JSON.stringify({ confirmed: policy !== "manual_only" || confirmed }),
      });
      await captions.refetch();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Caption request failed.");
    }
  }

  async function mark(caption: CaptionItem, status: string, text?: string) {
    await api(`/api/captions/${caption.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status, text }),
    });
    await captions.refetch();
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog" onClick={(event) => event.stopPropagation()}>
        <h2>Captions</h2>
        <p>Gemini receives text context only unless the image policy explicitly allows an approved preview.</p>
        {policy === "manual_only" ? (
          <label>
            <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> I confirm this request.
          </label>
        ) : null}
        <button className="button" onClick={() => void generate()}>
          Generate
        </button>
        {error ? <p className="error">{error}</p> : null}
        <div className="card-list">
          {captions.data?.map((caption) => (
            <article key={caption.id}>
              <strong>
                {caption.style} · {caption.language} · {caption.status}
              </strong>
              <p>{caption.text}</p>
              <div className="row">
                <button className="ghost" onClick={() => void mark(caption, "used")}>
                  Used
                </button>
                <button className="ghost" onClick={() => void mark(caption, "rejected")}>
                  Rejected
                </button>
                <button
                  className="ghost"
                  onClick={() => {
                    const text = window.prompt("Edit caption", caption.text);
                    if (text) void mark(caption, "modified", text);
                  }}
                >
                  Edit
                </button>
              </div>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}
