import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "./api";

export type HealthLevel = "ok" | "warning" | "down" | "unknown";

export type HealthCheck = {
  id: string;
  label: string;
  status: HealthLevel;
  detail: string;
};

export type HealthStatusPayload = {
  overall: HealthLevel;
  checks: HealthCheck[];
  cached_seconds?: number;
};

const STATUS_LABEL: Record<HealthLevel, string> = {
  ok: "OK",
  warning: "Degraded",
  down: "Down",
  unknown: "Unknown",
};

function statusClass(status: HealthLevel): string {
  return `health-dot health-dot--${status}`;
}

function HealthItem({
  check,
  href,
}: {
  check: HealthCheck;
  href?: string;
}) {
  const [open, setOpen] = useState(false);
  const popoverId = useId();
  const statusText = STATUS_LABEL[check.status] ?? check.status;

  const inner = (
    <>
      <span className={statusClass(check.status)} aria-hidden="true" />
      <span className="health-label">{check.label}</span>
      <span className="health-status-text">{statusText}</span>
    </>
  );

  const body = (
    <div className="health-item-wrap">
      {href ? (
        <Link
          to={href}
          className="health-item health-item--link"
          aria-label={`${check.label}: ${statusText}. ${check.detail}`}
          title={check.detail}
          onClick={(event) => {
            if (event.metaKey || event.ctrlKey) return;
            event.preventDefault();
            setOpen((value) => !value);
          }}
        >
          {inner}
        </Link>
      ) : (
        <button
          type="button"
          className="health-item"
          aria-expanded={open}
          aria-controls={popoverId}
          aria-label={`${check.label}: ${statusText}. ${check.detail}`}
          title={check.detail}
          onClick={() => setOpen((value) => !value)}
        >
          {inner}
        </button>
      )}
      {open ? (
        <div className="health-popover" id={popoverId} role="tooltip">
          <strong>{check.label}</strong>
          <p>{check.detail}</p>
          {href ? (
            <Link to={href} className="health-popover-link" onClick={() => setOpen(false)}>
              Open settings
            </Link>
          ) : null}
        </div>
      ) : null}
    </div>
  );

  return body;
}

export function SystemHealthBar() {
  const [tabVisible, setTabVisible] = useState(() => !document.hidden);

  useEffect(() => {
    const onVisibility = () => setTabVisible(!document.hidden);
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  const health = useQuery({
    queryKey: ["health-status"],
    queryFn: () => api<HealthStatusPayload>("/api/health/status"),
    refetchInterval: tabVisible ? 30_000 : false,
    staleTime: 20_000,
    retry: 1,
  });

  if (health.isLoading && !health.data) {
    return (
      <div className="health-bar" aria-live="polite" aria-busy="true">
        <span className="health-item health-item--static">
          <span className="health-dot health-dot--unknown" aria-hidden="true" />
          <span className="health-label">System</span>
          <span className="health-status-text">Loading</span>
        </span>
      </div>
    );
  }

  if (health.isError && !health.data) {
    return (
      <div className="health-bar" aria-live="polite">
        <span className="health-item health-item--static">
          <span className="health-dot health-dot--down" aria-hidden="true" />
          <span className="health-label">System</span>
          <span className="health-status-text">Unavailable</span>
        </span>
      </div>
    );
  }

  const payload = health.data;
  if (!payload) {
    return null;
  }

  const overall = payload.overall;
  const overallText = STATUS_LABEL[overall] ?? overall;

  return (
    <div className="health-bar" aria-live="polite">
      <span
        className="health-item health-item--static health-overall"
        title={`Overall: ${overallText}`}
        aria-label={`Overall system health: ${overallText}`}
      >
        <span className={statusClass(overall)} aria-hidden="true" />
        <span className="health-label">System</span>
        <span className="health-status-text">{overallText}</span>
      </span>
      {payload.checks.map((check) => (
        <HealthItem
          key={check.id}
          check={check}
          href={check.id === "instagram" ? "/instagram" : undefined}
        />
      ))}
    </div>
  );
}
