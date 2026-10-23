import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { AppConfig, ImageItem, LibraryItem } from "../types";
import { ReviewPage } from "./Review";

vi.mock("../thumbQueue", () => ({
  acquireThumbSlot: vi.fn(() => Promise.resolve(() => {})),
}));

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => Math.max(count, 1) * 168,
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({
        key: index,
        index,
        start: index * 168,
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

const LIBRARY: LibraryItem = {
  id: "lib1",
  name: "Fixture",
  root_path: "/tmp/lib",
  attested: true,
  image_count: 2,
};

const CONFIG: AppConfig = {
  gemini_policy: "text_only",
  gemini_configured: false,
  auto_publish: false,
  detector_available: true,
  suggested_library_root: "",
  attestation_statement: "",
  safety_classifier: "fake",
};

function makeImage(id: string): ImageItem {
  return {
    id,
    filename: `${id}.jpg`,
    relative_path: `photos/${id}.jpg`,
    width: 800,
    height: 600,
    orientation: "landscape",
    captured_at: null,
    camera: null,
    session_name: null,
    model_state: null,
    human_rating: null,
    effective_state: "UNREVIEWED",
    aesthetic_score: null,
    queued: false,
    queue_state: null,
    duplicate_count: 0,
    has_gps: false,
    thumbnail_url: null,
    preview_url: null,
  };
}

let imageList: ImageItem[];

function installApiMock() {
  mockApi.mockImplementation(async (path: string) => {
    if (path === "/api/libraries") return [LIBRARY];
    if (path === "/api/config") return CONFIG;
    if (path.startsWith("/api/jobs")) return [];
    if (path.startsWith("/api/images?")) {
      const qs = path.includes("?") ? path.slice(path.indexOf("?") + 1) : "";
      const params = new URLSearchParams(qs);
      const items =
        params.get("human_rating") === "safe"
          ? [makeImage("img1"), makeImage("img2"), makeImage("img3")]
          : imageList;
      return { items, next_cursor: null };
    }
    if (path.startsWith("/api/libraries/") && path.includes("/folders")) {
      return { folders: [], image_count: 0 };
    }
    const single = path.match(/^\/api\/images\/([^/]+)$/);
    if (single) {
      const found = imageList.find((row) => row.id === single[1]);
      if (found) return found;
      return makeImage(single[1]);
    }
    throw new Error(`Unhandled api path in test: ${path}`);
  });
}

function renderReview(initialPath = "/") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const router = createMemoryRouter([{ path: "/", element: <ReviewPage /> }], {
    initialEntries: [initialPath],
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...view, router };
}

function classifyDialog() {
  return screen.queryByRole("dialog", { name: "Classify image" });
}

function gridRoot() {
  const grid = document.querySelector(".grid-scroll");
  if (!grid) throw new Error("Review grid is not mounted");
  return within(grid as HTMLElement);
}

function tileButton(id: string) {
  return gridRoot().getAllByRole("button", { name: new RegExp(`photos/${id}\\.jpg`) })[0]!;
}

beforeEach(() => {
  imageList = [makeImage("img1"), makeImage("img2")];
  installApiMock();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Review classify modal", () => {
  it("does not auto-open on load when ?image= is present", async () => {
    renderReview("/?image=img1");
    await waitFor(() => expect(tileButton("img1")).toBeInTheDocument());
    expect(classifyDialog()).not.toBeInTheDocument();
  });

  it("does not open on a single tile click", async () => {
    const user = userEvent.setup();
    renderReview("/?image=img1");
    await waitFor(() => expect(tileButton("img2")).toBeInTheDocument());
    await user.click(tileButton("img2"));
    expect(classifyDialog()).not.toBeInTheDocument();
  });

  it("opens once for ?image=&open=1 and removes open from the URL", async () => {
    const { router } = renderReview("/?image=img2&open=1");
    await waitFor(() => expect(classifyDialog()).toBeInTheDocument());
    await waitFor(() => {
      expect(router.state.location.search).not.toContain("open=1");
    });
    expect(router.state.location.search).toContain("image=img2");
  });

  it("stays closed after close when the grid items refresh", async () => {
    const user = userEvent.setup();
    renderReview("/?image=img1");
    await waitFor(() => expect(tileButton("img1")).toBeInTheDocument());
    await user.click(tileButton("img1"));
    await user.keyboard("{Enter}");
    await waitFor(() => expect(classifyDialog()).toBeInTheDocument());
    await user.keyboard("{Escape}");
    await waitFor(() => expect(classifyDialog()).not.toBeInTheDocument());

    const ratingFilter = screen.getByLabelText("Human rating filter");
    await user.selectOptions(ratingFilter, "safe");
    await waitFor(() => expect(screen.getByText(/3 photos/)).toBeInTheDocument());
    expect(classifyDialog()).not.toBeInTheDocument();
  });
});
