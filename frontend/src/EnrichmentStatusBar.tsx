import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api } from "./api";
import { enrichmentEtaMinutes, enrichmentLine, type EnrichmentStatus } from "./enrichmentStatus";

export function EnrichmentStatusBar() {
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const status = useQuery({
    queryKey: ["enrichment-status"],
    queryFn: () => api<EnrichmentStatus>("/api/enrichment/status"),
    refetchInterval: (query) => (query.state.data?.state === "running" ? 10000 : 30000),
  });
  const act = useMutation({
    mutationFn: (action: { path: string; method?: string; body?: unknown }) =>
      api<EnrichmentStatus>(action.path, {
        method: action.method ?? "POST",
        body: action.body === undefined ? undefined : JSON.stringify(action.body),
      }),
    onSuccess: (data) => {
      setError("");
      queryClient.setQueryData(["enrichment-status"], data);
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Enrichment update failed."),
  });
  const data = status.data;
  if (!data) return null;
  const eta = enrichmentEtaMinutes(data);
  return (
    <section className="panel enrichment-status" aria-label="Background enrichment">
      <div className="row">
        <strong>{enrichmentLine(data)}</strong>
        {eta ? <span className="muted">~{eta} min left</span> : null}
        {data.errors ? <span className="muted">{data.errors} errors</span> : null}
        <span className="muted">
          {data.calls_today}/{data.daily_cap} Gemini calls today
        </span>
        <label className="row">
          <input
            type="checkbox"
            checked={data.enabled}
            disabled={act.isPending}
            onChange={(event) =>
              act.mutate({ path: "/api/enrichment/settings", method: "PUT", body: { enabled: event.target.checked } })
            }
          />
          Auto captions &amp; hashtags
        </label>
        {data.enabled ? (
          <button
            type="button"
            className="ghost"
            disabled={act.isPending}
            onClick={() => act.mutate({ path: data.paused ? "/api/enrichment/resume" : "/api/enrichment/pause" })}
          >
            {data.paused ? "Resume" : "Pause"}
          </button>
        ) : null}
        {data.errors ? (
          <button
            type="button"
            className="ghost"
            disabled={act.isPending}
            onClick={() => act.mutate({ path: "/api/enrichment/retry" })}
          >
            Retry errors
          </button>
        ) : null}
      </div>
      <p className="muted">
        Labels, captions and hashtags are prepared in the background for photos you rated Safe/Excellent that pass the
        blur/crop gates. Existing captions are never replaced.
      </p>
      {error ? <p className="error">{error}</p> : null}
    </section>
  );
}
