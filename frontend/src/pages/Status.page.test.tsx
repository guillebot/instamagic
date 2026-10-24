import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { StatusPage, type AppStatus } from "./Status";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: vi.fn() };
});

const mockApi = vi.mocked(api);

function payload(): AppStatus {
  return {
    generated_at: "2026-10-24T21:00:00+00:00",
    cached_seconds: 15,
    health: { overall: "ok", checks: [{ id: "instagram", label: "Instagram", status: "ok", detail: "@foto.sch" }] },
    scan: {
      current: {
        id: "j1", status: "running", stage: "uniqueness", detail: "Checking similar photos 40/80",
        files_seen: 80, files_processed: 20, files_skipped: 60, files_failed: 0, files_total: 80, expected_total: 80,
        started_at: "2026-10-24T14:05:00+00:00", finished_at: null, elapsed_seconds: 3600, files_per_minute: 2,
        eta_seconds: null, uniqueness: { done: 40, total: 80 }, error: null,
      },
      last_completed_at: null, active_jobs: { running: 1 }, active_by_kind: [],
    },
    photos: {
      total: 100, active: 80, skipped: { duplicate: 20 }, locally_scanned: 80, local_verdicts: { SAFE: 50, REVIEW: 30 },
      human_ratings: { safe: 10, none: 70 }, rated: 10, rejected: 0, curation_picks: {}, curated: 0,
      blurred_images: 3, blur_unlock: {}, blur_verified: 0, duplicates_found: 20, ingested_24h: 5,
    },
    enrichment: { error: "unavailable (RuntimeError)" } as AppStatus["enrichment"],
    queue: {
      states: { APPROVED: 4, PUBLISHED: 2 }, approved_unscheduled: 1,
      upcoming: [{ id: "q1", filename: "sunset.jpg", state: "APPROVED", media_type: "IMAGE", scheduled_at: "2026-10-25T13:00:00+00:00", story: false }],
      open_slots: ["2026-10-25T22:00:00+00:00"],
      schedule: { timezone: "America/Argentina/Buenos_Aires", paused: false, max_posts_per_day: 2, min_gap_hours: 4 },
    },
    publish: {
      attempts: 3, succeeded: 2, failed: 1, posts_total: 2, by_kind: {}, last_attempt_at: null, last_failure: null,
      last_published: { at: "2026-10-23T13:00:00+00:00", permalink: "https://www.instagram.com/p/x/", media_type: "IMAGE" },
      per_day: [{ date: "2026-10-24", count: 1 }],
    },
    instagram: {
      configured: true, username: "foto.sch", expected_username: "foto.sch", followers: 120, media_count: 30,
      account_fetched_at: null, permissions_granted: 6, permissions_missing: [], insights_permission: true,
      comments_permission: true, auto_publish: true, insights_last_sync_at: null,
    },
    sync: { insights_tick_at: null, comments_tick_at: null, last_comment_synced_at: null, inbox_open: 2 },
    engage: { today: "2026-10-24", counts: { done: 3, open: 7 }, total: 10, enabled: true, daily_target: 10, run_hour_local: 9, last_run: null },
    workers: { available: true, version: "sha-abc", queues: [{ name: "social", heartbeat_age_seconds: 5, queued: 0, started: 0, failed: 0, workers: 1 }] },
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <StatusPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("StatusPage", () => {
  afterEach(() => {
    cleanup();
    mockApi.mockReset();
  });

  it("renders sections from /api/status and tolerates a failed section", async () => {
    mockApi.mockResolvedValue(payload());
    renderPage();
    expect((await screen.findAllByText("@foto.sch")).length).toBeGreaterThan(0);
    expect(mockApi).toHaveBeenCalledWith("/api/status");
    expect(screen.getByRole("progressbar", { name: "Duplicate check" }).getAttribute("aria-valuenow")).toBe("50");
    expect(screen.getByText("sunset.jpg")).toBeTruthy();
    expect(screen.getByText(/Unavailable: unavailable \(RuntimeError\)/)).toBeTruthy();
    expect(screen.getByText("social")).toBeTruthy();
  });
});
