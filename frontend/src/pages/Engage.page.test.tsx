import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { EngageOverview, EngageTarget } from "../types";
import { EngagePage } from "./Engage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: vi.fn() };
});

const mockApi = vi.mocked(api);

function target(id: string, extra: Partial<EngageTarget> = {}): EngageTarget {
  return {
    id,
    run_date: "2026-10-24",
    source: "account",
    source_value: "alice.photo",
    username: "alice.photo",
    permalink: `https://www.instagram.com/p/${id}/`,
    media_url: `https://cdn.example/${id}.jpg`,
    media_type: "IMAGE",
    caption_snippet: "Morning light on the river",
    like_count: 42,
    comments_count: 3,
    posted_at: null,
    reason: "Similar account you follow for research",
    suggestion: "The soft light here is lovely.",
    status: "open",
    acted_at: null,
    ...extra,
  };
}

function overview(extra: Partial<EngageOverview> = {}): EngageOverview {
  return {
    today: "2026-10-24",
    list_date: "2026-10-24",
    settings: {
      enabled: true,
      daily_target: 18,
      account_cooldown_days: 7,
      run_hour_local: 8,
      hashtags_per_run: 3,
      seed_hashtags: [],
    },
    permission: { ok: true, code: null, missing: [], message: null },
    running: false,
    last_run: null,
    targets: [target("a1"), target("h1", { source: "hashtag", source_value: "streetportrait", username: null })],
    days: [{ date: "2026-10-24", done: 1, skipped: 0, total: 3 }],
    niche_hashtags: [{ tag: "streetportrait", why: "seed" }],
    hashtag_budget: { used_unique_hashtags_7d: 3, remaining_unique_hashtags_7d: 27, limit_unique_hashtags_7d: 30 },
    similar_accounts: ["alice.photo"],
    ...extra,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <EngagePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("EngagePage", () => {
  beforeEach(() => mockApi.mockReset());
  afterEach(() => cleanup());

  it("lists targets with open links and marks one done", async () => {
    mockApi.mockImplementation(async (path: string) => {
      if (path === "/api/engage") return overview();
      return target("a1", { status: "done" });
    });
    renderPage();
    expect((await screen.findAllByText("@alice.photo")).length).toBeGreaterThan(0);
    expect(screen.getByText("via #streetportrait")).toBeTruthy();
    const links = screen.getAllByRole("link", { name: "Open post" });
    expect(links[0].getAttribute("href")).toBe("https://www.instagram.com/p/a1/");
    expect(links[0].getAttribute("target")).toBe("_blank");
    expect(screen.getByText(/Today:/).textContent).toContain("1");
    await userEvent.click(screen.getAllByRole("button", { name: "Done" })[0]);
    await waitFor(() =>
      expect(mockApi).toHaveBeenCalledWith("/api/engage/targets/a1", {
        method: "PATCH",
        body: JSON.stringify({ status: "done" }),
      }),
    );
  });

  it("greys out refresh and links to reconnect when permissions are missing", async () => {
    mockApi.mockResolvedValue(
      overview({
        targets: [],
        permission: { ok: false, code: "permission_missing", missing: ["pages_read_engagement"], message: "Reconnect Instagram and grant: pages_read_engagement." },
      }),
    );
    renderPage();
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Reconnect Instagram" }).getAttribute("href")).toBe("/instagram");
    expect((screen.getByRole("button", { name: "Refresh now" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("queues a refresh", async () => {
    mockApi.mockImplementation(async (path: string) => {
      if (path === "/api/engage") return overview();
      return { queued: true, running: false };
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Refresh now" }));
    await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/api/engage/refresh", { method: "POST" }));
    expect(await screen.findByText(/Building list/)).toBeTruthy();
  });
});
