/** Operator-facing labels for `effective_state` / safety filters. Matches API presentation. */
export const LIFECYCLE_LABELS: Record<string, string> = {
  SAFE: "Safe",
  PUBLISHABLE: "Publishable",
  SAFE_AFTER_CROP: "Safe after crop",
  SAFE_AFTER_BLUR: "Safe after blur",
  REVIEW: "Review",
  REJECTED: "Rejected",
  DO_NOT_POST: "Unsafe",
  UNREVIEWED: "Unreviewed",
};

export function lifecycleLabel(state: string | null | undefined): string {
  if (!state) return "Unreviewed";
  return LIFECYCLE_LABELS[state] ?? state;
}

/** Safety badge filter values for `/api/images?model_state=` (effective_state). */
export const SAFETY_FILTERS = [
  { value: "SAFE", label: "Safe" },
  { value: "SAFE_AFTER_CROP", label: "Safe after crop" },
  { value: "SAFE_AFTER_BLUR", label: "Safe after blur" },
  { value: "PUBLISHABLE", label: "Publishable" },
  { value: "REVIEW", label: "Review" },
  { value: "REJECTED", label: "Rejected" },
  { value: "DO_NOT_POST", label: "Unsafe" },
] as const;
