import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api";
import { ButtonSpinner, PendingButtonLabel } from "../actionButton";
import type { EngageOverview, EngageSettings, EngageTarget } from "../types";

const RUN_ERROR_COPY: Record<string, string> = {
  not_connected: "Instagram is not connected.",
  auth: "Instagram token expired or was revoked.",
  hashtag_permission: "Instagram denied hashtag search for this app.",
  permission_missing: "Some Instagram permissions are missing.",
};

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export function EngageTargetCard({
  target,
  onMark,
  busy,
}: {
  target: EngageTarget;
  onMark: (id: string, status: EngageTarget["status"]) => void;
  busy: boolean;
}) {
  const [draft, setDraft] = useState(target.suggestion ?? "");
  const [copied, setCopied] = useState(false);
  useEffect(() => setDraft(target.suggestion ?? ""), [target.suggestion]);
  const who = target.username ? `@${target.username}` : `via #${target.source_value}`;
  const closed = target.status !== "open";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(draft);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <article className={`card engage-card${closed ? " engage-closed" : ""}`} data-status={target.status}>
      <a href={target.permalink} target="_blank" rel="noopener noreferrer" className="engage-thumb">
        {target.media_url ? (
          <img src={target.media_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
        ) : (
          <span className="engage-thumb-empty">{target.media_type === "CAROUSEL_ALBUM" ? "Carousel" : "Open"}</span>
        )}
      </a>
      <div className="engage-body">
        <div className="row engage-head">
          <strong>{who}</strong>
          {target.like_count != null ? <span className="muted">{target.like_count} likes</span> : null}
          {closed ? <span className={`badge engage-${target.status}`}>{target.status}</span> : null}
        </div>
        {target.caption_snippet ? <p className="engage-caption">{target.caption_snippet}</p> : null}
        <p className="help">Why: {target.reason}</p>
        <textarea
          rows={2}
          value={draft}
          placeholder="Write your own comment"
          aria-label={`Suggested comment for ${who}`}
          onChange={(e) => setDraft(e.target.value)}
        />
        <div className="row">
          <a className="button" href={target.permalink} target="_blank" rel="noopener noreferrer">
            Open post
          </a>
          <button className="ghost" type="button" disabled={!draft.trim()} onClick={() => void copy()}>
            {copied ? "Copied" : "Copy comment"}
          </button>
          {closed ? (
            <button className="ghost" type="button" disabled={busy} onClick={() => onMark(target.id, "open")}>
              Undo
            </button>
          ) : (
            <>
              <button className="ghost" type="button" disabled={busy} onClick={() => onMark(target.id, "done")}>
                Done
              </button>
              <button className="ghost" type="button" disabled={busy} onClick={() => onMark(target.id, "skipped")}>
                Skip
              </button>
            </>
          )}
        </div>
      </div>
    </article>
  );
}

function SettingsForm({ settings }: { settings: EngageSettings }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    daily_target: settings.daily_target,
    account_cooldown_days: settings.account_cooldown_days,
    run_hour_local: settings.run_hour_local,
    seed: settings.seed_hashtags.join(" "),
    enabled: settings.enabled,
  });
  const save = useMutation({
    mutationFn: () =>
      api<EngageSettings>("/api/engage/settings", {
        method: "PATCH",
        body: JSON.stringify({
          daily_target: form.daily_target,
          account_cooldown_days: form.account_cooldown_days,
          run_hour_local: form.run_hour_local,
          enabled: form.enabled,
          seed_hashtags: form.seed.split(/[\s,]+/).filter(Boolean),
        }),
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["engage"] }),
  });
  return (
    <form
      className="engage-settings"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label>
        <input
          type="checkbox"
          checked={form.enabled}
          onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
        />{" "}
        Build a list every morning
      </label>
      <label>
        Posts per day
        <input
          type="number"
          min={5}
          max={30}
          value={form.daily_target}
          onChange={(e) => setForm({ ...form, daily_target: Number(e.target.value) })}
        />
      </label>
      <label>
        Don't repeat an account for (days)
        <input
          type="number"
          min={1}
          max={60}
          value={form.account_cooldown_days}
          onChange={(e) => setForm({ ...form, account_cooldown_days: Number(e.target.value) })}
        />
      </label>
      <label>
        Morning run hour (ART)
        <input
          type="number"
          min={0}
          max={23}
          value={form.run_hour_local}
          onChange={(e) => setForm({ ...form, run_hour_local: Number(e.target.value) })}
        />
      </label>
      <label>
        Seed hashtags (optional, max 8)
        <input
          type="text"
          value={form.seed}
          placeholder="streetportrait availablelight"
          onChange={(e) => setForm({ ...form, seed: e.target.value })}
        />
      </label>
      <button className="button" type="submit" disabled={save.isPending}>
        <PendingButtonLabel pending={save.isPending} pendingLabel="Saving…" label="Save settings" />
      </button>
      {save.isError ? <p className="error">{errorText(save.error, "Save failed")}</p> : null}
    </form>
  );
}

function SimilarAccounts({ handles, suggested }: { handles: string[]; suggested: string[] }) {
  const queryClient = useQueryClient();
  const [handle, setHandle] = useState("");
  const add = useMutation({
    mutationFn: (h: string) =>
      api("/api/related-accounts", {
        method: "POST",
        body: JSON.stringify({ handle: h.replace(/^@/, ""), why_relevant: "similar photographer" }),
      }),
    onSuccess: () => {
      setHandle("");
      void queryClient.invalidateQueries({ queryKey: ["engage"] });
      void queryClient.invalidateQueries({ queryKey: ["related"] });
    },
  });
  return (
    <section className="engage-accounts">
      <h2>Similar accounts</h2>
      <p className="muted">
        One recent post from each (business/creator accounts only) joins the daily list. Manage the full list on{" "}
        <Link to="/related">Related</Link>.
      </p>
      <p>{handles.length ? handles.map((h) => `@${h}`).join(" · ") : "None yet."}</p>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          if (handle.trim()) add.mutate(handle.trim());
        }}
      >
        <input type="text" value={handle} placeholder="@username" onChange={(e) => setHandle(e.target.value)} />
        <button className="ghost" type="submit" disabled={!handle.trim() || add.isPending}>
          Add
        </button>
      </form>
      {suggested.length ? (
        <>
          <p className="help">Mentioned in today's hashtag posts:</p>
          <div className="row engage-suggested">
            {suggested.map((h) => (
              <button key={h} className="ghost" type="button" disabled={add.isPending} onClick={() => add.mutate(h)}>
                + @{h}
              </button>
            ))}
          </div>
        </>
      ) : null}
      {add.isError ? <p className="error">{errorText(add.error, "Could not add account")}</p> : null}
    </section>
  );
}

export function EngagePage() {
  const queryClient = useQueryClient();
  const [waiting, setWaiting] = useState(false);
  const overview = useQuery({
    queryKey: ["engage"],
    queryFn: () => api<EngageOverview>("/api/engage"),
    refetchInterval: (query) => (waiting || query.state.data?.running ? 3000 : false),
  });
  const data = overview.data;
  const lastRunId = data?.last_run?.id ?? null;
  const [startRunId, setStartRunId] = useState<string | null>(null);
  useEffect(() => {
    if (waiting && !data?.running && lastRunId !== startRunId) setWaiting(false);
  }, [waiting, data?.running, lastRunId, startRunId]);
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setTimeout(() => setWaiting(false), 180000);
    return () => window.clearTimeout(timer);
  }, [waiting]);

  const refresh = useMutation({
    mutationFn: () => api<{ queued: boolean; running: boolean }>("/api/engage/refresh", { method: "POST" }),
    onMutate: () => setStartRunId(lastRunId),
    onSuccess: (body) => {
      if (body.queued || body.running) setWaiting(true);
      void queryClient.invalidateQueries({ queryKey: ["engage"] });
    },
  });
  const mark = useMutation({
    mutationFn: ({ id, status }: { id: string; status: EngageTarget["status"] }) =>
      api<EngageTarget>(`/api/engage/targets/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["engage"] }),
  });

  if (overview.isLoading) return <main className="page">Loading…</main>;
  if (overview.isError || !data) {
    return (
      <main className="page">
        <p className="error">{errorText(overview.error, "Could not load the Engage list.")}</p>
      </main>
    );
  }

  const busy = waiting || data.running || refresh.isPending;
  const permissionBlocked = !data.permission.ok;
  const run = data.last_run;
  const runBlocked = run && run.status === "blocked" && run.error_code;
  const todayRow = data.days.find((d) => d.date === data.today);
  const open = data.targets.filter((t) => t.status === "open").length;
  const suggested = (run?.detail?.suggested_accounts as string[] | undefined) ?? [];

  return (
    <main className="page engage-page">
      <h1>Engage</h1>
      <p className="muted">
        A short daily list of posts by similar photographers. Open each one in Instagram and like or comment
        yourself, only if you mean it. This app never likes, follows, or comments for you.
      </p>

      {permissionBlocked || runBlocked ? (
        <p className="banner error" role="alert">
          {permissionBlocked
            ? data.permission.message
            : run?.error || RUN_ERROR_COPY[run?.error_code ?? ""] || "The last run could not finish."}{" "}
          <Link to="/instagram">Reconnect Instagram</Link>
        </p>
      ) : null}
      {run?.error_code === "hashtag_permission" && run.status === "ok" ? (
        <p className="banner">Hashtag search was denied; today's list uses similar accounts only.</p>
      ) : null}

      <div className="row engage-toolbar">
        <button
          className="button"
          type="button"
          disabled={busy || permissionBlocked}
          title={permissionBlocked ? "Reconnect Instagram first" : undefined}
          onClick={() => refresh.mutate()}
        >
          {busy ? (
            <>
              <ButtonSpinner /> Building list…
            </>
          ) : (
            "Refresh now"
          )}
        </button>
        <span className="engage-stats">
          Today: <strong>{todayRow?.done ?? 0}</strong> done · {todayRow?.skipped ?? 0} skipped · {open} open
        </span>
        {run ? (
          <span className="muted">
            Last run {run.finished_at ? new Date(run.finished_at).toLocaleString() : "—"} ({run.targets_created}{" "}
            new)
          </span>
        ) : null}
      </div>
      {refresh.isError ? <p className="error">{errorText(refresh.error, "Refresh failed")}</p> : null}

      {data.list_date !== data.today && data.targets.length ? (
        <p className="help">Showing the list from {data.list_date}. Today's list is built at {data.settings.run_hour_local}:00 ART.</p>
      ) : null}
      {!data.targets.length && !busy ? (
        <p className="help">
          No list yet. It is built every morning at {data.settings.run_hour_local}:00 ART, or press Refresh now.
        </p>
      ) : null}

      <section className="card-list engage-list">
        {data.targets.map((t) => (
          <EngageTargetCard
            key={t.id}
            target={t}
            busy={mark.isPending}
            onMark={(id, status) => mark.mutate({ id, status })}
          />
        ))}
      </section>

      {data.days.length ? (
        <section>
          <h2>Daily done</h2>
          <p className="muted">
            {data.days
              .slice(0, 7)
              .map((d) => `${d.date.slice(5)}: ${d.done}/${d.total}`)
              .join(" · ")}
          </p>
        </section>
      ) : null}

      <SimilarAccounts handles={data.similar_accounts} suggested={suggested} />

      <section>
        <h2>Niche hashtags</h2>
        <p className="muted">
          {data.niche_hashtags.map((h) => `#${h.tag}`).join(" ")} — {data.settings.hashtags_per_run} rotate in each
          day. Hashtag budget: {data.hashtag_budget.used_unique_hashtags_7d}/{data.hashtag_budget.limit_unique_hashtags_7d}{" "}
          unique in 7 days.
        </p>
      </section>

      <section>
        <h2>Settings</h2>
        <SettingsForm key={JSON.stringify(data.settings)} settings={data.settings} />
      </section>
    </main>
  );
}
