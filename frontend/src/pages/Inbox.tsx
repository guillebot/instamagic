import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api } from "../api";
import type { InstagramConnectionStatus } from "../types";

type InboxComment = {
  id: string;
  author_username: string | null;
  text: string;
  suggested_reply: string | null;
  status: string;
  commented_at: string | null;
};

export function InboxPage() {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["instagram-status"],
    queryFn: () => api<InstagramConnectionStatus>("/api/instagram/status"),
  });
  const comments = useQuery({
    queryKey: ["inbox", "open"],
    queryFn: () => api<InboxComment[]>("/api/inbox/comments?status=open"),
  });
  const sync = useMutation({
    mutationFn: () => api("/api/inbox/comments/sync", { method: "POST" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["inbox"] }),
  });
  const [edits, setEdits] = useState<Record<string, string>>({});

  const suggest = useMutation({
    mutationFn: (id: string) =>
      api<{ suggested_reply: string }>(`/api/inbox/comments/${id}/suggest-reply`, { method: "POST" }),
    onSuccess: (body, id) => {
      setEdits((prev) => ({ ...prev, [id]: body.suggested_reply }));
      void queryClient.invalidateQueries({ queryKey: ["inbox"] });
    },
  });

  const reply = useMutation({
    mutationFn: ({ id, message }: { id: string; message: string }) =>
      api(`/api/inbox/comments/${id}/reply`, { method: "POST", body: JSON.stringify({ message }) }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["inbox"] }),
    onError: (err) => alert(err instanceof ApiError ? err.message : "Reply failed"),
  });

  return (
    <main className="page">
      <h1>Comment inbox</h1>
      <p className="muted">
        Unanswered comments on your posts. Suggested replies are drafts — click <strong>Reply</strong> to post via
        Graph (never automatic).
      </p>
      {status.data?.comments_reconnect_required ? (
        <p className="banner error">Reconnect to grant instagram_manage_comments.</p>
      ) : null}
      <button className="primary" onClick={() => sync.mutate()} disabled={sync.isPending}>
        {sync.isPending ? "Syncing…" : "Sync comments"}
      </button>
      {comments.isLoading ? <p>Loading…</p> : null}
      <section className="card-list">
        {comments.data?.map((row) => {
          const draft = edits[row.id] ?? row.suggested_reply ?? "";
          return (
            <article className="card" key={row.id}>
              <strong>@{row.author_username ?? "unknown"}</strong>
              <p>{row.text}</p>
              <textarea
                rows={3}
                value={draft}
                onChange={(e) => setEdits({ ...edits, [row.id]: e.target.value })}
              />
              <div className="row">
                <button className="ghost" type="button" onClick={() => suggest.mutate(row.id)}>
                  Suggest reply
                </button>
                <button
                  className="button"
                  type="button"
                  disabled={!draft.trim() || reply.isPending}
                  onClick={() => reply.mutate({ id: row.id, message: draft })}
                >
                  Reply
                </button>
                <button
                  className="ghost"
                  type="button"
                  onClick={() =>
                    void api(`/api/inbox/comments/${row.id}`, {
                      method: "PATCH",
                      body: JSON.stringify({ status: "dismissed" }),
                    }).then(() => queryClient.invalidateQueries({ queryKey: ["inbox"] }))
                  }
                >
                  Mark done
                </button>
                <button
                  className="ghost"
                  type="button"
                  onClick={() => void navigator.clipboard.writeText(draft)}
                >
                  Copy
                </button>
              </div>
            </article>
          );
        })}
        {comments.data?.length === 0 ? <p className="muted">No open comments — sync after publishing.</p> : null}
      </section>
    </main>
  );
}
