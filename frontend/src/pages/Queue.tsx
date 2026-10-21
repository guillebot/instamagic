import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { QueueItem } from "../types";

export function QueuePage() {
  const queryClient = useQueryClient();
  const queue = useQuery({ queryKey: ["queue"], queryFn: () => api<QueueItem[]>("/api/queue") });

  async function approve(id: string) {
    await api(`/api/queue/${id}/approve`, { method: "POST" });
    await queryClient.invalidateQueries({ queryKey: ["queue"] });
  }

  return (
    <main className="page">
      <h1>Publishing queue</h1>
      <p>Approving a candidate does not publish it. Auto publish stays off.</p>
      <div className="card-list">
        {queue.data?.map((item) => (
          <article className="card" key={item.id}>
            {item.image.thumbnail_url ? <img src={item.image.thumbnail_url} alt="" /> : <div />}
            <div>
              <strong>{item.image.filename}</strong>
              <p>
                {item.state} · {item.image.effective_state} · model {item.image.model_state}
              </p>
              {item.state === "CANDIDATE" ? (
                <button className="button" onClick={() => void approve(item.id)}>
                  Approve for later
                </button>
              ) : null}
            </div>
          </article>
        ))}
        {queue.data && queue.data.length === 0 ? <p>Nothing queued yet. Press P on a reviewed photograph.</p> : null}
      </div>
    </main>
  );
}
