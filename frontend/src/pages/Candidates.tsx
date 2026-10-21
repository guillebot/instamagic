import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { CandidateItem } from "../types";

export function CandidatesPage() {
  const candidates = useQuery({
    queryKey: ["candidates"],
    queryFn: () => api<CandidateItem[]>("/api/candidates"),
  });

  return (
    <main className="page">
      <h1>Next best posts</h1>
      <p>Ranked from local sharpness, composition, your rating, and near-duplicate suppression.</p>
      <div className="card-list">
        {candidates.data?.map((candidate) => (
          <article className="card" key={candidate.image.id}>
            {candidate.image.thumbnail_url ? <img src={candidate.image.thumbnail_url} alt="" /> : <div />}
            <div>
              <strong>Score {candidate.score}/100</strong>
              <p>
                {candidate.image.effective_state} · model {candidate.model_state ?? "—"}
                {candidate.crop_aspect ? ` · crop ${candidate.crop_aspect}` : ""}
              </p>
              <ul>
                {candidate.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          </article>
        ))}
        {candidates.data && candidates.data.length === 0 ? (
          <p>Rate a few photographs safe or excellent before this list can rank them.</p>
        ) : null}
      </div>
    </main>
  );
}
