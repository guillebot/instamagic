import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { InstagramConnectionStatus } from "../types";

type InsightsDashboard = {
  follower_trend: { captured_at: string; follower_count: number | null; reach: number | null }[];
  top_posts: {
    post_id: string;
    image_id: string | null;
    permalink: string | null;
    caption: string;
    engagement_rate: number | null;
    reach: number | null;
    likes: number | null;
  }[];
  by_tag: Record<string, number>;
  by_hour: Record<string, number>;
  by_weekday: Record<string, number>;
  best_posting_times: { best_hours_utc: number[]; best_weekdays_iso: number[]; source: string };
};

export function InsightsPage() {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["instagram-status"],
    queryFn: () => api<InstagramConnectionStatus>("/api/instagram/status"),
  });
  const dashboard = useQuery({
    queryKey: ["insights-dashboard"],
    queryFn: () => api<InsightsDashboard>("/api/instagram/insights/dashboard"),
  });
  const sync = useMutation({
    mutationFn: () => api<{ ok: boolean; error?: string }>("/api/instagram/insights/sync?force=true", { method: "POST" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["insights-dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["instagram-status"] });
    },
  });

  const reconnect = status.data?.insights_reconnect_required;

  return (
    <main className="page">
      <h1>Insights</h1>
      <p className="muted">Account and post performance from the official Graph API. Sync runs about every 6 hours.</p>
      {reconnect ? (
        <p className="banner error">
          Reconnect via Meta on the Instagram page to grant <code>instagram_manage_insights</code>.
        </p>
      ) : null}
      <div className="toolbar">
        <button className="primary" onClick={() => sync.mutate()} disabled={sync.isPending}>
          {sync.isPending ? "Syncing…" : "Sync now"}
        </button>
        {sync.data?.error ? <span className="muted">{sync.data.error}</span> : null}
      </div>
      {dashboard.isLoading ? <p>Loading insights…</p> : null}
      {dashboard.data ? (
        <>
          <section className="card">
            <h2>Follower trend</h2>
            {dashboard.data.follower_trend.length === 0 ? (
              <p className="muted">No snapshots yet — run a sync after connecting Instagram.</p>
            ) : (
              <ul className="plain-list">
                {dashboard.data.follower_trend.slice(-14).map((row) => (
                  <li key={row.captured_at}>
                    {new Date(row.captured_at).toLocaleString()} — followers {row.follower_count ?? "—"}, reach{" "}
                    {row.reach ?? "—"}
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="card">
            <h2>Best posting times (UTC)</h2>
            <p>
              Hours: {dashboard.data.best_posting_times.best_hours_utc.join(", ")} (
              {dashboard.data.best_posting_times.source})
            </p>
            <p>Weekdays (ISO): {dashboard.data.best_posting_times.best_weekdays_iso.join(", ")}</p>
          </section>
          <section className="card">
            <h2>Top posts</h2>
            {dashboard.data.top_posts.length === 0 ? (
              <p className="muted">Publish from the queue to see post-level metrics here.</p>
            ) : (
              <ul className="plain-list">
                {dashboard.data.top_posts.map((post) => (
                  <li key={post.post_id}>
                    {post.permalink ? (
                      <a href={post.permalink} target="_blank" rel="noreferrer">
                        Post
                      </a>
                    ) : (
                      "Post"
                    )}{" "}
                    — engagement {post.engagement_rate != null ? (post.engagement_rate * 100).toFixed(1) + "%" : "—"}
                    <div className="muted">{post.caption}</div>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="card">
            <h2>By tag</h2>
            {Object.keys(dashboard.data.by_tag).length === 0 ? (
              <p className="muted">Tag linked posts to see tag performance.</p>
            ) : (
              <ul className="plain-list">
                {Object.entries(dashboard.data.by_tag).map(([tag, score]) => (
                  <li key={tag}>#{tag} — {score.toFixed(3)}</li>
                ))}
              </ul>
            )}
          </section>
        </>
      ) : null}
    </main>
  );
}
