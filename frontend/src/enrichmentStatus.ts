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
};

const STATE_LABEL: Record<string, string> = {
  disabled: "off",
  paused: "paused",
  backoff: "waiting (Gemini rate limit)",
  capped: "daily cap reached",
  running: "running",
  idle: "up to date",
};

export function enrichmentLine(status: EnrichmentStatus): string {
  const n = (value: number) => value.toLocaleString("en-US");
  const state = STATE_LABEL[status.state] ?? status.state;
  return `Enrichment: ${n(status.ready)}/${n(status.eligible)} safe photos ready · ${state}`;
}

/** Rough minutes left: ~2 Gemini calls per photo (label+captions, hashtags) at the configured RPM. */
export function enrichmentEtaMinutes(status: EnrichmentStatus): number | null {
  if (!status.pending || !status.rpm || status.state !== "running") return null;
  return Math.max(1, Math.ceil((status.pending * 2) / status.rpm));
}
