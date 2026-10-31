import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { MusicLibrary, QueueItem } from "../types";
import { MusicPage } from "./Music";
import { GrowthBadges } from "./Queue";
import { splitTimes } from "./Schedule";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: vi.fn() };
});

const mockApi = vi.mocked(api);

function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  mockApi.mockReset();
});

describe("Reel schedule UI", () => {
  it("parses reel slot times", () => {
    expect(splitTimes(" 15:00, 21:00  ")).toEqual(["15:00", "21:00"]);
  });

  it("badges a Reel candidate as needing approval and warns when silent", () => {
    const item = {
      id: "r1",
      state: "CANDIDATE",
      media_type: "REELS",
      reel_auto: true,
      reel_music: { track: null, silent: true, reason: "library_empty", warning: "No music in the library" },
      image: {},
    } as unknown as QueueItem;
    wrap(<GrowthBadges item={item} />);
    expect(screen.getByText("Reel · needs approval")).toBeTruthy();
    expect(screen.getByText("Silent Reel")).toBeTruthy();
  });

  it("feed photos never show a Reel slot badge", () => {
    const item = { id: "p1", state: "APPROVED", media_type: "IMAGE", is_reel_slot: true, image: {} } as unknown as QueueItem;
    wrap(<GrowthBadges item={item} />);
    expect(screen.queryByText(/Reel/)).toBeNull();
  });

  it("music library shows the royalty-free note and empty warning", async () => {
    const library: MusicLibrary = {
      tracks: [],
      enabled_count: 0,
      pick_mode: "rotate",
      license_note: "Only upload royalty-free music.",
    };
    mockApi.mockResolvedValue(library);
    wrap(<MusicPage />);
    expect(await screen.findByText(/royalty-free music/)).toBeTruthy();
    expect(screen.getByText(/Reels render silent/)).toBeTruthy();
  });
});
