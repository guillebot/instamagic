export type EnrichmentStatus = {
  enabled: boolean;
  paused: boolean;
  daily_cap: number;
  calls_today: number;
  eligible: number;
  ready: number;
  pending: number;
  errors: number;
  errors_exhausted: number;
  refused: number;
  state: "disabled" | "paused" | "backoff" | "capped" | "running" | "idle" | string;
  batch_active: boolean;
  backoff_until: string | null;
  rpm?: number;
  concurrency?: number;
  prefetch_enabled?: boolean;
  prefetch_daily_cap?: number;
  prefetch_calls_today?: number;
  prefetch_eligible?: number;
  prefetched?: number;
  prefetch_pending?: number;
  prefetch_paused_reason?: string | null;
  prefetch_paused_at?: string | null;
  gemini_refused?: number;
};

const STATE_LABEL: Record<string, string> = {
  disabled: "off",
  paused: "paused",
  backoff: "waiting (Gemini rate limit)",
  capped: "daily cap reached",
  running: "running",
  idle: "up to date",
  prefetching: "pre-generating for unrated clean photos",
  prefetch_capped: "prefetch daily cap reached",
};

export function enrichmentLine(status: EnrichmentStatus): string {
  const n = (value: number) => value.toLocaleString("en-US");
  const state = STATE_LABEL[status.state] ?? status.state;
  const rated = `Enrichment: rated-safe ready ${n(status.ready)}/${n(status.eligible)}`;
  const prefetch =
    status.prefetch_enabled && status.prefetch_eligible !== undefined
      ? ` · prefetched ${n(status.prefetched ?? 0)}/${n(status.prefetch_eligible)} clean unrated`
      : "";
  return `${rated}${prefetch} · ${state}`;
}

/** Rough minutes left: ~2 Gemini calls per photo (label+captions, hashtags) at the configured RPM. */
export function enrichmentEtaMinutes(status: EnrichmentStatus): number | null {
  if (!status.pending || !status.rpm || status.state !== "running") return null;
  return Math.max(1, Math.ceil((status.pending * 2) / status.rpm));
}
