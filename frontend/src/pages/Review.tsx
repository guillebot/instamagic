import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  FormEvent,
  MouseEvent as ReactMouseEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, api } from "../api";
import { BlurPanel } from "../BlurPanel";
import { ClassifyPublishPanel } from "../ClassifyPublishPanel";
import { CropPanel } from "../CropPanel";
import { SAFETY_FILTERS, lifecycleLabel } from "../lifecycle";
import { displayPreviewUrl, publishVariantLabel } from "../publishPresentation";
import { acquireThumbSlot } from "../thumbQueue";
import {
  appendGridPage,
  applyPendingNewPhotos,
  buildRowTopIndex,
  captureScrollAnchor,
  createNavStacks,
  hardResetGridItems,
  indexOfImageId,
  mergeGridUpdatesInPlace,
  navBack,
  navForward,
  navVisit,
  nextImageIdAfterCurrent,
  resolveCurrentImage,
  scrollTopForAnchor,
  shouldApplyInitialImageFromUrl,
  type GridImageItem,
  type ReviewNavStacks,
  type ScrollAnchor,
} from "../reviewNavigation";
import type {
  AppConfig,
  CaptionItem,
  FolderListing,
  ImageItem,
  JobItem,
  LibraryItem,
  QueuesStatus,
} from "../types";

type PrepMode = "rate" | "crop" | "blur";

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
      loading="lazy"
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
const PAGE_SIZE = 60;

type JustifiedRow = {
  startIndex: number;
  items: GridImageItem[];
  height: number;
};

function imageAspect(image: ImageItem): number {
  if (image.width && image.height && image.height > 0) return image.width / image.height;
  if (image.orientation === "portrait") return 3 / 4;
  if (image.orientation === "landscape") return 4 / 3;
  return 1;
}

/** Pack images into same-height rows; tile width follows aspect so the full photo is visible. */
function buildJustifiedRows(items: GridImageItem[], containerWidth: number, rowHeight: number, gap: number): JustifiedRow[] {
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
  const libraries = useQuery({
    queryKey: ["libraries"],
    queryFn: () => api<LibraryItem[]>("/api/libraries"),
    refetchOnWindowFocus: false,
  });
  const config = useQuery({
    queryKey: ["config"],
    queryFn: () => api<AppConfig>("/api/config"),
    refetchOnWindowFocus: false,
  });
  const [libraryId, setLibraryId] = useState("");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [items, setItems] = useState<GridImageItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [listExhausted, setListExhausted] = useState(false);
  const [pendingNewPhotos, setPendingNewPhotos] = useState<ImageItem[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [pinnedImage, setPinnedImage] = useState<ImageItem | null>(null);
  const [navStacks, setNavStacks] = useState<ReviewNavStacks>(() => createNavStacks());
  const [multiIds, setMultiIds] = useState<Set<string>>(() => new Set());
  const [preview, setPreview] = useState(false);
  const [prepMode, setPrepMode] = useState<PrepMode>("rate");
  const [showOriginalPreview, setShowOriginalPreview] = useState(false);
  const [ratingBusy, setRatingBusy] = useState(false);
  const [captionsOpen, setCaptionsOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [folderRefresh, setFolderRefresh] = useState(0);
  const [searchParams, setSearchParams] = useSearchParams();
  const folder = searchParams.get("folder") ?? "";
  const recursive = searchParams.get("recursive") === "1";
  const focusImageId = searchParams.get("image");
  const parentRef = useRef<HTMLDivElement>(null);
  const classifyDialogRef = useRef<HTMLDivElement>(null);
  const currentIdRef = useRef<string | null>(null);
  const pinnedRef = useRef<ImageItem | null>(null);
  const urlSelectionInitialized = useRef(false);
  const sessionImagesRef = useRef<Map<string, ImageItem>>(new Map());
  const sentinelRef = useRef<HTMLDivElement>(null);
  const cursorRef = useRef<string | null>(null);
  const pendingScrollAnchorRef = useRef<ScrollAnchor | null>(null);
  const suppressSelectionScrollRef = useRef(false);
  const justifiedRowsRef = useRef<JustifiedRow[]>([]);
  const [width, setWidth] = useState(960);

  useEffect(() => {
    cursorRef.current = cursor;
  }, [cursor]);

  useEffect(() => {
    currentIdRef.current = currentId;
  }, [currentId]);
  useEffect(() => {
    pinnedRef.current = pinnedImage;
  }, [pinnedImage]);

  const syncImageParam = useCallback(
    (imageId: string | null, replace = true) => {
      setSearchParams(
        (current) => {
          const params = new URLSearchParams(current);
          if (imageId) params.set("image", imageId);
          else params.delete("image");
          return params;
        },
        { replace },
      );
    },
    [setSearchParams],
  );

  const rememberImage = useCallback((image: ImageItem) => {
    sessionImagesRef.current.set(image.id, image);
  }, []);

  const pinIfMissingFromList = useCallback(
    (imageId: string) => {
      const inList = items.some((row) => row.id === imageId);
      if (inList) {
        setPinnedImage(null);
        return;
      }
      const cached = sessionImagesRef.current.get(imageId) ?? pinnedRef.current;
      if (cached?.id === imageId) setPinnedImage(cached);
    },
    [items],
  );

  const goToImage = useCallback(
    (nextId: string, options?: { recordHistory?: boolean; replaceUrl?: boolean }) => {
      const recordHistory = options?.recordHistory ?? true;
      const replaceUrl = options?.replaceUrl ?? true;
      if (recordHistory) {
        setNavStacks((stacks) => navVisit(stacks, currentIdRef.current, nextId));
      }
      setCurrentId(nextId);
      pinIfMissingFromList(nextId);
      syncImageParam(nextId, replaceUrl);
    },
    [pinIfMissingFromList, syncImageParam],
  );

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

  const rememberGridScrollAnchor = useCallback(() => {
    const el = parentRef.current;
    if (!el) return;
    pendingScrollAnchorRef.current = captureScrollAnchor(
      el.scrollTop,
      currentIdRef.current,
      buildRowTopIndex(justifiedRowsRef.current, GRID_GAP),
    );
  }, []);

  const restoreGridScrollAnchor = useCallback(() => {
    const el = parentRef.current;
    const anchor = pendingScrollAnchorRef.current;
    if (!el || !anchor) return;
    const top = scrollTopForAnchor(anchor, buildRowTopIndex(justifiedRowsRef.current, GRID_GAP));
    if (top != null) el.scrollTop = top;
    pendingScrollAnchorRef.current = null;
  }, []);

  async function fetchImagePage(nextCursor: string | null): Promise<Page> {
    const params = new URLSearchParams(queryString);
    params.set("limit", String(PAGE_SIZE));
    if (nextCursor) params.set("cursor", nextCursor);
    return api<Page>(`/api/images?${params.toString()}`);
  }

  async function loadInitial(resetSelection = true) {
    const page = await fetchImagePage(null);
    setItems(hardResetGridItems(page.items));
    setCursor(page.next_cursor);
    setListExhausted(!page.next_cursor);
    setPendingNewPhotos([]);
    if (resetSelection) {
      const urlImage = searchParams.get("image");
      const pick =
        urlImage && page.items.some((row) => row.id === urlImage)
          ? urlImage
          : page.items[0]?.id ?? null;
      setCurrentId(pick);
      setPinnedImage(null);
      setNavStacks(createNavStacks());
      setMultiIds(new Set());
      if (pick) syncImageParam(pick, true);
      urlSelectionInitialized.current = true;
    }
  }

  const loadingMore = useRef(false);

  async function loadMorePage() {
    const nextCursor = cursorRef.current;
    if (!nextCursor || loadingMore.current || listExhausted) return;
    loadingMore.current = true;
    rememberGridScrollAnchor();
    try {
      const page = await fetchImagePage(nextCursor);
      setItems((previous) => appendGridPage(previous, page.items));
      setCursor(page.next_cursor);
      setListExhausted(!page.next_cursor);
      suppressSelectionScrollRef.current = true;
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : "Could not load more.");
    } finally {
      loadingMore.current = false;
    }
  }

  async function peekLibraryHeadFromScan() {
    rememberGridScrollAnchor();
    try {
      const page = await fetchImagePage(null);
      setItems((previous) => {
        const { items: merged, pendingNewItems } = mergeGridUpdatesInPlace(previous, page.items);
        if (pendingNewItems.length > 0) {
          setPendingNewPhotos((current) => {
            const seen = new Set(current.map((row) => row.id));
            const extra = pendingNewItems.filter((row) => !seen.has(row.id));
            return extra.length ? [...current, ...extra] : current;
          });
        }
        return merged;
      });
      suppressSelectionScrollRef.current = true;
      setFolderRefresh((value) => value + 1);
    } catch {
      /* scan peek is best-effort */
    }
  }

  function showPendingNewPhotos() {
    if (pendingNewPhotos.length === 0) return;
    rememberGridScrollAnchor();
    setItems((previous) => applyPendingNewPhotos(previous, pendingNewPhotos));
    setPendingNewPhotos([]);
    suppressSelectionScrollRef.current = true;
  }

  useEffect(() => {
    if (!libraryId) return;
    void loadInitial(true).catch((err: unknown) =>
      setMessage(err instanceof Error ? err.message : "Could not load images."),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryString, libraryId]);

  useEffect(() => {
    if (!focusImageId) return;
    if (items.some((row) => row.id === focusImageId)) {
      setCurrentId(focusImageId);
      setPreview(true);
      return;
    }
    let cancelled = false;
    void api<ImageItem>(`/api/images/${focusImageId}`)
      .then((image) => {
        if (cancelled) return;
        sessionImagesRef.current.set(image.id, image);
        setItems((rows) => (rows.some((row) => row.id === image.id) ? rows : [image, ...rows]));
        setCurrentId(image.id);
        setPreview(true);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setMessage(err instanceof Error ? err.message : "Could not open image from link.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [focusImageId, items]);

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
    justifiedRowsRef.current = justifiedRows;
  }, [justifiedRows]);

  useLayoutEffect(() => {
    if (!suppressSelectionScrollRef.current) return;
    restoreGridScrollAnchor();
    suppressSelectionScrollRef.current = false;
  }, [items, preview, restoreGridScrollAnchor]);

  useEffect(() => {
    const initial = shouldApplyInitialImageFromUrl(
      urlSelectionInitialized.current,
      items,
      searchParams.get("image"),
    );
    if (initial) {
      urlSelectionInitialized.current = true;
      setCurrentId(initial);
      setPinnedImage(null);
    } else if (items.length > 0 && !urlSelectionInitialized.current) {
      urlSelectionInitialized.current = true;
    }
  }, [items, searchParams]);

  useEffect(() => {
    if (items.length === 0) {
      setCurrentId(null);
      setPinnedImage(null);
      return;
    }
    if (!currentId) setCurrentId(items[0].id);
  }, [items, currentId]);

  const selectedIndex = indexOfImageId(items, currentId);

  useEffect(() => {
    if (!items.length || preview || !currentId) return;
    if (suppressSelectionScrollRef.current) return;
    const itemIndex = indexOfImageId(items, currentId);
    if (itemIndex < 0) return;
    const rowIndex = rowIndexForItem(justifiedRows, itemIndex);
    if (rowIndex < 0) return;
    virtualizer.scrollToIndex(rowIndex, { align: "auto" });
    // Scroll only when keyboard/click selection moves — not when row packing recomputes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId, preview]);

  useEffect(() => {
    const root = parentRef.current;
    const sentinel = sentinelRef.current;
    if (!root || !sentinel || listExhausted) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) void loadMorePage();
      },
      { root, rootMargin: "480px 0px", threshold: 0 },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listExhausted, cursor, items.length]);

  const current = resolveCurrentImage(items, currentId, pinnedImage);
  useEffect(() => {
    if (current) rememberImage(current);
  }, [current, rememberImage]);

  const multiActive = multiIds.size > 0;

  const classifyIds = useMemo(() => {
    if (!multiActive) return items.map((row) => row.id);
    return items.filter((row) => multiIds.has(row.id)).map((row) => row.id);
  }, [items, multiActive, multiIds]);

  const applyRating = useCallback(async (imageId: string, rating: string) => {
    const updated = await api<ImageItem>(`/api/images/${imageId}/rating`, {
      method: "POST",
      body: JSON.stringify({ rating }),
    });
    setItems((rows) => rows.map((row) => (row.id === updated.id ? updated : row)));
    rememberImage(updated);
    return updated;
  }, [rememberImage]);

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
    if (!currentId) return;
    const orderedIds = [...classifyIds];
    const nextId = nextImageIdAfterCurrent(orderedIds, currentId);
    if (nextId) {
      goToImage(nextId, { recordHistory: false });
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
    if (!currentId) return;
    const pos = classifyIds.indexOf(currentId);
    if (pos < 0) return;
    const nextId = classifyIds[pos + delta];
    if (nextId) goToImage(nextId);
  }

  function navigateBack() {
    if (!currentId) return false;
    const result = navBack(navStacks, currentId);
    if (!result.targetId) return false;
    setNavStacks(result.stacks);
    setCurrentId(result.targetId);
    pinIfMissingFromList(result.targetId);
    syncImageParam(result.targetId, true);
    return true;
  }

  function navigateForward() {
    if (!currentId) return false;
    const result = navForward(navStacks, currentId);
    if (!result.targetId) return false;
    setNavStacks(result.stacks);
    setCurrentId(result.targetId);
    pinIfMissingFromList(result.targetId);
    syncImageParam(result.targetId, true);
    return true;
  }

  function openClassify(atIndex?: number, mode: PrepMode = "rate") {
    const index = atIndex ?? selectedIndex;
    if (atIndex !== undefined) {
      const image = items[atIndex];
      if (image) goToImage(image.id);
    }
    if (multiIds.size > 0) {
      const image = items[index];
      if (!image || !multiIds.has(image.id)) {
        const first = items.find((item) => multiIds.has(item.id));
        if (first) goToImage(first.id);
      }
    }
    rememberGridScrollAnchor();
    suppressSelectionScrollRef.current = true;
    setMessage("");
    setPrepMode(mode);
    setPreview(true);
  }

  function closeClassify() {
    suppressSelectionScrollRef.current = true;
    setPreview(false);
    setPrepMode("rate");
    setShowOriginalPreview(false);
  }

  function patchImage(updated: Partial<ImageItem> & { id: string }) {
    setItems((rows) => rows.map((row) => (row.id === updated.id ? { ...row, ...updated } : row)));
    setPinnedImage((pinned) => (pinned?.id === updated.id ? { ...pinned, ...updated } : pinned));
  }

  function selectTile(index: number, event: ReactMouseEvent) {
    const image = items[index];
    if (!image) return;
    if (event.metaKey || event.ctrlKey) {
      goToImage(image.id);
      setMultiIds((currentIds) => {
        const next = new Set(currentIds);
        if (next.has(image.id)) next.delete(image.id);
        else next.add(image.id);
        return next;
      });
      return;
    }
    if (event.shiftKey) {
      const from = selectedIndex >= 0 ? selectedIndex : index;
      const lo = Math.min(from, index);
      const hi = Math.max(from, index);
      setMultiIds(new Set(items.slice(lo, hi + 1).map((item) => item.id)));
      goToImage(image.id);
      return;
    }
    goToImage(image.id);
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
        if (captionsOpen) {
          setCaptionsOpen(false);
          return;
        }
        if (preview && prepMode !== "rate") {
          setPrepMode("rate");
          return;
        }
        if (preview) {
          closeClassify();
          return;
        }
        return;
      }
      if (preview) {
        if (event.key.toLowerCase() === "c") {
          event.preventDefault();
          setPrepMode((mode) => (mode === "crop" ? "rate" : "crop"));
          return;
        }
        if (event.key.toLowerCase() === "b") {
          event.preventDefault();
          setPrepMode((mode) => (mode === "blur" ? "rate" : "blur"));
          return;
        }
        // 1–4 always rate-and-advance so crop/blur + rate stays one stop.
        if (RATINGS[event.key]) {
          event.preventDefault();
          setPrepMode("rate");
          void rateAndAdvance(RATINGS[event.key]);
          return;
        }
        if (prepMode !== "rate") return;
        if (event.key === "ArrowRight") {
          event.preventDefault();
          if (!navigateForward()) moveInClassifySet(1);
        }
        if (event.key === "ArrowLeft" || event.key === "Backspace" || event.key.toLowerCase() === "z") {
          event.preventDefault();
          if (!navigateBack()) moveInClassifySet(-1);
        }
        return;
      }
      if (captionsOpen) return;
      if (RATINGS[event.key]) {
        event.preventDefault();
        void rate(RATINGS[event.key]);
        return;
      }
      if (event.key === "ArrowRight") {
        if (!navigateForward() && selectedIndex >= 0) {
          const next = items[selectedIndex + 1];
          if (next) goToImage(next.id);
        }
      }
      if (event.key === "ArrowLeft" || event.key === "Backspace" || event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (!navigateBack() && selectedIndex > 0) {
          const prev = items[selectedIndex - 1];
          if (prev) goToImage(prev.id);
        }
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (selectedIndex >= 0) {
          const nextIndex = neighborIndex(justifiedRows, selectedIndex, 1);
          const next = items[nextIndex];
          if (next) goToImage(next.id);
        }
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        if (selectedIndex >= 0) {
          const nextIndex = neighborIndex(justifiedRows, selectedIndex, -1);
          const next = items[nextIndex];
          if (next) goToImage(next.id);
        }
      }
      if (event.key === " ") {
        event.preventDefault();
        openClassify();
      }
      if (event.key.toLowerCase() === "c") openClassify(undefined, "crop");
      if (event.key.toLowerCase() === "b") openClassify(undefined, "blur");
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
            onPeekLibraryHead={() => void peekLibraryHeadFromScan()}
            onLibraryRefresh={() => void queryClient.invalidateQueries({ queryKey: ["libraries"] })}
          />
          <select
            aria-label="Safety badge filter"
            value={filters.model_state}
            onChange={(event) => setFilters({ ...filters, model_state: event.target.value })}
          >
            <option value="">Any safety</option>
            {SAFETY_FILTERS.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            aria-label="Human rating filter"
            value={filters.human_rating}
            onChange={(event) => setFilters({ ...filters, human_rating: event.target.value })}
          >
            <option value="">Any rating</option>
            {RATING_KEYS.map(({ rating, label }) => (
              <option key={rating} value={rating}>
                {label}
              </option>
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
      {pendingNewPhotos.length > 0 ? (
        <div className="grid-refresh-banner" role="status">
          <span>
            {pendingNewPhotos.length} new photo{pendingNewPhotos.length === 1 ? "" : "s"} —{" "}
          </span>
          <button type="button" className="ghost" onClick={() => showPendingNewPhotos()}>
            Show
          </button>
        </div>
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
                  const isSelected = image.id === currentId;
                  const isMulti = multiIds.has(image.id);
                  const tileClass = [
                    "tile",
                    isSelected ? "selected" : "",
                    isMulti ? "multi" : "",
                    image.gridStale ? "grid-stale" : "",
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
                        <span className={`badge ${image.effective_state}`}>
                          {lifecycleLabel(image.effective_state)}
                        </span>
                        {image.has_approved_crop ? <span className="badge crop-approved">crop</span> : null}
                        {image.blur_unlock_message ? (
                          <span className="badge publish-variant">{image.blur_unlock_message}</span>
                        ) : image.publish_variant_label ? (
                          <span className="badge publish-variant">{image.publish_variant_label}</span>
                        ) : null}
                        {image.has_approved_blur && !image.publish_variant_label ? (
                          <span className="badge crop-approved">blur</span>
                        ) : null}
                        {image.gemini_label_status?.startsWith("blocked:") ? (
                          <span className="badge gemini-blocked" title="Gemini blocked captions — review this photo">
                            Gemini blocked: {image.gemini_label_status.replace("blocked:", "")} — captions not
                            generated, review this photo
                          </span>
                        ) : null}
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
        <div ref={sentinelRef} className="grid-sentinel" aria-hidden />
        {listExhausted && items.length > 0 ? (
          <p className="grid-end-marker">End of library · {items.length} photos</p>
        ) : null}
      </div>
      <p className="help">
        Keys: 1 reject, 2 review, 3 safe, 4 excellent, arrows move (→ next / forward, ← or Z or Backspace back), space
        classify, C crop, B blur, P queue, G captions. ⌘/Ctrl-click multi-select, Shift-click range. In classify: 1–4 rate
        and advance, C/B prep tools, Queue / Schedule / Post now panel, Esc back/close.
        {multiActive ? ` · ${multiIds.size} selected for classify` : ""}
      </p>
      {preview && current ? (
        <div className="overlay" role="presentation" onClick={closeClassify}>
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
                  ? ` · ${(currentId ? classifyIds.indexOf(currentId) : -1) + 1}/${classifyIds.length} selected`
                  : ` · ${(currentId ? classifyIds.indexOf(currentId) : selectedIndex) + 1}/${items.length}`}
                {ratingBusy ? " · saving…" : ""}
              </p>
              <div className="classify-modes">
                {(
                  [
                    ["rate", "Rate"],
                    ["crop", "Crop"],
                    ["blur", "Blur"],
                  ] as const
                ).map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    className={prepMode === mode ? "ghost active" : "ghost"}
                    onClick={() => setPrepMode(mode)}
                  >
                    {mode === "crop" ? "C " : mode === "blur" ? "B " : ""}
                    {label}
                  </button>
                ))}
              </div>
              <button type="button" className="ghost" disabled={navStacks.back.length === 0} onClick={() => navigateBack()}>
                ← Back
              </button>
              <button
                type="button"
                className="ghost"
                disabled={navStacks.forward.length === 0}
                onClick={() => navigateForward()}
              >
                Forward →
              </button>
              <button type="button" className="ghost" onClick={closeClassify}>
                Close
              </button>
            </div>
            {prepMode === "rate" ? (
              <div className="classify-stage">
                {current.has_approved_blur && current.original_preview_url ? (
                  <label className="show-original-toggle">
                    <input
                      type="checkbox"
                      checked={showOriginalPreview}
                      onChange={(event) => setShowOriginalPreview(event.target.checked)}
                    />
                    Show original (editing)
                  </label>
                ) : null}
                {displayPreviewUrl(current, showOriginalPreview) ? (
                  <img
                    key={`${current.id}-${showOriginalPreview ? "orig" : "blur"}`}
                    src={displayPreviewUrl(current, showOriginalPreview) ?? ""}
                    alt=""
                  />
                ) : (
                  <span className="tile-empty">No preview</span>
                )}
                {current.publish_variant_label || publishVariantLabel(current.publish_variant) ? (
                  <p className="muted publish-variant-caption">
                    {current.publish_variant_label ?? publishVariantLabel(current.publish_variant)}
                  </p>
                ) : null}
              </div>
            ) : prepMode === "crop" ? (
              <CropPanel
                key={`crop-${current.id}`}
                image={current}
                onApproved={(updated) => {
                  patchImage(updated);
                  setPrepMode("rate");
                }}
                onStatus={setMessage}
              />
            ) : (
              <BlurPanel
                key={`blur-${current.id}`}
                image={current}
                onChanged={(imageId) => {
                  void api<ImageItem>(`/api/images/${imageId}`)
                    .then((refreshed) => patchImage(refreshed))
                    .catch(() => {
                      /* keep local row if refresh fails */
                    });
                }}
                onStatus={setMessage}
              />
            )}
            <ClassifyPublishPanel
              image={current}
              geminiPolicy={config.data?.gemini_policy ?? "text_only"}
              onImageUpdated={(updated) => patchImage(updated)}
              onStatus={setMessage}
            />
            <div className="classify-toolbar">
              <div className="classify-ratings">
                {RATING_KEYS.map(({ key, rating, label }) => (
                  <button
                    key={rating}
                    type="button"
                    className={current.human_rating === rating ? "button active-rating" : "ghost"}
                    disabled={ratingBusy}
                    onClick={() => {
                      setPrepMode("rate");
                      void rateAndAdvance(rating);
                    }}
                  >
                    {key} {label}
                  </button>
                ))}
              </div>
              <p className="classify-meta">
                model {current.model_state ?? "unreviewed"} · rating {current.human_rating ?? "none"}
                {current.has_approved_crop ? " · crop ✓" : ""}
                {current.has_gps ? " · GPS on original only" : ""}
                {prepMode !== "rate" ? " · Esc → rate" : ""}
              </p>
            </div>
          </div>
        </div>
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
  onPeekLibraryHead,
  onLibraryRefresh,
}: {
  libraryId: string;
  imageCount: number;
  detectorAvailable: boolean;
  safetyClassifier: string;
  onPeekLibraryHead: () => void;
  onLibraryRefresh: () => void;
}) {
  const [job, setJob] = useState<JobItem | null>(null);
  const [recent, setRecent] = useState<JobItem[]>([]);
  const [queues, setQueues] = useState<QueuesStatus | null>(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const onPeekRef = useRef(onPeekLibraryHead);
  const onLibraryRef = useRef(onLibraryRefresh);
  onPeekRef.current = onPeekLibraryHead;
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
          onPeekRef.current();
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
          onPeekRef.current();
          onLibraryRef.current();
        }
        if (watchActive && !still) {
          onPeekRef.current();
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
      if (!scanActive(visible.status)) onPeekRef.current();
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
