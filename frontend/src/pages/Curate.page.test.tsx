import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { AppConfig, CurationBatch, CurationPick, ImageItem, LibraryItem } from "../types";
import { CurationPicksWorkspace } from "./Curate";

vi.mock("../thumbQueue", () => ({
  acquireThumbSlot: vi.fn(() => Promise.resolve(() => {})),
}));

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => Math.max(count, 1) * 156,
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({
        key: index,
        index,
        start: index * 156,
      })),
    measure: vi.fn(),
    scrollToIndex: vi.fn(),
  }),
}));

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
    human_rating: "excellent",
    effective_state: "SAFE",
    aesthetic_score: 10,
    queued: false,
    queue_state: null,
    duplicate_count: 0,
    has_gps: false,
    thumbnail_url: `/thumb/${id}.jpg`,
    preview_url: `/preview/${id}.jpg`,
  };
}

function makePick(id: string, sortOrder: number, status = "suggested"): CurationPick {
  const image = makeImage(id);
  return {
    id: `pick-${id}`,
    batch_id: "batch-1",
    sort_order: sortOrder,
    gemini_rank: sortOrder,
    local_score: 90 - sortOrder,
    reasons: ["Strong composition"],
    suggested_tags: ["portrait"],
    status,
    preferred_caption_id: "cap-1",
    image,
    captions: [
      {
        id: "cap-1",
        style: "warm",
        language: "en",
        text: `Caption for ${id}`,
        status: "draft",
      },
    ],
  };
}

const BATCH: CurationBatch = {
  id: "batch-1",
  library_id: null,
  folder: null,
  status: "ready",
  limit: 0,
  detail: null,
  error: null,
  created_at: new Date().toISOString(),
  finished_at: new Date().toISOString(),
  pick_count: 3,
  picks: [makePick("a", 1), makePick("b", 2), makePick("c", 3)],
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

const LIBRARY: LibraryItem = {
  id: "lib1",
  name: "Fixture",
  root_path: "/tmp",
  attested: true,
  image_count: 3,
};

let picksState: CurationPick[];

function installApiMock() {
  mockApi.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method;
    const rawBody = typeof init?.body === "string" ? init.body : "{}";
    if (path === "/api/config") return CONFIG;
    if (path === "/api/libraries") return [LIBRARY];
    if (path === "/api/curation") return [{ ...BATCH, picks: undefined }];
    if (path === `/api/curation/${BATCH.id}`) return { ...BATCH, picks: picksState };
    if (path.startsWith("/api/curation/picks/") && method === "PATCH") {
      const pickId = path.split("/").pop()!;
      const body = JSON.parse(rawBody) as Record<string, unknown>;
      const pick = picksState.find((row) => row.id === pickId);
      if (!pick) throw new Error("missing pick");
      const next = { ...pick };
      if (body.status) next.status = String(body.status);
      if (body.suggested_tags) next.suggested_tags = body.suggested_tags as string[];
      if (body.caption_text) {
        next.captions = next.captions.map((row) =>
          row.id === next.preferred_caption_id ? { ...row, text: String(body.caption_text) } : row,
        );
      }
      picksState = picksState.map((row) => (row.id === pickId ? next : row));
      return next;
    }
    if (path === "/api/curation/queue" && method === "POST") {
      const body = JSON.parse(rawBody) as { pick_ids: string[] };
      const pickId = body.pick_ids[0];
      picksState = picksState.map((row) => (row.id === pickId ? { ...row, status: "queued" } : row));
      const pick = picksState.find((row) => row.id === pickId)!;
      return [{ id: "queue-1", state: "CANDIDATE", image: pick.image }];
    }
    if (path.includes("/undo-queue") && method === "POST") {
      const pickId = path.split("/")[4]!;
      picksState = picksState.map((row) => (row.id === pickId ? { ...row, status: "suggested" } : row));
      return picksState.find((row) => row.id === pickId);
    }
    throw new Error(`Unhandled api path: ${path}`);
  });
}

function renderWorkspace() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <CurationPicksWorkspace
        batch={{ ...BATCH, picks: picksState }}
        geminiPolicy="text_only"
        serverPickCount={picksState.length}
        onRefreshBatch={async () => {}}
      />
    </QueryClientProvider>,
  );
}

function gridRoot() {
  const grid = document.querySelector(".curate-grid-scroll");
  if (!grid) throw new Error("Curate grid missing");
  return within(grid as HTMLElement);
}

function tile(id: string) {
  return gridRoot().getByRole("button", { name: new RegExp(`${id}\\.jpg`) });
}

function curateDialog() {
  return screen.queryByRole("dialog", { name: "Curate pick" });
}

beforeEach(() => {
  picksState = BATCH.picks.map((row) => ({ ...row }));
  installApiMock();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Curate picks workspace", () => {
  it("renders thumbnail tiles in the grid", async () => {
    renderWorkspace();
    await waitFor(() => expect(tile("a")).toBeInTheDocument());
    expect(tile("b")).toBeInTheDocument();
    expect(curateDialog()).not.toBeInTheDocument();
  });

  it("opens the modal on tile click, not on load", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await waitFor(() => expect(tile("a")).toBeInTheDocument());
    expect(curateDialog()).not.toBeInTheDocument();
    await user.click(tile("b"));
    await waitFor(() => expect(curateDialog()).toBeInTheDocument());
  });

  it("shows visible keyboard hints on curate modal actions", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await waitFor(() => expect(tile("a")).toBeInTheDocument());
    await user.click(tile("a"));
    await waitFor(() => expect(curateDialog()).toBeInTheDocument());
    const dialog = curateDialog()!;
    const shortcuts = Array.from(dialog.querySelectorAll("kbd.key-hint")).map((el) => el.textContent);
    expect(shortcuts).toEqual(expect.arrayContaining(["Q", "R", "S", "Esc", "←", "→"]));
    expect(dialog.querySelector(".modal-shortcut-legend")).toBeTruthy();
  });

  it("send to queue saves caption/tags and advances to the next pending pick", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await waitFor(() => expect(tile("a")).toBeInTheDocument());
    await user.click(tile("a"));
    await waitFor(() => expect(curateDialog()).toBeInTheDocument());

    const caption = screen.getByLabelText("Caption");
    await user.clear(caption);
    await user.type(caption, "Edited caption text");

    await user.click(screen.getByRole("button", { name: "Send to queue" }));

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith(
        "/api/curation/picks/pick-a",
        expect.objectContaining({
          method: "PATCH",
          body: expect.stringContaining("Edited caption text"),
        }),
      );
    });
    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith(
        "/api/curation/queue",
        expect.objectContaining({ method: "POST" }),
      );
    });
    await waitFor(() => expect(screen.getByDisplayValue("Caption for b")).toBeInTheDocument());
  });

  it("reject advances to the next pending pick", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await waitFor(() => expect(tile("a")).toBeInTheDocument());
    await user.click(tile("a"));
    await waitFor(() => expect(curateDialog()).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(screen.getByDisplayValue("Caption for b")).toBeInTheDocument());
  });

  it("ignores Q/R shortcuts while typing in the caption field", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await waitFor(() => expect(tile("a")).toBeInTheDocument());
    await user.click(tile("a"));
    await waitFor(() => expect(curateDialog()).toBeInTheDocument());
    const caption = screen.getByLabelText("Caption");
    await user.click(caption);
    await user.type(caption, "q");
    const queueCalls = mockApi.mock.calls.filter(([path]) => path === "/api/curation/queue");
    expect(queueCalls.length).toBe(0);
  });
});
