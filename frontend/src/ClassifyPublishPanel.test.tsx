import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ImageItem } from "./types";

const calls: { path: string; method: string }[] = [];
let responses: Record<string, unknown> = {};

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    api: vi.fn(async (path: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ path, method });
      const key = `${method} ${path}`;
      const value = responses[key];
      if (value instanceof Error) throw value;
      if (value === undefined) throw new actual.ApiError(404, `no mock for ${key}`);
      return typeof value === "function" ? (value as () => unknown)() : value;
    }),
  };
});

import { ApiError } from "./api";
import { ClassifyPublishPanel } from "./ClassifyPublishPanel";

const IMG = "img-1";
const readiness = {
  publishing_allowed: true,
  publishing_reasons: [],
  caption_generation_allowed: true,
  caption_generation_reasons: [],
  library_attested: true,
  queue_item_id: null,
  queue_state: null,
  scheduled_at: null,
  published_at: null,
  instagram_configured: true,
  instagram_target_username: "foto.sch",
  feed_preview_url: null,
  uses_approved_crop: false,
};
const captions = [{ id: "cap-1", style: "short", language: "es", text: "Hola", status: "draft" }];

function setup(overrides: Record<string, unknown> = {}) {
  calls.length = 0;
  responses = {
    [`GET /api/images/${IMG}/publish-readiness`]: readiness,
    [`GET /api/images/${IMG}/captions`]: captions,
    "GET /api/instagram/status": { configured: true, token_configured: true, business_account_id_configured: true },
    "GET /api/health/status": { checks: [{ id: "instagram", status: "ok", detail: "" }] },
    "GET /api/posting-schedule/config": {
      timezone: "UTC",
      weekday_slots: {},
      max_posts_per_day: 2,
      min_gap_hours: 4,
      paused: false,
      story_share_default: false,
    },
    ...overrides,
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ClassifyPublishPanel
          image={{ id: IMG, relative_path: "a.jpg" } as ImageItem}
          geminiPolicy="text_only"
          onImageUpdated={() => undefined}
          onStatus={() => undefined}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const publishCalls = () => calls.filter((c) => c.method === "POST");

describe("ClassifyPublishPanel Post now", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("disables Post now with an inline reconnect link when Instagram is disconnected", async () => {
    setup({ "GET /api/instagram/status": { configured: true, token_configured: false } });
    expect(await screen.findByText(/Instagram disconnected — reconnect to post/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Reconnect Instagram" }).getAttribute("href")).toBe("/instagram");
    expect((screen.getByRole("button", { name: "Post now" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows why Post now is unavailable inline (blocked image)", async () => {
    setup({
      [`GET /api/images/${IMG}/publish-readiness`]: {
        ...readiness,
        publishing_allowed: false,
        publishing_reasons: ["The local model marked this image DO_NOT_POST."],
      },
    });
    expect(await screen.findByText(/Post now unavailable: The local model marked this image DO_NOT_POST/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Post now" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("requires confirmation, then queues, approves, publishes and shows the permalink", async () => {
    setup({
      "POST /api/queue": { id: "q1", state: "CANDIDATE" },
      "POST /api/queue/q1/approve": { id: "q1", state: "APPROVED" },
      "PATCH /api/queue/q1": { id: "q1", state: "APPROVED" },
      "POST /api/queue/q1/publish": { id: "q1", state: "PUBLISHED", permalink: "https://www.instagram.com/p/abc/" },
    });
    const post = await screen.findByRole("button", { name: "Post now" });
    await waitFor(() => expect((post as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(post);
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    expect(screen.getByText(/publishes immediately and publicly/)).toBeTruthy();
    expect(publishCalls()).toHaveLength(0);
    // after approve the re-check sees APPROVED
    responses[`GET /api/images/${IMG}/publish-readiness`] = { ...readiness, queue_item_id: "q1", queue_state: "APPROVED" };
    fireEvent.click(screen.getByRole("button", { name: "Publish to @foto.sch" }));
    expect((await screen.findAllByRole("link", { name: "https://www.instagram.com/p/abc/" })).length).toBeGreaterThan(0);
    expect(publishCalls().map((c) => c.path)).toEqual([
      "/api/queue",
      "/api/queue/q1/approve",
      "/api/queue/q1/publish",
    ]);
  });

  it("confirm dialog mentions Story when also share is enabled", async () => {
    setup({
      "GET /api/posting-schedule/config": {
        timezone: "UTC",
        weekday_slots: {},
        max_posts_per_day: 2,
        min_gap_hours: 4,
        paused: false,
        story_share_default: true,
      },
    });
    const post = await screen.findByRole("button", { name: "Post now" });
    await waitFor(() => expect((post as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(post);
    expect(await screen.findByText(/will also publish an Instagram Story/)).toBeTruthy();
  });

  it("cancel sends nothing", async () => {
    setup();
    const post = await screen.findByRole("button", { name: "Post now" });
    await waitFor(() => expect((post as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(post);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(publishCalls()).toHaveLength(0);
  });

  it("surfaces the exact backend refusal inside the confirm dialog", async () => {
    const approvedReadiness = { ...readiness, queue_item_id: "q1", queue_state: "APPROVED" };
    setup({
      [`GET /api/images/${IMG}/publish-readiness`]: approvedReadiness,
      "PATCH /api/queue/q1": { id: "q1", state: "APPROVED" },
      "POST /api/queue/q1/publish": new ApiError(409, "DO_NOT_POST requires verified blur unlock before publishing."),
    });
    const post = await screen.findByRole("button", { name: "Post now" });
    await waitFor(() => expect((post as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(post);
    fireEvent.click(screen.getByRole("button", { name: "Publish to @foto.sch" }));
    const alerts = await screen.findAllByText("Blocked (409): DO_NOT_POST requires verified blur unlock before publishing.");
    expect(alerts.length).toBeGreaterThan(0);
  });
});
