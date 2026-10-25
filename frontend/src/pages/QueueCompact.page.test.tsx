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
      if (path === "/api/queue/q-todo/schedule" && init?.method === "POST")
        return { ...ITEMS[2], state: "APPROVED", scheduled_at: "2026-10-27T22:00:00Z" };
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
    expect(within(needs).getByRole("button", { name: "Approve & auto-schedule" })).toBeInTheDocument();
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

  it("Approve & auto-schedule approves, takes the next slot, then minimises", async () => {
    const user = userEvent.setup();
    renderQueue();
    const needs = await screen.findByRole("region", { name: "Needs action" });
    const btn = within(needs).getByRole("button", { name: "Approve & auto-schedule" });
    expect(btn).toBeDisabled();
    expect(within(needs).getByText("Choose a caption below to schedule.")).toBeInTheDocument();
    await user.click(await within(needs).findByRole("radio"));
    expect(btn).toBeEnabled();
    await user.click(btn);
    expect(await screen.findAllByTestId("queue-row-compact")).toHaveLength(3);
    expect(mockApi).toHaveBeenCalledWith("/api/queue/q-todo/approve", { method: "POST" });
    expect(mockApi).toHaveBeenCalledWith(
      "/api/queue/q-todo/schedule",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"auto_slot"') }),
    );
  });

  it("Approve only keeps the card open; Pick time offers an explicit time", async () => {
    const user = userEvent.setup();
    renderQueue();
    const needs = await screen.findByRole("region", { name: "Needs action" });
    await user.click(within(needs).getByRole("button", { name: "Approve only" }));
    expect(mockApi).toHaveBeenCalledWith("/api/queue/q-todo/approve", { method: "POST" });
    expect(within(needs).queryByTestId("queue-row-compact")).toBeNull();
    await user.click(within(needs).getByRole("button", { name: "Pick time…" }));
    expect(within(needs).getByLabelText(/Publish time/)).toBeInTheDocument();
    expect(within(needs).getByRole("button", { name: "Approve & schedule at this time" })).toBeDisabled();
  });

  it("collapses whole groups from their headers and remembers it", async () => {
    const user = userEvent.setup();
    renderQueue();
    const scheduled = await screen.findByRole("region", { name: "Scheduled" });
    const schedToggle = within(scheduled).getByRole("button", { name: /Scheduled \(2\)/ });
    expect(schedToggle).toHaveAttribute("aria-expanded", "true");
    expect(schedToggle).toHaveTextContent(/next: \w{3} 11:00 ART/);
    await user.click(schedToggle);
    expect(schedToggle).toHaveAttribute("aria-expanded", "false");
    expect(within(scheduled).queryAllByTestId("queue-row-compact")).toHaveLength(0);
    expect(window.localStorage.getItem("instamagic.queue.groupCollapsed.scheduled")).toBe("true");

    const needs = screen.getByRole("region", { name: "Needs action" });
    const needsToggle = within(needs).getByRole("button", { name: /Needs action \(1\)/ });
    needsToggle.focus();
    await user.keyboard("{Enter}");
    expect(needsToggle).toHaveAttribute("aria-expanded", "false");
    expect(within(needs).queryByRole("button", { name: "Approve & auto-schedule" })).toBeNull();

    cleanup();
    renderQueue();
    const again = await screen.findByRole("region", { name: "Scheduled" });
    expect(within(again).getByRole("button", { name: /Scheduled \(2\)/ })).toHaveAttribute("aria-expanded", "false");
    await user.click(screen.getByRole("button", { name: /Needs action \(1\)/ }));
    expect(screen.getByRole("button", { name: "Approve & auto-schedule" })).toBeInTheDocument();
  });
});
