import { useQuery } from "@tanstack/react-query";
import { PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import { containedLayout, cropToCss, type ContainedLayout, type CropRect } from "./cropGeometry";
import type { BlurItem, ImageItem } from "./types";

type Props = {
  image: ImageItem;
  captureKeys?: boolean;
  onChanged?: (imageId: string) => void;
  onStatus?: (message: string) => void;
};

function clientToImage(
  clientX: number,
  clientY: number,
  frame: HTMLElement,
  layout: ContainedLayout,
  imageW: number,
  imageH: number,
): { x: number; y: number } {
  const rect = frame.getBoundingClientRect();
  const px = (clientX - rect.left - layout.offsetX) / layout.scale;
  const py = (clientY - rect.top - layout.offsetY) / layout.scale;
  return {
    x: Math.min(Math.max(0, Math.round(px)), imageW),
    y: Math.min(Math.max(0, Math.round(py)), imageH),
  };
}

export function BlurPanel({ image, captureKeys = true, onChanged, onStatus }: Props) {
  const blurs = useQuery({
    queryKey: ["blurs", image.id],
    queryFn: () => api<BlurItem[]>(`/api/images/${image.id}/blurs`),
  });
  const [strength, setStrength] = useState("standard");
  const [shapeMode, setShapeMode] = useState<"ellipse" | "rectangle">("ellipse");
  const [cornerRadius, setCornerRadius] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<CropRect | null>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const [layout, setLayout] = useState<ContainedLayout | null>(null);
  const drawRef = useRef<{
    pointerId: number;
    originX: number;
    originY: number;
  } | null>(null);
  const busyRef = useRef(busy);
  const itemsLenRef = useRef(0);
  busyRef.current = busy;

  const items = blurs.data ?? [];
  itemsLenRef.current = items.length;
  const previewUrl = items.find((item) => item.preview_url)?.preview_url ?? null;
  const approved = items.some((item) => item.approved);
  const isDoNotPost = image.model_state === "DO_NOT_POST";

  useEffect(() => {
    setDraft(null);
    setError("");
  }, [image.id]);

  useEffect(() => {
    const current = blurs.data?.[0]?.strength;
    if (current) setStrength(current);
  }, [blurs.data]);

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
  }, [image.id, image.width, image.height, image.preview_url, previewUrl]);

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
      onChanged?.(image.id);
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
      onChanged?.(image.id);
      onStatus?.(
        isDoNotPost
          ? image.blur_unlock_message ?? "Blur verification finished — check result and rate safe when ready."
          : "Blur covers approved — rate 3/4 when ready.",
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not approve blur covers.");
    } finally {
      setBusy(false);
    }
  }

  async function suggestFromModel() {
    setBusy(true);
    setError("");
    try {
      await api(`/api/images/${image.id}/blurs/suggest`, {
        method: "POST",
        body: JSON.stringify({ strength }),
      });
      await blurs.refetch();
      onChanged?.(image.id);
      onStatus?.("Suggested covers from local model — review and approve.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not suggest blur covers.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!captureKeys) return;
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.key === "Enter" || event.key.toLowerCase() === "a") {
        if (!busyRef.current && itemsLenRef.current > 0) {
          event.preventDefault();
          void approve();
        }
      }
      if (event.key === "Delete" || event.key === "Backspace") {
        if (selectedId && !busyRef.current) {
          event.preventDefault();
          void deleteBlur(selectedId);
        }
      }
      const step = event.shiftKey ? 10 : 1;
      if (selectedId && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
        event.preventDefault();
        const item = items.find((row) => row.id === selectedId);
        if (!item) return;
        const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
        const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
        void patchBlur(item.id, { x: item.x + dx, y: item.y + dy });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [captureKeys, isDoNotPost, image.id]);

  async function patchBlur(blurId: string, patch: Record<string, unknown>) {
    setBusy(true);
    setError("");
    try {
      await api(`/api/images/${image.id}/blurs/${blurId}`, { method: "PATCH", body: JSON.stringify(patch) });
      await blurs.refetch();
      onChanged?.(image.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update blur region.");
    } finally {
      setBusy(false);
    }
  }

  async function deleteBlur(blurId: string) {
    setBusy(true);
    setError("");
    try {
      await api(`/api/images/${image.id}/blurs/${blurId}`, { method: "DELETE" });
      setSelectedId(null);
      await blurs.refetch();
      onChanged?.(image.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete blur region.");
    } finally {
      setBusy(false);
    }
  }

  async function createRegion(rect: CropRect) {
    if (!image.width || !image.height) return;
    const clamped = {
      ...rect,
      width: Math.max(8, rect.width),
      height: Math.max(8, rect.height),
    };
    if (clamped.width < 8 || clamped.height < 8) {
      setError("Drag a larger region to blur.");
      setDraft(null);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(`/api/images/${image.id}/blurs`, {
        method: "POST",
        body: JSON.stringify({
          x: clamped.x,
          y: clamped.y,
          width: clamped.width,
          height: clamped.height,
          strength,
          shape: shapeMode,
          corner_radius: shapeMode === "rectangle" ? cornerRadius : 0,
          rotation_deg: 0,
          reason: shapeMode === "rectangle" ? "Manual rectangle cover." : "Manual ellipse cover.",
        }),
      });
      setDraft(null);
      await blurs.refetch();
      onChanged?.(image.id);
      onStatus?.("Blur region added — approve when covers look right.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add a blur region.");
    } finally {
      setBusy(false);
    }
  }

  function beginDraw(event: ReactPointerEvent<HTMLDivElement>) {
    if (!layout || !image.width || !image.height || !frameRef.current || busy) return;
    if ((event.target as HTMLElement).closest(".blur-handle")) return;
    event.preventDefault();
    const origin = clientToImage(event.clientX, event.clientY, frameRef.current, layout, image.width, image.height);
    const pointerId = event.pointerId;
    drawRef.current = { pointerId, originX: origin.x, originY: origin.y };
    setDraft({ x: origin.x, y: origin.y, width: 1, height: 1 });
    setError("");

    function onMove(ev: PointerEvent) {
      if (!drawRef.current || ev.pointerId !== pointerId || !frameRef.current || !layout) return;
      const point = clientToImage(ev.clientX, ev.clientY, frameRef.current, layout, image.width!, image.height!);
      const x = Math.min(drawRef.current.originX, point.x);
      const y = Math.min(drawRef.current.originY, point.y);
      const width = Math.max(1, Math.abs(point.x - drawRef.current.originX));
      const height = Math.max(1, Math.abs(point.y - drawRef.current.originY));
      setDraft({ x, y, width, height });
    }
    function onUp(ev: PointerEvent) {
      if (!drawRef.current || ev.pointerId !== pointerId) return;
      const start = drawRef.current;
      drawRef.current = null;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      if (!frameRef.current || !layout || !image.width || !image.height) return;
      const point = clientToImage(ev.clientX, ev.clientY, frameRef.current, layout, image.width, image.height);
      const rect = {
        x: Math.min(start.originX, point.x),
        y: Math.min(start.originY, point.y),
        width: Math.max(1, Math.abs(point.x - start.originX)),
        height: Math.max(1, Math.abs(point.y - start.originY)),
      };
      void createRegion(rect);
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }

  const draftCss = draft && layout ? cropToCss(draft, layout) : null;
  const displaySrc = previewUrl ?? image.preview_url ?? "";

  return (
    <div className="prep-panel">
      <div
        className="preview-frame crop-stage prep-stage blur-draw-stage"
        ref={frameRef}
        onPointerDown={beginDraw}
      >
        {displaySrc ? <img ref={imgRef} src={displaySrc} alt="" onLoad={measureLayout} draggable={false} /> : null}
        {(image.blur_unlock_highlights ?? []).map((region, index) =>
          image.width && image.height && layout ? (
            <div
              key={`highlight-${index}`}
              className="blur-ellipse highlight-unsafe"
              style={(() => {
                const css = cropToCss(
                  { x: region.x, y: region.y, width: region.width, height: region.height },
                  layout,
                );
                return { left: css.left, top: css.top, width: css.width, height: css.height };
              })()}
            />
          ) : null,
        )}
        {items.map((blur) =>
          image.width && image.height && layout ? (
            <div
              key={blur.id}
              className={
                blur.approved
                  ? "blur-ellipse approved"
                  : selectedId === blur.id
                    ? "blur-ellipse selected"
                    : "blur-ellipse"
              }
              onPointerDown={(ev) => {
                ev.stopPropagation();
                setSelectedId(blur.id);
              }}
              style={(() => {
                const css = cropToCss(
                  { x: blur.x, y: blur.y, width: blur.width, height: blur.height },
                  layout,
                );
                const rot = blur.rotation_deg ?? 0;
                const radiusPx =
                  blur.shape === "ellipse"
                    ? "50%"
                    : `${Math.max(0, (blur.corner_radius ?? 0) * layout.scale)}px`;
                return {
                  left: css.left,
                  top: css.top,
                  width: css.width,
                  height: css.height,
                  transform: `rotate(${rot}deg)`,
                  transformOrigin: "center center",
                  borderRadius: radiusPx,
                };
              })()}
            >
              {selectedId === blur.id ? (
                <>
                <button
                  type="button"
                  className="blur-handle resize-handle se"
                  aria-label="Resize blur"
                  onPointerDown={(ev) => {
                    ev.stopPropagation();
                    ev.preventDefault();
                    const frame = frameRef.current;
                    if (!frame || !layout || !image.width || !image.height) return;
                    const activeLayout = layout;
                    function onMove(moveEv: PointerEvent) {
                      const pt = clientToImage(
                        moveEv.clientX,
                        moveEv.clientY,
                        frame!,
                        activeLayout,
                        image.width!,
                        image.height!,
                      );
                      void patchBlur(blur.id, {
                        width: Math.max(8, pt.x - blur.x),
                        height: Math.max(8, pt.y - blur.y),
                      });
                    }
                    function onUp() {
                      window.removeEventListener("pointermove", onMove);
                      window.removeEventListener("pointerup", onUp);
                    }
                    window.addEventListener("pointermove", onMove);
                    window.addEventListener("pointerup", onUp);
                  }}
                />
                <button
                  type="button"
                  className="blur-handle rotate-handle"
                  aria-label="Rotate blur"
                  onPointerDown={(ev) => {
                    ev.stopPropagation();
                    ev.preventDefault();
                    const frame = frameRef.current;
                    if (!frame || !layout || !image.width || !image.height) return;
                    const activeLayout = layout;
                    const center = clientToImage(
                      ev.clientX,
                      ev.clientY,
                      frame,
                      activeLayout,
                      image.width,
                      image.height,
                    );
                    const cx = blur.x + blur.width / 2;
                    const cy = blur.y + blur.height / 2;
                    const startAngle = Math.atan2(center.y - cy, center.x - cx);
                    const startRot = blur.rotation_deg ?? 0;
                    function onMove(moveEv: PointerEvent) {
                      const pt = clientToImage(
                        moveEv.clientX,
                        moveEv.clientY,
                        frame!,
                        activeLayout,
                        image.width!,
                        image.height!,
                      );
                      let deg =
                        startRot + (Math.atan2(pt.y - cy, pt.x - cx) - startAngle) * (180 / Math.PI);
                      if (moveEv.shiftKey) {
                        deg = Math.round(deg / 15) * 15;
                      }
                      void patchBlur(blur.id, { rotation_deg: Math.max(-180, Math.min(180, deg)) });
                    }
                    function onUp() {
                      window.removeEventListener("pointermove", onMove);
                      window.removeEventListener("pointerup", onUp);
                    }
                    window.addEventListener("pointermove", onMove);
                    window.addEventListener("pointerup", onUp);
                  }}
                />
                </>
              ) : null}
            </div>
          ) : null,
        )}
        {draftCss ? (
          <div
            className="blur-ellipse draft"
            style={{
              left: draftCss.left,
              top: draftCss.top,
              width: draftCss.width,
              height: draftCss.height,
            }}
          />
        ) : null}
      </div>
      <div className="prep-controls">
        {isDoNotPost ? (
          <p className="help">
            <strong>Blur to unlock.</strong> Draw or suggest covers, approve to run verification (coverage +
            re-classify on the blurred render). Crop stays disabled until unlocked.
          </p>
        ) : (
          <p className="help">
            Drag to add a cover, or suggest ellipses from the local safety boxes. Derivatives only.
            Enter/A approves the set.
          </p>
        )}
        {image.blur_unlock_message ? (
          <p className={image.blur_unlock_status === "verified" ? "help" : "error"}>{image.blur_unlock_message}</p>
        ) : null}
        <div className="row">
          <label>
            Shape{" "}
            <select
              value={shapeMode}
              disabled={busy}
              onChange={(event) => setShapeMode(event.target.value as "ellipse" | "rectangle")}
            >
              <option value="ellipse">Ellipse</option>
              <option value="rectangle">Rectangle</option>
            </select>
          </label>
          {shapeMode === "rectangle" ? (
            <label>
              Corner radius{" "}
              <input
                type="number"
                min={0}
                max={200}
                value={cornerRadius}
                disabled={busy}
                onChange={(event) => setCornerRadius(Number(event.target.value) || 0)}
              />
            </label>
          ) : null}
          <label>
            Strength{" "}
            <select
              value={strength}
              disabled={busy}
              onChange={(event) => {
                const next = event.target.value;
                if (items.length === 0) {
                  setStrength(next);
                  return;
                }
                void applyStrength(next);
              }}
            >
              <option value="subtle">Subtle</option>
              <option value="standard">Standard</option>
              <option value="strong">Strong</option>
            </select>
          </label>
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => void suggestFromModel()}
          >
            Suggest from model
          </button>
          <button
            type="button"
            className="button"
            disabled={busy || items.length === 0}
            onClick={() => void approve()}
          >
            {busy ? "Saving…" : approved ? "Approved" : "Approve covers"}
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
              </li>
            ))}
          </ul>
        ) : (
          <p className="help">
            {blurs.isLoading
              ? "Loading…"
              : "No covers yet — drag a region, or Suggest from model when boxes exist."}
          </p>
        )}
      </div>
    </div>
  );
}
