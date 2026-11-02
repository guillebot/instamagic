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

  it("music search shows licence badges and imports through the backend", async () => {
    const library: MusicLibrary = {
      tracks: [
        {
          id: "t1",
          title: "Night Glass",
          artist: "Ada Lune",
          duration_s: 95,
          size_bytes: 2_000_000,
          enabled: true,
          preview_url: "/api/music/t1/audio",
          source: "openverse",
          source_url: "https://www.jamendo.com/track/1",
          license: "by",
          license_label: "CC BY 4.0",
          license_url: "https://creativecommons.org/licenses/by/4.0/",
          attribution: '"Night Glass" by Ada Lune is licensed under CC BY 4.0.',
        },
      ],
      enabled_count: 1,
      pick_mode: "rotate",
      license_note: "Only royalty-free music.",
    };
    mockApi.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/music/sources/search")) {
        return {
          source: "openverse",
          policy: "CC0 and CC BY only",
          results: [
            {
              source: "openverse",
              id: "abc",
              title: "Still Water",
              artist: "Kai",
              source_url: "https://www.jamendo.com/track/2",
              license: "cc0",
              license_label: "CC0 1.0",
              license_url: "https://creativecommons.org/publicdomain/zero/1.0/",
              duration_s: 120,
              genres: ["ambient"],
              tags: [],
              instrumental: true,
              vocals: false,
              preview_url: "/api/music/sources/openverse/abc/preview",
              imported: false,
            },
          ],
        };
      }
      if (path === "/api/music/autorefill") return { enabled: false, target: 15, queries: ["ambient"] };
      return library;
    });
    wrap(<MusicPage />);
    expect(await screen.findByText("CC BY 4.0")).toBeTruthy();
    expect(screen.getByText(/is licensed under CC BY 4.0/)).toBeTruthy();
    screen.getByRole("button", { name: "Search" }).click();
    expect(await screen.findByText("Still Water")).toBeTruthy();
    expect(screen.getByText("CC0 1.0")).toBeTruthy();
    expect(screen.getByLabelText("Preview Still Water").getAttribute("src")).toBe(
      "/api/music/sources/openverse/abc/preview",
    );
    expect(await screen.findByText("Auto-refill")).toBeTruthy();
  });
});
