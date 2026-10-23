/** Client-side blur composite (mask shape matches server hard/soft modes). */

import type { BlurEdgeMode } from "./blurEdgeMode";

export type ClientBlurCover = {
  x: number;
  y: number;
  width: number;
  height: number;
  shape: string;
  rotation_deg: number;
  corner_radius: number;
  feather: number;
  radius: number;
  edge_mode?: BlurEdgeMode;
};

function antialiasNorm(minAxis: number, pixels = 1.5): number {
  return pixels / Math.max(minAxis, 1e-6);
}

function localCoords(
  w: number,
  h: number,
  cx: number,
  cy: number,
  thetaRad: number,
): { lx: Float32Array; ly: Float32Array } {
  const lx = new Float32Array(w * h);
  const ly = new Float32Array(w * h);
  const cosT = Math.cos(thetaRad);
  const sinT = Math.sin(thetaRad);
  let i = 0;
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const dx = x - cx;
      const dy = y - cy;
      lx[i] = dx * cosT + dy * sinT;
      ly[i] = -dx * sinT + dy * cosT;
      i += 1;
    }
  }
  return { lx, ly };
}

function normalizedDist(cover: ClientBlurCover, w: number, h: number): { dist: Float32Array; minAxis: number } {
  const cx = cover.x + cover.width / 2;
  const cy = cover.y + cover.height / 2;
  const theta = (cover.rotation_deg * Math.PI) / 180;
  const { lx, ly } = localCoords(w, h, cx, cy, theta);
  const dist = new Float32Array(w * h);
  if (cover.shape === "ellipse") {
    const rx = Math.max(cover.width / 2, 1e-6);
    const ry = Math.max(cover.height / 2, 1e-6);
    for (let i = 0; i < dist.length; i += 1) {
      dist[i] = Math.sqrt((lx[i] / rx) ** 2 + (ly[i] / ry) ** 2);
    }
    return { dist, minAxis: Math.min(rx, ry) };
  }
  const hw = Math.max(cover.width / 2, 1e-6);
  const hh = Math.max(cover.height / 2, 1e-6);
  const r = Math.min(cover.corner_radius, hw, hh);
  if (r <= 0) {
    for (let i = 0; i < dist.length; i += 1) {
      dist[i] = Math.max(Math.abs(lx[i]) / hw, Math.abs(ly[i]) / hh);
    }
    return { dist, minAxis: Math.min(hw, hh) };
  }
  const inscribed = Math.max(Math.min(hw, hh) - r, 1e-6);
  for (let i = 0; i < dist.length; i += 1) {
    const qx = Math.abs(lx[i]) - (hw - r);
    const qy = Math.abs(ly[i]) - (hh - r);
    const outside = Math.hypot(Math.max(qx, 0), Math.max(qy, 0));
    const inside = Math.min(Math.max(qx, qy), 0);
    const sdf = outside + inside - r;
    dist[i] = sdf <= 0 ? 1 - -sdf / inscribed : 1 + sdf / inscribed;
  }
  return { dist, minAxis: Math.min(hw, hh) };
}

function maskAlpha(cover: ClientBlurCover, w: number, h: number): Float32Array {
  const mode = cover.edge_mode ?? "soft";
  const { dist, minAxis } = normalizedDist(cover, w, h);
  const alpha = new Float32Array(w * h);
  if (mode === "hard") {
    const aa = antialiasNorm(minAxis);
    const inner = 1 - aa;
    for (let i = 0; i < dist.length; i += 1) {
      const d = dist[i];
      if (d <= inner) alpha[i] = 1;
      else if (d <= 1 + aa) alpha[i] = Math.max(0, 1 - (d - inner) / (2 * aa));
      else alpha[i] = 0;
    }
    return alpha;
  }
  const feather = Math.max(0, cover.feather);
  const inner = Math.max(0, 1 - feather / Math.max(minAxis, 1e-6));
  for (let i = 0; i < dist.length; i += 1) {
    const d = dist[i];
    if (d <= inner) alpha[i] = 1;
    else if (d <= 1 && inner < 1) {
      const t = (d - inner) / (1 - inner);
      alpha[i] = 0.5 * (1 + Math.cos(Math.PI * t));
    } else alpha[i] = 0;
  }
  return alpha;
}

export function compositeBlurCoversOnCanvas(
  ctx: CanvasRenderingContext2D,
  covers: ClientBlurCover[],
): void {
  if (!covers.length) return;
  const w = ctx.canvas.width;
  const h = ctx.canvas.height;
  const base = ctx.getImageData(0, 0, w, h);
  const byRadius = new Map<number, ClientBlurCover[]>();
  for (const cover of covers) {
    const key = Math.round(cover.radius * 1000) / 1000;
    const group = byRadius.get(key) ?? [];
    group.push(cover);
    byRadius.set(key, group);
  }
  const out = new Uint8ClampedArray(base.data);
  const scratch = document.createElement("canvas");
  scratch.width = w;
  scratch.height = h;
  const sctx = scratch.getContext("2d");
  if (!sctx) return;
  for (const [radius, group] of byRadius) {
    sctx.putImageData(base, 0, 0);
    sctx.filter = `blur(${Math.max(1, radius)}px)`;
    sctx.drawImage(scratch, 0, 0);
    sctx.filter = "none";
    const blurData = sctx.getImageData(0, 0, w, h).data;
    const combined = new Float32Array(w * h);
    for (const cover of group) {
      const alpha = maskAlpha(cover, w, h);
      for (let i = 0; i < combined.length; i += 1) {
        combined[i] = Math.max(combined[i], alpha[i]);
      }
    }
    for (let i = 0; i < combined.length; i += 1) {
      const a = combined[i];
      if (a <= 0) continue;
      const j = i * 4;
      out[j] = Math.round(out[j] * (1 - a) + blurData[j] * a);
      out[j + 1] = Math.round(out[j + 1] * (1 - a) + blurData[j + 1] * a);
      out[j + 2] = Math.round(out[j + 2] * (1 - a) + blurData[j + 2] * a);
    }
  }
  ctx.putImageData(new ImageData(out, w, h), 0, 0);
}
