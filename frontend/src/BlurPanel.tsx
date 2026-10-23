import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import {
  type BlurRect,
  type BlurResizeMode,
  moveBlur,
  resizeBlur,
  snapRotation,
} from "./blurGeometry";
import { compositeBlurCoversOnCanvas, type ClientBlurCover } from "./blurClientPreview";
import { defaultEdgeMode } from "./blurEdgeMode";
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
  unbounded = false,
): { x: number; y: number } {
  const rect = frame.getBoundingClientRect();
  const px = (clientX - rect.left - layout.offsetX) / layout.scale;
  const py = (clientY - rect.top - layout.offsetY) / layout.scale;
  if (unbounded) {
    return { x: Math.round(px), y: Math.round(py) };
  }
  return {
    x: Math.min(Math.max(0, Math.round(px)), imageW),
    y: Math.min(Math.max(0, Math.round(py)), imageH),
  };
}

function blurToRect(blur: BlurItem): BlurRect {
  return {
    x: blur.x,
    y: blur.y,
    width: blur.width,
    height: blur.height,
    rotation_deg: blur.rotation_deg ?? 0,
  };
}

export function blurVerificationLabel(
  image: Pick<ImageItem, "model_state" | "blur_unlock_status" | "blur_unlock_message">,
  { verifying, hasCovers, approved }: { verifying: boolean; hasCovers: boolean; approved: boolean },
): { tone: "ok" | "pending" | "error" | "info"; text: string } | null {
  if (verifying) return { tone: "pending", text: "Verifying blurred render…" };
  if (image.model_state === "DO_NOT_POST") {
    switch (image.blur_unlock_status) {
      case "verified":
        // The server only reports SAFE · BLURRED while the verified geometry still matches.
        if ((image.blur_unlock_message ?? "").startsWith("SAFE · BLURRED")) {
          return { tone: "ok", text: "SAFE · BLURRED (verified) — only the blurred version can be published." };
        }
        return { tone: "error", text: "Covers changed since verification — approve (Enter/A) to re-verify. Still blocked." };
      case "pending":
        return { tone: "pending", text: "Verifying blurred render…" };
      case "failed":
        return {
          tone: "error",
          text: `Verification failed — ${image.blur_unlock_message || "flagged areas are not covered"}. Still blocked.`,
        };
      default:
        return hasCovers
          ? { tone: "info", text: "Not verified yet — approve (Enter/A) to verify the blurred render." }
          : { tone: "info", text: "Blocked (DO_NOT_POST) — add covers over the flagged areas, then approve to verify." };
    }
  }
  if (approved) return { tone: "ok", text: "Covers approved — only the blurred version will be published." };
  if (hasCovers) return { tone: "info", text: "Covers not approved yet — Enter/A approves the set." };
  return null;
}

export function BlurPanel({ image, captureKeys = true, onChanged, onStatus }: Props) {
  const queryClient = useQueryClient();
  const blurs = useQuery({
    queryKey: ["blurs", image.id],
    queryFn: () => api<BlurItem[]>(`/api/images/${image.id}/blurs`),
  });
  const [strength, setStrength] = useState("standard");
  const [shapeMode, setShapeMode] = useState<"ellipse" | "rectangle">("ellipse");
  const [cornerRadius, setCornerRadius] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<BlurRect | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<CropRect | null>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [sharpReady, setSharpReady] = useState(false);
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
  const sharpSrc = image.original_preview_url ?? image.preview_url ?? "";
  const approved = items.some((item) => item.approved);
  const isDoNotPost = image.model_state === "DO_NOT_POST";
  const [verifying, setVerifying] = useState(false);

  function notifyChanged() {
    void queryClient.invalidateQueries({ queryKey: ["publish-readiness", image.id] });
    onChanged?.(image.id);
  }

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
  }, [image.id, image.width, image.height, image.preview_url, previewUrl, sharpSrc]);

  function liveCovers(): ClientBlurCover[] {
    return items.map((blur) => {
      const live =
        editDraft && selectedId === blur.id
          ? editDraft
          : { x: blur.x, y: blur.y, width: blur.width, height: blur.height, rotation_deg: blur.rotation_deg ?? 0 };
      return {
        x: live.x,
        y: live.y,
        width: live.width,
        height: live.height,
        shape: blur.shape,
        rotation_deg: live.rotation_deg,
        corner_radius: blur.corner_radius ?? 0,
        feather: blur.feather,
        radius: blur.radius,
        edge_mode: blur.edge_mode ?? defaultEdgeMode(blur.shape),
      };
    });
  }

  useEffect(() => {
    const img = imgRef.current;
    const canvas = canvasRef.current;
    if (!img || !canvas || !sharpReady || !image.width || !image.height) return;
    canvas.width = image.width;
    canvas.height = image.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(img, 0, 0, image.width, image.height);
    if (items.length > 0) {
      compositeBlurCoversOnCanvas(ctx, liveCovers());
    }
  }, [items, editDraft, selectedId, sharpReady, image.width, image.height, image.id]);

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
      notifyChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update blur strength.");
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    setBusy(true);
    setVerifying(isDoNotPost);
    setError("");
    try {
      await api(`/api/images/${image.id}/blurs/approve`, { method: "POST" });
      await blurs.refetch();
      notifyChanged();
      onStatus?.(
        isDoNotPost
          ? image.blur_unlock_message ?? "Blur verification finished — check result and rate safe when ready."
          : "Blur covers approved — rate 3/4 when ready.",
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not approve blur covers.");
    } finally {
      setVerifying(false);
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
      notifyChanged();
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
        const rect = blurToRect(item);
        void patchBlur(item.id, moveBlur(rect, dx, dy));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [captureKeys, isDoNotPost, image.id]);

  async function patchBlur(
    blurId: string,
    patch: Partial<BlurRect> & { edge_mode?: "hard" | "soft" },
  ) {
    setBusy(true);
    setError("");
    try {
      await api(`/api/images/${image.id}/blurs/${blurId}`, { method: "PATCH", body: JSON.stringify(patch) });
      await blurs.refetch();
      notifyChanged();
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
      notifyChanged();
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
          edge_mode: defaultEdgeMode(shapeMode),
          reason: shapeMode === "rectangle" ? "Manual rectangle cover." : "Manual ellipse cover.",
        }),
      });
      setDraft(null);
      await blurs.refetch();
      notifyChanged();
      onStatus?.("Blur region added — approve when covers look right.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add a blur region.");
    } finally {
      setBusy(false);
    }
  }

  function beginBlurDrag(blur: BlurItem, mode: BlurResizeMode, event: ReactPointerEvent<HTMLElement>) {
    if (!layout || !image.width || !image.height || busy) return;
    event.preventDefault();
    event.stopPropagation();
    const origin = blurToRect(blur);
    const pointerId = event.pointerId;
    const startX = event.clientX;
    const startY = event.clientY;
    const scale = layout.scale;
    setEditDraft(origin);
    setSelectedId(blur.id);

    function onMove(ev: PointerEvent) {
      if (ev.pointerId !== pointerId) return;
      const dx = (ev.clientX - startX) / scale;
      const dy = (ev.clientY - startY) / scale;
      setEditDraft(resizeBlur(origin, mode, dx, dy));
    }
    async function onUp(ev: PointerEvent) {
      if (ev.pointerId !== pointerId) return;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      setEditDraft((current) => {
        if (current) void patchBlur(blur.id, current);
        return null;
      });
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }

  function beginRotate(blur: BlurItem, event: ReactPointerEvent<HTMLElement>) {
    if (!layout || !image.width || !image.height || !frameRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    const frame = frameRef.current;
    const activeLayout = layout;
    const origin = blurToRect(blur);
    const cx = origin.x + origin.width / 2;
    const cy = origin.y + origin.height / 2;
    const center = clientToImage(event.clientX, event.clientY, frame, activeLayout, image.width, image.height);
    const startAngle = Math.atan2(center.y - cy, center.x - cx);
    const pointerId = event.pointerId;

    function onMove(moveEv: PointerEvent) {
      if (moveEv.pointerId !== pointerId) return;
      const pt = clientToImage(moveEv.clientX, moveEv.clientY, frame, activeLayout, image.width!, image.height!);
      let deg = origin.rotation_deg + (Math.atan2(pt.y - cy, pt.x - cx) - startAngle) * (180 / Math.PI);
      deg = snapRotation(Math.max(-180, Math.min(180, deg)), moveEv.shiftKey);
      setEditDraft({ ...origin, rotation_deg: deg });
    }
    async function onUp(moveEv: PointerEvent) {
      if (moveEv.pointerId !== pointerId) return;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setEditDraft((current) => {
        if (current) void patchBlur(blur.id, { rotation_deg: current.rotation_deg });
        return null;
      });
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  function beginDraw(event: ReactPointerEvent<HTMLDivElement>) {
    if (!layout || !image.width || !image.height || !frameRef.current || busy) return;
    if ((event.target as HTMLElement).closest(".blur-handle, .blur-ellipse")) return;
    event.preventDefault();
    const origin = clientToImage(
      event.clientX,
      event.clientY,
      frameRef.current,
      layout,
      image.width,
      image.height,
      true,
    );
    const pointerId = event.pointerId;
    drawRef.current = { pointerId, originX: origin.x, originY: origin.y };
    setDraft({ x: origin.x, y: origin.y, width: 1, height: 1 });
    setError("");

    function onMove(ev: PointerEvent) {
      if (!drawRef.current || ev.pointerId !== pointerId || !frameRef.current || !layout) return;
      const point = clientToImage(
        ev.clientX,
        ev.clientY,
        frameRef.current,
        layout,
        image.width!,
        image.height!,
        true,
      );
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
      const point = clientToImage(
        ev.clientX,
        ev.clientY,
        frameRef.current,
        layout,
        image.width,
        image.height,
        true,
      );
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
  const useClientPreview = items.length > 0 && Boolean(sharpSrc);
  const displaySrc = useClientPreview ? sharpSrc : previewUrl ?? image.preview_url ?? "";
  const selectedBlur = selectedId ? items.find((row) => row.id === selectedId) : null;

  return (
    <div className="prep-panel">
      <div
        className="preview-frame crop-stage prep-stage blur-draw-stage"
        ref={frameRef}
        onPointerDown={beginDraw}
      >
        {displaySrc ? (
          <>
            <img
              ref={imgRef}
              src={displaySrc}
              alt=""
              onLoad={() => {
                measureLayout();
                setSharpReady(true);
              }}
              draggable={false}
              className={useClientPreview ? "blur-sharp-source" : undefined}
            />
            {useClientPreview ? (
              <canvas ref={canvasRef} className="blur-live-canvas" aria-hidden="true" />
            ) : null}
          </>
        ) : null}
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
        {items.map((blur) => {
          if (!image.width || !image.height || !layout) return null;
          const live =
            editDraft && selectedId === blur.id
              ? editDraft
              : { x: blur.x, y: blur.y, width: blur.width, height: blur.height, rotation_deg: blur.rotation_deg ?? 0 };
          const css = cropToCss({ x: live.x, y: live.y, width: live.width, height: live.height }, layout);
          const rot = live.rotation_deg;
          const radiusPx =
            blur.shape === "ellipse"
              ? "50%"
              : `${Math.max(0, (blur.corner_radius ?? 0) * layout.scale)}px`;
          const selected = selectedId === blur.id;
          const handles: BlurResizeMode[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
          return (
            <div
              key={blur.id}
              className={
                blur.approved ? "blur-ellipse approved interactive" : selected ? "blur-ellipse selected interactive" : "blur-ellipse interactive"
              }
              style={{
                left: css.left,
                top: css.top,
                width: css.width,
                height: css.height,
                transform: `rotate(${rot}deg)`,
                transformOrigin: "center center",
                borderRadius: radiusPx,
                opacity: blur.approved ? 1 : selected ? 1 : 0.92,
              }}
              onPointerDown={(ev) => {
                if ((ev.target as HTMLElement).closest(".blur-handle")) return;
                setSelectedId(blur.id);
                beginBlurDrag(blur, "move", ev);
              }}
            >
              {selected ? (
                <>
                  {handles.map((corner) => (
                    <span
                      key={corner}
                      className={`blur-handle resize-handle ${corner}`}
                      aria-hidden="true"
                      onPointerDown={(ev) => beginBlurDrag(blur, corner, ev)}
                    />
                  ))}
                  <button
                    type="button"
                    className="blur-handle rotate-handle"
                    aria-label="Rotate blur"
                    onPointerDown={(ev) => beginRotate(blur, ev)}
                  />
                </>
              ) : null}
            </div>
          );
        })}
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
        {(() => {
          const label = blurVerificationLabel(image, { verifying, hasCovers: items.length > 0, approved });
          if (!label) return null;
          return (
            <p
              className={label.tone === "error" ? "error blur-verify-state" : "help blur-verify-state"}
              data-testid="blur-verify-state"
              role={label.tone === "error" ? "alert" : "status"}
            >
              <strong>{label.text}</strong>
            </p>
          );
        })()}
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
          {selectedBlur ? (
            <label>
              Edge{" "}
              <select
                value={selectedBlur.edge_mode ?? defaultEdgeMode(selectedBlur.shape)}
                disabled={busy}
                onChange={(event) => {
                  const next = event.target.value as "hard" | "soft";
                  void patchBlur(selectedBlur.id, { edge_mode: next });
                }}
              >
                <option value="hard">Hard edge</option>
                <option value="soft">Soft edge</option>
              </select>
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
                {blur.shape} · {blur.edge_mode ?? defaultEdgeMode(blur.shape)} · σ {blur.radius.toFixed(1)} · feather{" "}
                {blur.feather}px
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
