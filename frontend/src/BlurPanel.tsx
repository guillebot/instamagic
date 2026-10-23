import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "./api";
import { createProbeQueue } from "./blurDetectorQueue";
import { createSaveQueue } from "./blurSaveQueue";
import {
  type BlurRect,
  type BlurResizeMode,
  moveBlur,
  resizeBlur,
  snapRotation,
} from "./blurGeometry";
import { defaultEdgeMode } from "./blurEdgeMode";
import { containedLayout, cropToCss, type ContainedLayout, type CropRect } from "./cropGeometry";
import type {
  BlurCoverageRegion,
  BlurCoverageReport,
  BlurDetectorProbe,
  BlurItem,
  ImageItem,
} from "./types";

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

/** CSS border-radius for a cover overlay (saved cover or the in-progress draft). */
export function coverBorderRadius(shape: string | null | undefined, cornerRadius: number | null | undefined, scale: number): string {
  if (shape === "rectangle") return `${Math.max(0, (cornerRadius ?? 0) * scale)}px`;
  return "50%";
}

export function blurVerificationLabel(
  image: Pick<ImageItem, "model_state" | "blur_unlock_status" | "blur_unlock_message">,
  {
    verifying,
    hasCovers,
    approved,
    dirty = false,
  }: { verifying: boolean; hasCovers: boolean; approved: boolean; dirty?: boolean },
): { tone: "ok" | "pending" | "error" | "info"; text: string } | null {
  if (verifying) return { tone: "pending", text: "Verifying blurred render…" };
  // Any move/resize/rotate invalidates the previous verification until it is saved,
  // re-rendered and approved again: never show a verified label for old geometry.
  if (dirty && hasCovers) {
    return {
      tone: "pending",
      text: "Blur changed — re-rendering… (approve again to re-verify)",
    };
  }
  if (image.model_state === "DO_NOT_POST") {
    switch (image.blur_unlock_status) {
      case "verified":
        // The server only reports SAFE · BLURRED while the verified geometry still matches,
        // and every current cover must still be approved (edits clear approval).
        if (approved && (image.blur_unlock_message ?? "").startsWith("SAFE · BLURRED")) {
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

const pct = (value: number) => `${Math.floor(value * 100)}%`;

function prettyLabel(label: string): string {
  return label.replace(/_EXPOSED$/, "").replace(/^FEMALE_|^MALE_/, "").replace(/_/g, " ").toLowerCase();
}

/** Which side(s) of a flagged box the cover misses, e.g. "extend ~27px up". */
export function missedHint(region: Pick<BlurCoverageRegion, "missed_px">): string {
  const missed = region.missed_px;
  if (!missed) return "";
  const sides: Array<[number, string]> = [
    [missed.top, "up"],
    [missed.bottom, "down"],
    [missed.left, "left"],
    [missed.right, "right"],
  ];
  const parts = sides.filter(([px]) => px > 0).map(([px, dir]) => `~${px}px ${dir}`);
  return parts.length ? `extend ${parts.join(", ")}` : "solid core too thin here — enlarge or lower feather";
}

function thresholdPercent(report: BlurCoverageReport): number {
  return report.coverage_threshold_percent ?? Math.round(report.min_coverage * 100);
}

/** Summary line for the coverage gate vs original flagged boxes. */
export function coverageVsOriginalLine(
  report: BlurCoverageReport,
): { tone: "ok" | "error" | "info"; text: string } {
  const need = thresholdPercent(report);
  const total = report.regions.length;
  if (total === 0) {
    return { tone: "info", text: "Coverage vs original flagged areas: none from local model." };
  }
  const ok = report.regions.filter((row) => row.covered).length;
  if (ok === total) {
    return { tone: "ok", text: `Coverage vs original flagged areas: ${ok}/${total} ✓ (needs ${need}%)` };
  }
  const detail = report.regions
    .filter((row) => !row.covered)
    .map((row) => `${prettyLabel(row.label)} ${pct(row.coverage)} (${missedHint(row)})`)
    .join("; ");
  return {
    tone: "error",
    text: `Coverage vs original flagged areas: ${ok}/${total} ✗ (needs ${need}%) — ${detail}`,
  };
}

/** Live detector probe line (read-only; unlock still requires approve). */
export function detectorProbeLine(
  result: BlurDetectorProbe | null | undefined,
  probing: boolean,
): { tone: "ok" | "error" | "info" | "pending"; text: string } {
  if (probing) return { tone: "pending", text: "Detector on blurred image: running…" };
  if (!result) return { tone: "info", text: "Detector on blurred image: not run yet." };
  if (result.passed) {
    return { tone: "ok", text: `Detector on blurred image: PASS (top score ${result.top_score.toFixed(2)})` };
  }
  const detail = result.detections
    .map((row) => `${prettyLabel(row.label)} ${(row.score * 100).toFixed(0)}%`)
    .join(", ");
  return { tone: "error", text: `Detector on blurred image: FAIL: ${detail}` };
}

/** Per-check lines for the editor (coverage detail + stored verification on approve). */
export function coverageCheckLines(
  report: BlurCoverageReport,
  dirty = false,
): Array<{ tone: "ok" | "error" | "info"; text: string }> {
  const lines: Array<{ tone: "ok" | "error" | "info"; text: string }> = [coverageVsOriginalLine(report)];
  const verification = report.verification;
  if (!report.do_not_post) return lines;
  if (dirty || !verification.current_geometry || verification.recheck === "not_run") {
    lines.push({
      tone: "info",
      text: "Formal unlock: approve (Enter/A) runs coverage + detector together for SAFE · BLURRED.",
    });
  } else if (verification.recheck === "fail") {
    const labels = Array.from(new Set(verification.classifier_detections.map((row) => prettyLabel(row.label))));
    lines.push({ tone: "error", text: `Last approve re-check ✗ — classifier still detects ${labels.join(", ")}.` });
  } else {
    lines.push({ tone: "ok", text: "Last approve re-check ✓ — classifier passed on the blurred render." });
  }
  return lines;
}

export function BlurPanel({ image, captureKeys = true, onChanged, onStatus }: Props) {
  const queryClient = useQueryClient();
  const blurs = useQuery({
    queryKey: ["blurs", image.id],
    queryFn: () => api<BlurItem[]>(`/api/images/${image.id}/blurs`),
  });
  // Live coverage of each flagged area by the covers as saved now (same math as the gate).
  const coverage = useQuery({
    queryKey: ["blur-coverage", image.id],
    queryFn: () => api<BlurCoverageReport>(`/api/images/${image.id}/blur-coverage`),
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
  const isDoNotPost = image.model_state === "DO_NOT_POST";
  const allApproved = items.length > 0 && items.every((item) => item.approved);
  const [verifying, setVerifying] = useState(false);
  const [saving, setSaving] = useState(false);
  const [detectorResult, setDetectorResult] = useState<BlurDetectorProbe | null>(null);
  const [detectorBusy, setDetectorBusy] = useState(false);
  const [loadedSrc, setLoadedSrc] = useState<string | null>(null);
  const itemsRef = useRef<BlurItem[]>(items);
  itemsRef.current = items;
  const selectedIdRef = useRef<string | null>(selectedId);
  selectedIdRef.current = selectedId;

  function notifyChanged() {
    void queryClient.invalidateQueries({ queryKey: ["publish-readiness", image.id] });
    void queryClient.invalidateQueries({ queryKey: ["blur-coverage", image.id] });
    onChanged?.(image.id);
  }
  const notifyRef = useRef(notifyChanged);
  notifyRef.current = notifyChanged;

  // One latest-wins save queue per image: geometry edits are applied optimistically,
  // persisted one request at a time, and server state is re-read only once all settle.
  const probeQueue = useMemo(() => {
    const imageId = image.id;
    return createProbeQueue({
      delayMs: 400,
      onBusyChange: setDetectorBusy,
      run: async () => {
        const data = await api<BlurDetectorProbe>(`/api/images/${imageId}/blur-detector-probe`, { method: "POST" });
        setDetectorResult(data);
      },
    });
  }, [image.id]);

  const probeQueueRef = useRef(probeQueue);
  probeQueueRef.current = probeQueue;

  const saveQueue = useMemo(() => {
    const imageId = image.id;
    return createSaveQueue<Partial<BlurRect>>({
      delayMs: 250,
      send: (blurId, patch) =>
        api(`/api/images/${imageId}/blurs/${blurId}`, { method: "PATCH", body: JSON.stringify(patch) }).then(
          () => undefined,
        ),
      onBusyChange: setSaving,
      onIdle: async () => {
        await queryClient.invalidateQueries({ queryKey: ["blurs", imageId] });
        notifyRef.current();
        if (itemsLenRef.current > 0) {
          probeQueueRef.current.schedule();
        }
      },
      onError: (err) => {
        setError(err instanceof ApiError ? err.message : "Could not update blur region.");
        void queryClient.invalidateQueries({ queryKey: ["blurs", imageId] });
      },
    });
  }, [image.id, queryClient]);

  useEffect(() => {
    return () => {
      // Persist anything still pending when leaving this image, then stop timers.
      void saveQueue.flush().finally(() => saveQueue.dispose());
      probeQueue.dispose();
    };
  }, [saveQueue, probeQueue]);

  useEffect(() => {
    setDetectorResult(null);
  }, [image.id]);

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
      // Approve exactly the geometry on screen: persist pending edits first.
      await saveQueue.flush();
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
      // Read the latest items/selection through refs: this listener is installed once
      // per image, so closing over render-time state would nudge from stale geometry.
      const currentSelected = selectedIdRef.current;
      if (event.key === "Delete" || event.key === "Backspace") {
        if (currentSelected && !busyRef.current) {
          event.preventDefault();
          void deleteBlur(currentSelected);
        }
      }
      const step = event.shiftKey ? 10 : 1;
      if (currentSelected && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
        event.preventDefault();
        if (busyRef.current) return;
        const item = itemsRef.current.find((row) => row.id === currentSelected);
        if (!item) return;
        const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
        const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
        const next = moveBlur(blurToRect(item), dx, dy);
        patchBlur(item.id, { x: next.x, y: next.y });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [captureKeys, isDoNotPost, image.id, saveQueue]);

  /** Optimistically apply an edit and queue a latest-wins save (debounced unless delayMs=0). */
  function patchBlur(
    blurId: string,
    patch: Partial<BlurRect> & { edge_mode?: "hard" | "soft" },
    delayMs?: number,
  ) {
    setError("");
    // Drop any in-flight list refetch so an older server snapshot can't overwrite this edit.
    void queryClient.cancelQueries({ queryKey: ["blurs", image.id] });
    queryClient.setQueryData<BlurItem[]>(["blurs", image.id], (old) =>
      (old ?? []).map((row) =>
        // Any edit clears approval for the whole set (server does the same).
        row.id === blurId ? { ...row, ...patch, approved: false } : { ...row, approved: false },
      ),
    );
    saveQueue.schedule(blurId, patch, delayMs);
  }

  async function deleteBlur(blurId: string) {
    setBusy(true);
    setError("");
    try {
      await saveQueue.flush();
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
    // Saves in flight don't block a new drag: the newest geometry always wins.
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
        if (current) {
          const { x, y, width, height, rotation_deg } = current;
          queueMicrotask(() => patchBlur(blur.id, { x, y, width, height, rotation_deg }, 0));
        }
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
        if (current) {
          const rotation_deg = current.rotation_deg;
          queueMicrotask(() => patchBlur(blur.id, { rotation_deg }, 0));
        }
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
  const baseSrc = image.original_preview_url ?? image.preview_url ?? "";
  // While a cover is being dragged or its save/render is pending, the baked server render is
  // stale: show the unblurred base with a live client-side blur inside each cover instead.
  const dirty = editDraft !== null || saving;
  const displaySrc = !dirty && previewUrl ? previewUrl : baseSrc;
  const renderCurrent = !dirty && Boolean(previewUrl) && loadedSrc === previewUrl;
  const liveBlur = items.length > 0 && !renderCurrent;
  const selectedBlur = selectedId ? items.find((row) => row.id === selectedId) : null;

  useEffect(() => {
    if (items.length === 0 || editDraft !== null || saving) return;
    probeQueue.schedule(300);
  }, [items.length, editDraft, saving, previewUrl, probeQueue]);

  useEffect(() => {
    const img = imgRef.current;
    if (img && img.complete && img.naturalWidth > 0 && img.getAttribute("src") === displaySrc) {
      setLoadedSrc(displaySrc);
    }
  }, [displaySrc]);

  return (
    <div className="prep-panel">
      <div
        className="preview-frame crop-stage prep-stage blur-draw-stage"
        ref={frameRef}
        onPointerDown={beginDraw}
      >
        {displaySrc ? (
          <img
            ref={imgRef}
            src={displaySrc}
            alt=""
            data-testid="blur-stage-img"
            onLoad={(event) => {
              setLoadedSrc(event.currentTarget.getAttribute("src"));
              measureLayout();
            }}
            draggable={false}
          />
        ) : null}
        {(() => {
          if (!image.width || !image.height || !layout) return null;
          const report = coverage.data;
          const regions: Array<{ x: number; y: number; width: number; height: number; covered: boolean; tag: string }> =
            report
              ? report.regions.map((row) => ({
                  ...row,
                  tag: row.covered
                    ? `✓ ${pct(row.coverage)}`
                    : `cover this — ${pct(row.coverage)} (needs ${thresholdPercent(report)}%)`,
                }))
              : (image.blur_unlock_highlights ?? [])
                  .filter((row) => row.kind !== "detection")
                  .map((row) => ({
                    ...row,
                    covered: false,
                    tag: row.coverage !== undefined ? `cover this — ${pct(row.coverage)}` : "cover this",
                  }));
          return regions.map((region, index) => {
            const css = cropToCss({ x: region.x, y: region.y, width: region.width, height: region.height }, layout);
            return (
              <div
                key={`flag-${index}`}
                className={`flag-region ${region.covered ? "covered" : "uncovered"}${dirty ? " stale" : ""}`}
                data-testid="flag-region"
                style={{ left: css.left, top: css.top, width: css.width, height: css.height }}
              >
                <span className="flag-tag">{dirty ? "…" : region.tag}</span>
              </div>
            );
          });
        })()}
        {detectorResult?.detections.map((region, index) => {
          if (!layout) return null;
          const css = cropToCss({ x: region.x, y: region.y, width: region.width, height: region.height }, layout);
          return (
            <div
              key={`det-${index}`}
              className="flag-region detection"
              data-testid="detector-region"
              style={{ left: css.left, top: css.top, width: css.width, height: css.height }}
            />
          );
        })}
        {items.map((blur) => {
          if (!image.width || !image.height || !layout) return null;
          const live =
            editDraft && selectedId === blur.id
              ? editDraft
              : { x: blur.x, y: blur.y, width: blur.width, height: blur.height, rotation_deg: blur.rotation_deg ?? 0 };
          const css = cropToCss({ x: live.x, y: live.y, width: live.width, height: live.height }, layout);
          const rot = live.rotation_deg;
          const radiusPx = coverBorderRadius(blur.shape, blur.corner_radius, layout.scale);
          const selected = selectedId === blur.id;
          const handles: BlurResizeMode[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
          const liveSigma = Math.max(4, Math.round((blur.radius ?? 12) * layout.scale));
          const stateClass = blur.approved ? " approved" : selected ? " selected" : "";
          return (
            <div
              key={blur.id}
              data-testid="blur-region"
              className={`blur-ellipse${stateClass} interactive${liveBlur ? " live-blur" : ""}`}
              style={{
                ...(liveBlur
                  ? {
                      backdropFilter: `blur(${liveSigma}px)`,
                      WebkitBackdropFilter: `blur(${liveSigma}px)`,
                    }
                  : {}),
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
            className={`blur-ellipse draft ${shapeMode}`}
            data-testid="blur-draft"
            data-shape={shapeMode}
            style={{
              left: draftCss.left,
              top: draftCss.top,
              width: draftCss.width,
              height: draftCss.height,
              // The draft must preview the selected shape, not the default ellipse CSS.
              borderRadius: coverBorderRadius(
                shapeMode,
                shapeMode === "rectangle" ? cornerRadius : 0,
                layout?.scale ?? 1,
              ),
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
          const label = blurVerificationLabel(image, {
            verifying,
            hasCovers: items.length > 0,
            approved: allApproved,
            dirty,
          });
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
        {coverage.data && (coverage.data.regions.length > 0 || coverage.data.do_not_post) ? (
          <div className="blur-gate-panel" data-testid="blur-gate-panel">
            <div className="blur-gate-summary" data-testid="blur-gate-summary">
              <p className={`coverage-${coverageVsOriginalLine(coverage.data).tone}`}>
                {coverageVsOriginalLine(coverage.data).text}
              </p>
              <p className={`coverage-${detectorProbeLine(detectorResult, detectorBusy).tone}`}>
                {detectorProbeLine(detectorResult, detectorBusy).text}
              </p>
            </div>
            {isDoNotPost ? (
              <button
                type="button"
                className="button secondary"
                data-testid="blur-detector-probe"
                disabled={busy || saving || items.length === 0 || detectorBusy}
                onClick={() => void probeQueue.runNow()}
              >
                {detectorBusy ? "Testing blurred image…" : "Test blurred image with detector"}
              </button>
            ) : null}
            <ul className="coverage-checks" data-testid="coverage-checks">
              {coverageCheckLines(coverage.data, dirty).map((line, index) => (
                <li key={index} className={`coverage-${line.tone}`}>
                  {line.text}
                </li>
              ))}
              {coverage.data.regions.some((row) => !row.covered) ? (
                <li className="coverage-hint">
                  Red dashed boxes are the flagged areas (from the original photo); each needs ≥
                  {thresholdPercent(coverage.data)}% under the blur. Soft feathered edges only count partly, so
                  the solid part of the cover must reach past the red box — enlarge the cover or lower feather.
                </li>
              ) : null}
            </ul>
          </div>
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
          {selectedBlur ? (
            <label>
              Edge{" "}
              <select
                value={selectedBlur.edge_mode ?? defaultEdgeMode(selectedBlur.shape)}
                disabled={busy}
                onChange={(event) => {
                  const next = event.target.value as "hard" | "soft";
                  patchBlur(selectedBlur.id, { edge_mode: next }, 0);
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
            {busy || saving ? "Saving…" : allApproved ? "Approved" : "Approve covers"}
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
