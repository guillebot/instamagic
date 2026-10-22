import { useQuery } from "@tanstack/react-query";
import { PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import {
  clampCrop,
  containedLayout,
  cropToCss,
  moveCrop,
  parseAspectRatio,
  resizeCropAspect,
  type ContainedLayout,
  type CropRect,
} from "./cropGeometry";
import type { CropItem, ImageItem } from "./types";

export type CropApproved = Pick<ImageItem, "id" | "has_approved_crop" | "model_state" | "effective_state">;

type Props = {
  image: ImageItem;
  /** When true, arrow-key nudge is owned by this panel (classify prep mode). */
  captureKeys?: boolean;
  onApproved: (updated: CropApproved) => void;
  onStatus?: (message: string) => void;
};

export function CropPanel({ image, captureKeys = true, onApproved, onStatus }: Props) {
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
  const draftRef = useRef(draft);
  const shownRef = useRef<CropItem | undefined>(undefined);
  const busyRef = useRef(busy);
  draftRef.current = draft;
  busyRef.current = busy;

  const shown =
    (activeId ? crops.data?.find((crop) => crop.id === activeId) : null) ??
    crops.data?.find((crop) => crop.aspect_ratio === "4:5") ??
    crops.data?.[0];
  shownRef.current = shown;
  const modelState = image.model_state ?? "";
  const isDoNotPost = modelState === "DO_NOT_POST";
  const isSafeAfterCrop = modelState === "SAFE_AFTER_CROP";

  function selectCrop(crop: CropItem) {
    setActiveId(crop.id);
    setDraft({ x: crop.x, y: crop.y, width: crop.width, height: crop.height });
    setError("");
  }

  useEffect(() => {
    setActiveId(null);
    setDraft(null);
    setError("");
  }, [image.id]);

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

  async function approve(crop: CropItem) {
    const currentDraft = draftRef.current;
    if (!currentDraft || !image.width || !image.height || isDoNotPost) return;
    setBusy(true);
    setError("");
    try {
      const rect = clampCrop(currentDraft, image.width, image.height);
      await api<CropItem>(`/api/images/${image.id}/crops/${crop.id}/approve`, {
        method: "POST",
        body: JSON.stringify({
          x: rect.x,
          y: rect.y,
          width: rect.width,
          height: rect.height,
        }),
      });
      await crops.refetch();
      const refreshed = await api<ImageItem>(`/api/images/${image.id}`);
      onApproved({
        id: refreshed.id,
        has_approved_crop: refreshed.has_approved_crop ?? true,
        model_state: refreshed.model_state,
        effective_state: refreshed.effective_state,
      });
      onStatus?.(isSafeAfterCrop ? "Crop approved — rate 3/4 when ready." : "Crop saved — rate 3/4 when ready.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not approve this crop.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!captureKeys) return;
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (!image.width || !image.height) return;
      if (event.key === "Enter" || event.key.toLowerCase() === "a") {
        const crop = shownRef.current;
        if (crop && !busyRef.current && !isDoNotPost) {
          event.preventDefault();
          void approve(crop);
        }
        return;
      }
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [captureKeys, image.width, image.height, isDoNotPost, image.id]);

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
    <div className="prep-panel">
      <div className="preview-frame crop-stage prep-stage" ref={frameRef}>
        {image.preview_url ? (
          <img ref={imgRef} src={image.preview_url} alt="" onLoad={measureLayout} draggable={false} />
        ) : null}
        {boxCss ? (
          <div
            className="crop-box interactive"
            role="slider"
            aria-label="Crop frame"
            aria-valuetext={draft ? `${draft.width}×${draft.height} at ${draft.x},${draft.y}` : undefined}
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
      <div className="prep-controls">
        {isDoNotPost ? (
          <p className="error">DO_NOT_POST — crop cannot override. Rate reject or leave blocked.</p>
        ) : isSafeAfterCrop ? (
          <p className="help">SAFE_AFTER_CROP — approve a crop that excludes the unsafe region, then rate 3/4.</p>
        ) : (
          <p className="help">
            Adjust and save a crop. Model stays {modelState || "REVIEW"}; rating 3/4 marks it safe.
          </p>
        )}
        <div className="row">
          {crops.data?.map((crop) => (
            <button
              key={crop.id}
              type="button"
              className={shown?.id === crop.id ? "ghost active" : "ghost"}
              onClick={() => selectCrop(crop)}
            >
              {crop.aspect_ratio}
              {crop.approved ? " ✓" : ""}
            </button>
          ))}
        </div>
        {shown ? (
          <>
            <p className="help">{shown.reason}</p>
            {draft ? (
              <p className="help">
                {draft.width}×{draft.height} at {draft.x},{draft.y}
                {shown.aspect_ratio !== "original" ? ` · ${shown.aspect_ratio}` : ""}
                {" · arrows nudge · Enter/A approve"}
              </p>
            ) : null}
            {error ? <p className="error">{error}</p> : null}
            <button type="button" className="button" disabled={!canApprove || busy} onClick={() => void approve(shown)}>
              {busy ? "Saving…" : isSafeAfterCrop ? "Approve crop" : "Save crop"}
            </button>
          </>
        ) : (
          <p className="help">{crops.isLoading ? "Loading crops…" : "No crop suggestions yet."}</p>
        )}
      </div>
    </div>
  );
}
