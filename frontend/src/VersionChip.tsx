import { useQuery } from "@tanstack/react-query";

type HealthPayload = { status?: string; version?: string };

/** Prefer live /health (same build as API); fall back to Vite bake-in. */
export function useAppVersion(): string {
  const baked = import.meta.env.VITE_APP_VERSION?.trim() || "dev";
  const health = useQuery({
    queryKey: ["health-version"],
    queryFn: async (): Promise<HealthPayload> => {
      const response = await fetch("/health", { credentials: "include" });
      if (!response.ok) {
        throw new Error("health unavailable");
      }
      return (await response.json()) as HealthPayload;
    },
    retry: false,
    staleTime: 60_000,
  });
  const live = health.data?.version?.trim();
  return live || baked;
}

export function VersionChip({ className = "" }: { className?: string }) {
  const version = useAppVersion();
  return (
    <span className={`version-chip ${className}`.trim()} title="Build version" aria-label={`Build ${version}`}>
      {version}
    </span>
  );
}
