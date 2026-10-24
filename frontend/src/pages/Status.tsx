import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { api } from "../api";
import type { HealthCheck, HealthLevel } from "../SystemHealthBar";

type Counts = Record<string, number>;
type Section<T> = T & { error?: string };

export type ScanCurrent = {
  id: string;
  status: string;
  stage: string | null;
  detail: string | null;
  files_seen: number;
  files_processed: number;
  files_skipped: number;
  files_failed: number;
  files_total: number | null;
  expected_total: number | null;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number | null;
  files_per_minute: number | null;
  eta_seconds: number | null;
  uniqueness: { done: number; total: number } | null;
  error: string | null;
};

export type AppStatus = {
  generated_at: string;
  cached_seconds: number;
  health: Section<{ overall: HealthLevel; checks: HealthCheck[] }>;
  scan: Section<{ current: ScanCurrent | null; last_completed_at: string | null; active_jobs: Counts;
    active_by_kind: { kind: string; status: string; count: number }[] }>;
  photos: Section<{ total: number; active: number; skipped: Counts; locally_scanned: number; local_verdicts: Counts;
    human_ratings: Counts; rated: number; rejected: number; curation_picks: Counts; curated: number;
    blurred_images: number; blur_unlock: Counts; blur_verified: number; duplicates_found: number; ingested_24h: number }>;
  enrichment: Section<{ enabled: boolean; paused: boolean; state: string; daily_cap: number; calls_today: number;
    eligible: number; ready: number; pending: number; errors: number; refused: number; prefetch_enabled: boolean;
    prefetch_daily_cap: number; prefetch_calls_today: number; prefetch_eligible: number; prefetched: number;
    prefetch_pending: number; gemini_refused: number; backoff_until: string | null; captioned_images: number;
    hashtagged_images: number; rpm: number; concurrency: number }>;
  queue: Section<{ states: Counts; approved_unscheduled: number;
    upcoming: { id: string; filename: string; state: string; media_type: string; scheduled_at: string | null; story: boolean }[];
    open_slots: string[];
    schedule: { timezone: string; paused: boolean; max_posts_per_day: number; min_gap_hours: number } }>;
  publish: Section<{ attempts: number; succeeded: number; failed: number; posts_total: number;
    by_kind: Record<string, { attempts: number; succeeded: number; failed: number }>;
    last_attempt_at: string | null; last_failure: { at: string | null; error: string | null } | null;
    last_published: { at: string | null; permalink: string | null; media_type: string | null } | null;
    per_day: { date: string; count: number }[] }>;
  instagram: Section<{ configured: boolean; username: string | null; expected_username: string | null;
    followers: number | null; media_count: number | null; account_fetched_at: string | null;
    permissions_granted: number; permissions_missing: string[]; insights_permission: boolean | null;
    comments_permission: boolean | null; auto_publish: boolean | null; insights_last_sync_at: string | null }>;
  sync: Section<{ insights_tick_at: string | null; comments_tick_at: string | null;
    last_comment_synced_at: string | null; inbox_open: number }>;
  engage: Section<{ today: string; counts: Counts; total: number; enabled: boolean | null; daily_target: number | null;
    run_hour_local: number | null;
    last_run: { status: string; run_date: string; targets_created: number; error_code: string | null; finished_at: string | null } | null }>;
  workers: Section<{ available: boolean; version: string;
    queues: { name: string; heartbeat_age_seconds?: number | null; queued?: number; started?: number; failed?: number; workers?: number; error?: string }[] }>;
};

const fmt = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString());

function pct(part: number, whole: number | null | undefined): number {
  if (!whole) return 0;
  return Math.max(0, Math.min(100, (part / whole) * 100));
}

function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 0) return `in ${duration(-s)}`;
  return `${duration(s)} ago`;
}

function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  if (s < 90) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 90) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

function Dot({ level }: { level: HealthLevel }) {
  return <span className={`health-dot health-dot--${level}`} aria-hidden="true" />;
}

function Card({ title, children, wide }: { title: string; children: ReactNode; wide?: boolean }) {
  return (
    <section className={`st-card${wide ? " st-card--wide" : ""}`} aria-label={title}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function Failed({ section }: { section: { error?: string } }) {
  return section.error ? <p className="muted">Unavailable: {section.error}</p> : null;
}

function Bar({ value, total, label }: { value: number; total: number | null | undefined; label: string }) {
  const p = pct(value, total);
  return (
    <div className="st-bar-row">
      <div className="st-bar-label">
        <span>{label}</span>
        <span>
          {fmt(value)}
          {total ? ` / ${fmt(total)} (${p.toFixed(p < 10 ? 1 : 0)}%)` : ""}
        </span>
      </div>
      <div className="st-bar" role="progressbar" aria-label={label} aria-valuenow={Math.round(p)} aria-valuemin={0} aria-valuemax={100}>
        <div className="st-bar-fill" style={{ width: `${p}%` }} />
      </div>
    </div>
  );
}

function Breakdown({ counts, labels }: { counts: Counts; labels?: Record<string, string> }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  if (!entries.length) return <p className="muted">None yet.</p>;
  return (
    <div className="st-breakdown">
      {entries.map(([key, n]) => (
        <Bar key={key} label={labels?.[key] ?? key.replace(/_/g, " ")} value={n} total={total} />
      ))}
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="st-stat">
      <div className="st-stat-value">{value}</div>
      <div className="st-stat-label">{label}</div>
      {hint ? <div className="st-stat-hint">{hint}</div> : null}
    </div>
  );
}

function Spark({ data }: { data: { date: string; count: number }[] }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  return (
    <div className="st-spark" aria-label="Posts per day, last 14 days">
      {data.map((d) => (
        <div key={d.date} className="st-spark-col" title={`${d.date}: ${d.count}`}>
          <div className="st-spark-bar" style={{ height: `${(d.count / max) * 100}%` }} />
        </div>
      ))}
    </div>
  );
}

const VERDICT_LABELS: Record<string, string> = {
  SAFE: "Safe",
  SAFE_AFTER_CROP: "Safe after crop",
  REVIEW: "Review",
  DO_NOT_POST: "Do not post",
  none: "Not scanned",
};
const RATING_LABELS: Record<string, string> = { none: "Unrated" };

function ScanCard({ scan }: { scan: AppStatus["scan"] }) {
  if (scan.error) return <Card title="Library scan"><Failed section={scan} /></Card>;
  const job = scan.current;
  if (!job) return <Card title="Library scan"><p className="muted">No scan has run yet.</p></Card>;
  const running = job.status === "running" || job.status === "queued";
  return (
    <Card title="Library scan" wide>
      <p className="st-line">
        <span className={`st-pill st-pill--${running ? "busy" : job.status === "succeeded" ? "ok" : "bad"}`}>{job.status}</span>
        {job.stage ? <span className="muted">stage: {job.stage}</span> : null}
        <span className="muted">started {when(job.started_at)} · elapsed {duration(job.elapsed_seconds)}</span>
        {job.finished_at ? <span className="muted">finished {when(job.finished_at)}</span> : null}
      </p>
      <Bar label="Files seen" value={job.files_seen} total={job.expected_total} />
      {job.uniqueness ? (
        <Bar label="Duplicate check" value={job.uniqueness.done} total={job.uniqueness.total} />
      ) : job.stage === "uniqueness" ? (
        <p className="muted">Duplicate check running (progress appears after the next worker update).</p>
      ) : null}
      <div className="st-stats">
        <Stat label="processed" value={fmt(job.files_processed)} />
        <Stat label="unchanged / skipped" value={fmt(job.files_skipped)} />
        <Stat label="failed" value={fmt(job.files_failed)} />
        <Stat label="files / min" value={fmt(job.files_per_minute)} />
        <Stat label="ETA" value={running ? (job.stage === "uniqueness" ? "finishing" : duration(job.eta_seconds)) : "done"} />
      </div>
      {job.error ? <p className="st-error">{job.error}</p> : null}
      <p className="muted">Last completed scan: {when(scan.last_completed_at)}</p>
    </Card>
  );
}

export function StatusPage() {
  const query = useQuery({
    queryKey: ["app-status"],
    queryFn: () => api<AppStatus>("/api/status"),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });
  const data = query.data;

  return (
    <main className="page st-page">
      <div className="st-head">
        <h1>Status</h1>
        <span className="muted">
          {data ? `Updated ${when(data.generated_at)} · refreshes every 30s` : query.isLoading ? "Loading…" : ""}
        </span>
        <button className="ghost" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {query.isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {query.isError ? <p className="st-error">Could not load status.</p> : null}
      {data ? (
        <div className="st-grid">
          <Card title="Health">
            {data.health.error ? <Failed section={data.health} /> : (
              <>
                <p className="st-line"><Dot level={data.health.overall} /> <strong>{data.health.overall.toUpperCase()}</strong></p>
                <ul className="st-checks">
                  {data.health.checks.map((check) => (
                    <li key={check.id} title={check.detail}>
                      <Dot level={check.status} /> <span>{check.label}</span>
                      <span className="muted st-check-detail">{check.detail}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Card>

          <ScanCard scan={data.scan} />

          <Card title="Photos">
            {data.photos.error ? <Failed section={data.photos} /> : (
              <>
                <div className="st-stats">
                  <Stat label="total files" value={fmt(data.photos.total)} />
                  <Stat label="active photos" value={fmt(data.photos.active)} />
                  <Stat label="new in 24h" value={fmt(data.photos.ingested_24h)} />
                  <Stat label="duplicates" value={fmt(data.photos.duplicates_found)} />
                </div>
                <Bar label="Locally scanned" value={data.photos.locally_scanned} total={data.photos.active} />
                <Bar label="Human rated" value={data.photos.rated} total={data.photos.active} />
                <div className="st-stats">
                  <Stat label="curated picks" value={fmt(data.photos.curated)} />
                  <Stat label="rejected" value={fmt(data.photos.rejected)} />
                  <Stat label="blurred" value={fmt(data.photos.blurred_images)} />
                  <Stat label="blur verified" value={fmt(data.photos.blur_verified)} />
                </div>
              </>
            )}
          </Card>

          <Card title="Local verdicts">
            {data.photos.error ? <Failed section={data.photos} /> : <Breakdown counts={data.photos.local_verdicts} labels={VERDICT_LABELS} />}
          </Card>

          <Card title="Human ratings">
            {data.photos.error ? <Failed section={data.photos} /> : (
              <>
                <Breakdown counts={data.photos.human_ratings} labels={RATING_LABELS} />
                {Object.keys(data.photos.curation_picks).length ? (
                  <>
                    <h3>Curation picks</h3>
                    <Breakdown counts={data.photos.curation_picks} />
                  </>
                ) : null}
              </>
            )}
          </Card>

          <Card title="Enrichment (Gemini)">
            {data.enrichment.error ? <Failed section={data.enrichment} /> : (
              <>
                <p className="st-line">
                  <span className={`st-pill st-pill--${data.enrichment.paused || !data.enrichment.enabled ? "bad" : "ok"}`}>
                    {data.enrichment.state}
                  </span>
                  <span className="muted">prefetch {data.enrichment.prefetch_enabled ? "on" : "off"}</span>
                  <span className="muted">{data.enrichment.rpm} rpm · ×{data.enrichment.concurrency}</span>
                </p>
                <Bar label="Gemini calls today" value={data.enrichment.calls_today} total={data.enrichment.daily_cap} />
                {data.enrichment.prefetch_enabled ? (
                  <Bar label="Prefetch calls today" value={data.enrichment.prefetch_calls_today} total={data.enrichment.prefetch_daily_cap} />
                ) : null}
                <Bar label="Eligible enriched" value={data.enrichment.ready} total={data.enrichment.eligible} />
                <div className="st-stats">
                  <Stat label="pending" value={fmt(data.enrichment.pending)} />
                  <Stat label="captioned" value={fmt(data.enrichment.captioned_images)} />
                  <Stat label="hashtagged" value={fmt(data.enrichment.hashtagged_images)} />
                  <Stat label="refusals" value={fmt(data.enrichment.gemini_refused)} />
                  <Stat label="errors" value={fmt(data.enrichment.errors)} />
                  <Stat label="prefetched" value={fmt(data.enrichment.prefetched)} hint={`${fmt(data.enrichment.prefetch_pending)} pending`} />
                </div>
                {data.enrichment.backoff_until ? <p className="muted">Backing off until {when(data.enrichment.backoff_until)}</p> : null}
              </>
            )}
          </Card>

          <Card title="Publishing queue">
            {data.queue.error ? <Failed section={data.queue} /> : (
              <>
                <p className="st-line">
                  <span className={`st-pill st-pill--${data.queue.schedule.paused ? "bad" : "ok"}`}>
                    {data.queue.schedule.paused ? "schedule paused" : "schedule active"}
                  </span>
                  <span className="muted">
                    max {data.queue.schedule.max_posts_per_day}/day · gap {data.queue.schedule.min_gap_hours}h · {data.queue.schedule.timezone}
                  </span>
                </p>
                <Breakdown counts={data.queue.states} />
                <p className="muted">Approved without a time: {fmt(data.queue.approved_unscheduled)}</p>
              </>
            )}
          </Card>

          <Card title="Upcoming schedule">
            {data.queue.error ? <Failed section={data.queue} /> : (
              <>
                {data.queue.upcoming.length ? (
                  <ol className="st-list">
                    {data.queue.upcoming.map((item) => (
                      <li key={item.id}>
                        <strong>{when(item.scheduled_at)}</strong> <span className="muted">{item.media_type.toLowerCase()}{item.story ? " + story" : ""}</span>
                        <span className="st-file">{item.filename}</span>
                      </li>
                    ))}
                  </ol>
                ) : <p className="muted">Nothing scheduled.</p>}
                <h3>Open slots (next 72h)</h3>
                {data.queue.open_slots.length ? (
                  <div className="st-chips">
                    {data.queue.open_slots.map((slot) => <span key={slot} className="st-chip">{when(slot)}</span>)}
                  </div>
                ) : <p className="muted">No open slots.</p>}
              </>
            )}
          </Card>

          <Card title="Publish history">
            {data.publish.error ? <Failed section={data.publish} /> : (
              <>
                <div className="st-stats">
                  <Stat label="posts" value={fmt(data.publish.posts_total)} />
                  <Stat label="attempts" value={fmt(data.publish.attempts)} />
                  <Stat label="succeeded" value={fmt(data.publish.succeeded)} />
                  <Stat label="failed" value={fmt(data.publish.failed)} />
                </div>
                <Spark data={data.publish.per_day} />
                <p className="muted">Posts per day, last 14 days</p>
                <p>
                  Last published:{" "}
                  {data.publish.last_published ? (
                    <>
                      {when(data.publish.last_published.at)}{" "}
                      {data.publish.last_published.permalink ? (
                        <a href={data.publish.last_published.permalink} target="_blank" rel="noreferrer">view post</a>
                      ) : null}
                    </>
                  ) : "never"}
                </p>
                {data.publish.last_failure ? (
                  <p className="muted">Last failure {when(data.publish.last_failure.at)}: {data.publish.last_failure.error ?? "unknown"}</p>
                ) : null}
              </>
            )}
          </Card>

          <Card title="Instagram">
            {data.instagram.error ? <Failed section={data.instagram} /> : (
              <>
                <p className="st-line">
                  <Dot level={data.instagram.configured && (!data.instagram.expected_username || data.instagram.username === data.instagram.expected_username.replace(/^@/, "")) ? "ok" : "warning"} />
                  <strong>{data.instagram.username ? `@${data.instagram.username}` : "not connected"}</strong>
                  {data.instagram.expected_username ? <span className="muted">expected @{data.instagram.expected_username.replace(/^@/, "")}</span> : null}
                </p>
                <div className="st-stats">
                  <Stat label="followers" value={fmt(data.instagram.followers)} />
                  <Stat label="media" value={fmt(data.instagram.media_count)} />
                  <Stat label="permissions" value={fmt(data.instagram.permissions_granted)} hint={data.instagram.permissions_missing.length ? `missing: ${data.instagram.permissions_missing.join(", ")}` : "all granted"} />
                </div>
                <p className="muted">Auto-publish {data.instagram.auto_publish ? "on" : "off"} · profile checked {ago(data.instagram.account_fetched_at)}</p>
              </>
            )}
          </Card>

          <Card title="Sync & Engage">
            {data.sync.error ? <Failed section={data.sync} /> : (
              <ul className="st-kv">
                <li><span>Insights sync</span><span>{ago(data.sync.insights_tick_at ?? (data.instagram.error ? null : data.instagram.insights_last_sync_at))}</span></li>
                <li><span>Comments sync</span><span>{ago(data.sync.comments_tick_at ?? data.sync.last_comment_synced_at)}</span></li>
                <li><span>Open inbox comments</span><span>{fmt(data.sync.inbox_open)}</span></li>
              </ul>
            )}
            {data.engage.error ? <Failed section={data.engage} /> : (
              <>
                <h3>Engage today ({data.engage.today})</h3>
                <Bar label="Done" value={data.engage.counts.done ?? 0} total={data.engage.total || data.engage.daily_target} />
                <p className="muted">
                  {fmt(data.engage.counts.open ?? 0)} open · {fmt(data.engage.counts.skipped ?? 0)} skipped ·{" "}
                  {data.engage.enabled ? `daily list at ${data.engage.run_hour_local}:00` : "disabled"}
                  {data.engage.last_run ? ` · last run ${data.engage.last_run.status} ${ago(data.engage.last_run.finished_at)}` : ""}
                </p>
              </>
            )}
          </Card>

          <Card title="Workers" wide>
            {data.workers.error ? <Failed section={data.workers} /> : !data.workers.available ? (
              <p className="muted">Queue workers not in use (inline mode or Redis unreachable).</p>
            ) : (
              <table className="st-table">
                <thead>
                  <tr><th>Queue</th><th>Heartbeat</th><th>Workers</th><th>Running</th><th>Queued</th><th>Failed</th></tr>
                </thead>
                <tbody>
                  {data.workers.queues.map((q) => {
                    const fresh = q.heartbeat_age_seconds != null && q.heartbeat_age_seconds < 90;
                    return (
                      <tr key={q.name}>
                        <td><Dot level={fresh ? "ok" : "down"} /> {q.name}</td>
                        <td>{q.heartbeat_age_seconds != null ? `${q.heartbeat_age_seconds}s ago` : "none"}</td>
                        <td>{fmt(q.workers)}</td>
                        <td>{fmt(q.started)}</td>
                        <td>{fmt(q.queued)}</td>
                        <td>{fmt(q.failed)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            {!data.workers.error ? <p className="muted">Version {data.workers.version}</p> : null}
          </Card>
        </div>
      ) : null}
    </main>
  );
}
