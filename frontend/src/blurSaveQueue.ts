/**
 * Latest-wins, serialized save queue for blur geometry edits.
 *
 * - Edits for the same cover are merged; only the newest geometry is sent.
 * - At most one request is in flight, so an older PATCH can never land after a newer one.
 * - `onIdle` fires once every queued edit has been persisted, which is the only moment the
 *   caller should re-read server state (the render for the final geometry).
 */
export type SaveQueueOptions<P> = {
  send: (id: string, patch: P) => Promise<void>;
  delayMs?: number;
  onBusyChange?: (busy: boolean) => void;
  onIdle?: () => void | Promise<void>;
  onError?: (error: unknown) => void;
};

export type SaveQueue<P> = {
  schedule: (id: string, patch: P, delayMs?: number) => void;
  flush: () => Promise<void>;
  isBusy: () => boolean;
  dispose: () => void;
};

export function createSaveQueue<P extends object>(options: SaveQueueOptions<P>): SaveQueue<P> {
  const pending = new Map<string, P>();
  let timer: ReturnType<typeof setTimeout> | null = null;
  let running: Promise<void> | null = null;
  let busy = false;
  let disposed = false;

  function setBusy(next: boolean) {
    if (busy === next) return;
    busy = next;
    options.onBusyChange?.(next);
  }

  async function drain(): Promise<void> {
    let failed = false;
    while (pending.size > 0) {
      const [id, patch] = pending.entries().next().value as [string, P];
      pending.delete(id);
      try {
        await options.send(id, patch);
      } catch (error) {
        failed = true;
        options.onError?.(error);
      }
    }
    if (!disposed && pending.size === 0) {
      try {
        await options.onIdle?.();
      } catch (error) {
        if (!failed) options.onError?.(error);
      }
    }
  }

  function start(): Promise<void> {
    if (running) return running;
    running = (async () => {
      try {
        // Keep draining while edits keep arriving during the idle refresh.
        do {
          await drain();
        } while (pending.size > 0 && timer === null);
      } finally {
        running = null;
        if (pending.size === 0 && timer === null) setBusy(false);
      }
    })();
    return running;
  }

  return {
    schedule(id, patch, delayMs) {
      const merged = { ...(pending.get(id) ?? {}), ...patch } as P;
      pending.set(id, merged);
      setBusy(true);
      if (timer !== null) clearTimeout(timer);
      const wait = delayMs ?? options.delayMs ?? 250;
      timer = setTimeout(() => {
        timer = null;
        void start();
      }, wait);
    },
    async flush() {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      while (running || pending.size > 0) {
        await (running ?? start());
      }
    },
    isBusy: () => busy,
    dispose() {
      disposed = true;
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
  };
}
