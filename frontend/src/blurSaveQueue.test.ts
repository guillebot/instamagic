import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createSaveQueue } from "./blurSaveQueue";

type Patch = { x?: number; y?: number; rotation_deg?: number };

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("createSaveQueue", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("debounces rapid edits and sends only the latest merged geometry", async () => {
    const sent: Array<[string, Patch]> = [];
    const onIdle = vi.fn();
    const queue = createSaveQueue<Patch>({
      send: async (id, patch) => {
        sent.push([id, patch]);
      },
      delayMs: 200,
      onIdle,
    });
    queue.schedule("a", { x: 1 });
    queue.schedule("a", { x: 2 });
    queue.schedule("a", { y: 7 });
    await vi.advanceTimersByTimeAsync(250);
    expect(sent).toEqual([["a", { x: 2, y: 7 }]]);
    expect(onIdle).toHaveBeenCalledTimes(1);
  });

  it("never runs two requests at once; a newer edit is sent after the older one finishes", async () => {
    const first = deferred();
    let inFlight = 0;
    let maxInFlight = 0;
    const sent: Patch[] = [];
    const onIdle = vi.fn();
    const busy: boolean[] = [];
    const queue = createSaveQueue<Patch>({
      send: async (_id, patch) => {
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        sent.push(patch);
        if (sent.length === 1) await first.promise;
        inFlight -= 1;
      },
      delayMs: 0,
      onIdle,
      onBusyChange: (b) => busy.push(b),
    });
    queue.schedule("a", { x: 10 });
    await vi.advanceTimersByTimeAsync(1);
    queue.schedule("a", { x: 20 });
    queue.schedule("a", { x: 30 });
    await vi.advanceTimersByTimeAsync(1);
    expect(sent).toEqual([{ x: 10 }]);
    expect(onIdle).not.toHaveBeenCalled();
    first.resolve();
    await queue.flush();
    expect(sent).toEqual([{ x: 10 }, { x: 30 }]);
    expect(maxInFlight).toBe(1);
    expect(onIdle).toHaveBeenCalled();
    expect(busy[0]).toBe(true);
    expect(busy[busy.length - 1]).toBe(false);
    expect(queue.isBusy()).toBe(false);
  });

  it("flush sends pending edits immediately (e.g. before approve)", async () => {
    const sent: Patch[] = [];
    const queue = createSaveQueue<Patch>({
      send: async (_id, patch) => {
        sent.push(patch);
      },
      delayMs: 10_000,
    });
    queue.schedule("a", { rotation_deg: 15 });
    await queue.flush();
    expect(sent).toEqual([{ rotation_deg: 15 }]);
  });

  it("reports errors and still settles", async () => {
    const onError = vi.fn();
    const queue = createSaveQueue<Patch>({
      send: async () => {
        throw new Error("boom");
      },
      delayMs: 0,
      onError,
    });
    queue.schedule("a", { x: 1 });
    await queue.flush();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(queue.isBusy()).toBe(false);
  });
});
