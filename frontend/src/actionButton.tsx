import { useCallback, useEffect, useState, type ReactNode } from "react";

export function ButtonSpinner() {
  return <span className="spinner" aria-hidden="true" />;
}

export function PendingButtonLabel({
  pending,
  pendingLabel,
  label,
}: {
  pending: boolean;
  pendingLabel: string;
  label: ReactNode;
}) {
  if (pending) {
    return (
      <>
        <ButtonSpinner /> {pendingLabel}
      </>
    );
  }
  return <>{label}</>;
}

export function ActionStatus({
  message,
  variant = "help",
}: {
  message: string;
  variant?: "help" | "error";
}) {
  return (
    <p
      className={variant === "error" ? "error" : "help action-status"}
      role="status"
      aria-live="polite"
    >
      {message}
    </p>
  );
}

/** Short-lived success copy; errors should use dedicated error state instead. */
export function useBriefStatus(clearMs = 5000) {
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    if (!status) return;
    const timer = window.setTimeout(() => setStatus(null), clearMs);
    return () => window.clearTimeout(timer);
  }, [status, clearMs]);

  const clearStatus = useCallback(() => setStatus(null), []);
  return { status, setStatus, clearStatus };
}
