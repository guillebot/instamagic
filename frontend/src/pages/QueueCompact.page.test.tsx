import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { AppConfig, ImageItem, QueueItem } from "../types";
import { QueuePage } from "./Queue";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: vi.fn() };
});
const mockApi = vi.mocked(api);

function img(id: string): ImageItem {
  return {
    id, filename: `${id}.jpg`, relative_path: `p/${id}.jpg`, width: 400, height: 500, orientation: "portrait",
    captured_at: null, camera: null, session_name: null, model_state: "SAFE", human_rating: "safe",
    effective_state: "SAFE", aesthetic_score: 10, queued: true, queue_state: "APPROVED", duplicate_count: 0,
    has_gps: false, thumbnail_url: `/thumb/${id}.jpg`, preview_url: `/preview/${id}.jpg`,
  };
}

const ITEMS: QueueItem[] = [
  { id: "q-late", state: "APPROVED", caption_id: "c-late", scheduled_at: "2026-10-26T22:00:00Z", image: img("late") },
  { id: "q-early", state: "APPROVED", caption_id: "c-early", scheduled_at: "2026-10-26T14:00:00Z", image: img("early") },
  { id: "q-todo", state: "CANDIDATE", caption_id: null, image: img("todo") },
];
const CONFIG: AppConfig = {
  gemini_policy: "text_only", gemini_configured: true, auto_publish: false, detector_available: true,
  suggested_library_root: "", attestation_statement: "", safety_classifier: "fake",
};

function renderQueue() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <QueuePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Queue compact rows", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockApi.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/api/queue") return ITEMS;
      if (path === "/api/queue/q-todo/approve" && init?.method === "POST") return { ...ITEMS[2], state: "APPROVED" };
      if (path === "/api/config") return CONFIG;
      if (path === "/api/instagram/status") return { configured: true, provider: "graph" };
      const m = path.match(/^\/api\/images\/(\w+)\/captions$/);
      if (m) return [{ id: `c-${m[1]}`, style: "short", language: "en", text: `Quiet light ${m[1]}.\n#bnw #monochrome #art`, status: "draft" }];
      throw new Error(`unexpected ${path}`);
    });
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("collapses auto-scheduled items, sorts them by time in ART, keeps needs-action expanded", async () => {
    renderQueue();
    const scheduled = await screen.findByRole("region", { name: "Scheduled" });
    const rows = within(scheduled).getAllByTestId("queue-row-compact");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("early.jpg");
    expect(rows[0]).toHaveTextContent("11:00 ART");
    expect(rows[0]).toHaveTextContent("Feed");
    expect(await within(rows[0]).findByText(/Quiet light early\./)).toBeInTheDocument();
    expect(rows[0]).toHaveTextContent("3 hashtags");
    const needs = screen.getByRole("region", { name: "Needs action" });
    expect(within(needs).queryByTestId("queue-row-compact")).toBeNull();
    expect(within(needs).getByRole("button", { name: "Approve for publish" })).toBeInTheDocument();
  });

  it("expand/collapse all, per-row chevron, and persisted Compact view", async () => {
    const user = userEvent.setup();
    renderQueue();
    await screen.findByRole("region", { name: "Scheduled" });
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    expect(screen.queryAllByTestId("queue-row-compact")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(screen.getAllByTestId("queue-row-compact")).toHaveLength(3);
    await user.click(screen.getByRole("button", { name: "Expand todo.jpg" }));
    expect(screen.getAllByTestId("queue-row-compact")).toHaveLength(2);
    await user.click(screen.getByRole("checkbox", { name: "Compact view" }));
    expect(window.localStorage.getItem("instamagic.queue.compactView")).toBe("true");
    expect(screen.getAllByTestId("queue-row-compact")).toHaveLength(3);
  });

  it("minimises an item right after it is approved", async () => {
    const user = userEvent.setup();
    renderQueue();
    const needs = await screen.findByRole("region", { name: "Needs action" });
    await user.click(within(needs).getByRole("button", { name: "Approve for publish" }));
    expect(await screen.findAllByTestId("queue-row-compact")).toHaveLength(3);
    expect(mockApi).toHaveBeenCalledWith("/api/queue/q-todo/approve", { method: "POST" });
  });
});
