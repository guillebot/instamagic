import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api } from "../api";
import type { CommentDraftItem, RelatedAccountItem } from "../types";

const emptyForm = {
  handle: "",
  display_name: "",
  profile_url: "",
  why_relevant: "",
  notes: "",
};

function AccountDiscovery({ accountId, handle }: { accountId: string; handle: string }) {
  const queryClient = useQueryClient();
  const discovery = useQuery({
    queryKey: ["related-discovery", accountId],
    queryFn: () =>
      api<{
        cached: boolean;
        followers_count?: number;
        avg_likes?: number;
        posts_per_week?: number;
        common_hashtags?: string[];
      }>(`/api/related-accounts/${accountId}/discovery`),
  });
  const sync = useMutation({
    mutationFn: () =>
      api(`/api/related-accounts/${accountId}/discovery?force=true`, { method: "POST" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["related-discovery", accountId] }),
  });
  const data = discovery.data;
  return (
    <div className="muted">
      <button className="ghost" type="button" disabled={sync.isPending} onClick={() => sync.mutate()}>
        {sync.isPending ? "Syncing…" : "Sync business discovery"}
      </button>
      {data?.cached ? (
        <p>
          @{handle}: {data.followers_count ?? "—"} followers · avg likes {data.avg_likes?.toFixed(0) ?? "—"} ·{" "}
          {data.posts_per_week?.toFixed(1) ?? "—"} posts/week
          {data.common_hashtags?.length ? ` · tags: ${data.common_hashtags.map((t) => `#${t}`).join(" ")}` : ""}
        </p>
      ) : (
        <p>No cached discovery yet (daily cache).</p>
      )}
    </div>
  );
}

export function RelatedAccountsPage() {
  const queryClient = useQueryClient();
  const budget = useQuery({
    queryKey: ["hashtag-budget"],
    queryFn: () =>
      api<{ used_unique_hashtags_7d: number; remaining_unique_hashtags_7d: number }>(
        "/api/hashtag-research/budget",
      ),
  });
  const [hashtagInput, setHashtagInput] = useState("");
  const [hashtagResult, setHashtagResult] = useState<string | null>(null);
  const hashtagLookup = useMutation({
    mutationFn: (tag: string) =>
      api<{ hashtag: string; top_media: { caption?: string }[] }>(`/api/hashtag-research/${encodeURIComponent(tag)}`, {
        method: "POST",
      }),
    onSuccess: (body) => {
      setHashtagResult(`#${body.hashtag}: ${body.top_media?.length ?? 0} top posts cached`);
      void queryClient.invalidateQueries({ queryKey: ["hashtag-budget"] });
    },
    onError: (err) => setHashtagResult(err instanceof ApiError ? err.message : "Hashtag lookup failed"),
  });
  const accounts = useQuery({
    queryKey: ["related-accounts"],
    queryFn: () => api<RelatedAccountItem[]>("/api/related-accounts"),
  });
  const drafts = useQuery({
    queryKey: ["comment-drafts"],
    queryFn: () => api<CommentDraftItem[]>("/api/comment-drafts"),
  });
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");
  const [draftTarget, setDraftTarget] = useState("");
  const [draftAccountId, setDraftAccountId] = useState("");

  const save = useMutation({
    mutationFn: () =>
      api<RelatedAccountItem>("/api/related-accounts", {
        method: "POST",
        body: JSON.stringify({
          handle: form.handle,
          display_name: form.display_name || null,
          profile_url: form.profile_url || null,
          why_relevant: form.why_relevant || null,
          notes: form.notes || null,
        }),
      }),
    onSuccess: async () => {
      setForm(emptyForm);
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["related-accounts"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Could not save account."),
  });

  async function remove(id: string) {
    if (!window.confirm("Remove this research account?")) return;
    await api(`/api/related-accounts/${id}`, { method: "DELETE" });
    await queryClient.invalidateQueries({ queryKey: ["related-accounts"] });
  }

  const draft = useMutation({
    mutationFn: () =>
      api<CommentDraftItem[]>("/api/comment-drafts", {
        method: "POST",
        body: JSON.stringify({
          related_account_id: draftAccountId || null,
          target_context: draftTarget,
          confirmed: true,
        }),
      }),
    onSuccess: async () => {
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["comment-drafts"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Draft request failed."),
  });

  async function markDraft(id: string, status: string) {
    await api(`/api/comment-drafts/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
    await queryClient.invalidateQueries({ queryKey: ["comment-drafts"] });
  }

  return (
    <main className="page">
      <h1>Related accounts</h1>
      <p>
        Research list only — no follow, like, or comment bots. Paste handles and notes from your pointers; draft
        insightful replies here, then post them yourself on Instagram.
      </p>

      <section className="panel">
        <h2>Hashtag research</h2>
        <p className="muted">
          Uses Graph hashtag search (requires Instagram Public Content Access). Budget:{" "}
          {budget.data?.remaining_unique_hashtags_7d ?? "—"} of 30 unique tags left in the rolling 7-day window.
        </p>
        <div className="row">
          <input
            value={hashtagInput}
            onChange={(e) => setHashtagInput(e.target.value)}
            placeholder="streetphotography"
          />
          <button
            className="button"
            type="button"
            disabled={!hashtagInput.trim() || hashtagLookup.isPending}
            onClick={() => hashtagLookup.mutate(hashtagInput.trim())}
          >
            Research
          </button>
        </div>
        {hashtagResult ? <p className="muted">{hashtagResult}</p> : null}
      </section>

      <section className="panel">
        <h2>Add account</h2>
        <div className="form-grid">
          <label>
            Handle
            <input
              value={form.handle}
              onChange={(event) => setForm({ ...form, handle: event.target.value })}
              placeholder="photographer"
            />
          </label>
          <label>
            Display name
            <input
              value={form.display_name}
              onChange={(event) => setForm({ ...form, display_name: event.target.value })}
            />
          </label>
          <label>
            Profile URL
            <input
              value={form.profile_url}
              onChange={(event) => setForm({ ...form, profile_url: event.target.value })}
              placeholder="https://instagram.com/…"
            />
          </label>
          <label className="span-2">
            Why relevant
            <textarea
              rows={2}
              value={form.why_relevant}
              onChange={(event) => setForm({ ...form, why_relevant: event.target.value })}
              placeholder="Lighting style, audience, caption voice…"
            />
          </label>
          <label className="span-2">
            Research notes / pointers
            <textarea
              rows={3}
              value={form.notes}
              onChange={(event) => setForm({ ...form, notes: event.target.value })}
              placeholder="Paste your pointers, post URLs, themes to study…"
            />
          </label>
        </div>
        <button className="button" disabled={!form.handle.trim() || save.isPending} onClick={() => save.mutate()}>
          Save
        </button>
        {error ? <p className="error">{error}</p> : null}
      </section>

      <section className="card-list">
        {accounts.data?.map((account) => (
          <article className="card" key={account.id}>
            <div>
              <strong>@{account.handle}</strong>
              {account.display_name ? <span> · {account.display_name}</span> : null}
              {account.profile_url ? (
                <p>
                  <a href={account.profile_url} target="_blank" rel="noreferrer">
                    {account.profile_url}
                  </a>
                </p>
              ) : null}
              {account.why_relevant ? <p>{account.why_relevant}</p> : null}
              {account.notes ? <p className="muted">{account.notes}</p> : null}
              <AccountDiscovery accountId={account.id} handle={account.handle} />
              <button className="ghost" onClick={() => void remove(account.id)}>
                Remove
              </button>
            </div>
          </article>
        ))}
        {accounts.data && accounts.data.length === 0 ? (
          <p>No related accounts yet. Add the handles and research notes you already have.</p>
        ) : null}
      </section>

      <section className="panel">
        <h2>Draft insightful comments</h2>
        <p className="muted">Gemini drafts only. You copy and post manually — Instamagic never auto-comments.</p>
        <div className="form-grid">
          <label>
            Related account
            <select value={draftAccountId} onChange={(event) => setDraftAccountId(event.target.value)}>
              <option value="">None</option>
              {accounts.data?.map((account) => (
                <option key={account.id} value={account.id}>
                  @{account.handle}
                </option>
              ))}
            </select>
          </label>
          <label className="span-2">
            What you are replying to
            <textarea
              rows={2}
              value={draftTarget}
              onChange={(event) => setDraftTarget(event.target.value)}
              placeholder="Their caption, a question under your post, or a theme…"
            />
          </label>
        </div>
        <button className="button" disabled={draft.isPending} onClick={() => draft.mutate()}>
          {draft.isPending ? "Drafting…" : "Generate drafts"}
        </button>
      </section>

      <section className="card-list">
        {drafts.data?.map((item) => (
          <article className="card" key={item.id}>
            <div>
              <strong>{item.status}</strong>
              <p>{item.text}</p>
              <p className="muted">{item.target_context}</p>
              <div className="row">
                <button
                  className="ghost"
                  onClick={() => {
                    void navigator.clipboard.writeText(item.text);
                    void markDraft(item.id, "copied");
                  }}
                >
                  Copy
                </button>
                <button className="ghost" onClick={() => void markDraft(item.id, "discarded")}>
                  Discard
                </button>
              </div>
            </div>
          </article>
        ))}
      </section>
    </main>
  );
}
