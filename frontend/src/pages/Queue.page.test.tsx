import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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

function makeImage(id: string): ImageItem {
  return {
    id,
    filename: `${id}.jpg`,
    relative_path: `photos/${id}.jpg`,
    width: 400,
    height: 500,
    orientation: "portrait",
    captured_at: null,
    camera: null,
    session_name: null,
    model_state: "SAFE",
    human_rating: "safe",
    effective_state: "SAFE",
    aesthetic_score: 10,
    queued: true,
    queue_state: "APPROVED",
    duplicate_count: 0,
    has_gps: false,
    thumbnail_url: `/thumb/${id}.jpg`,
    preview_url: `/preview/${id}.jpg`,
  };
}

const QUEUE_ITEM: QueueItem = {
  id: "q-1",
  state: "APPROVED",
  caption_id: "cap-1",
  image: makeImage("img1"),
};

const CONFIG: AppConfig = {
  gemini_policy: "text_only",
  gemini_configured: true,
  auto_publish: false,
  detector_available: true,
  suggested_library_root: "",
  attestation_statement: "",
  safety_classifier: "fake",
};

let resolveHashtags: ((value: { tags: string[] }) => void) | null = null;

function installApiMock() {
  mockApi.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (path === "/api/queue") return [QUEUE_ITEM];
    if (path === "/api/config") return CONFIG;
    if (path === "/api/instagram/status") return { configured: true, provider: "graph" };
    if (path === `/api/images/${QUEUE_ITEM.image.id}/captions`) return [];
    if (path === `/api/images/${QUEUE_ITEM.image.id}/hashtags` && method === "POST") {
      return new Promise((resolve) => {
        resolveHashtags = resolve;
      });
    }
    throw new Error(`unexpected ${method} ${path}`);
  });
}

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

describe("Queue page hashtag suggest", () => {
  beforeEach(() => {
    resolveHashtags = null;
    installApiMock();
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows spinner, disables the button, then shows a result message", async () => {
    const user = userEvent.setup();
    renderQueue();
    const suggest = await screen.findByRole("button", { name: "Regenerate hashtags" });
    await user.click(suggest);

    const busy = await screen.findByRole("button", { name: /Regenerating/ });
    expect(busy).toBeDisabled();
    expect(busy.querySelector(".spinner")).toBeTruthy();

    resolveHashtags?.({ tags: ["street", "portrait"] });

    await waitFor(() => {
      expect(screen.getByText("Added 2 hashtags")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Regenerate hashtags" })).toBeEnabled();
    });
  });
});
