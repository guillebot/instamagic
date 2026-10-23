/** Debounced, latest-wins queue for blur detector probe requests. */

export type ProbeQueueOptions = {
  run: () => Promise<void>;
  delayMs?: number;
  onBusyChange?: (busy: boolean) => void;
};

export type ProbeQueue = {
  schedule: (delayMs?: number) => void;
  runNow: () => Promise<void>;
  dispose: () => void;
};

export function createProbeQueue(options: ProbeQueueOptions): ProbeQueue {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let running: Promise<void> | null = null;
  let pending = false;
  let disposed = false;
  let busy = false;

  function setBusy(next: boolean) {
    if (busy === next) return;
    busy = next;
    options.onBusyChange?.(next);
  }

  async function execute(): Promise<void> {
    pending = false;
    setBusy(true);
    try {
      await options.run();
    } finally {
      setBusy(false);
      if (pending && !disposed) {
        void execute();
      }
    }
  }

  function kick(): void {
    if (disposed) return;
    pending = true;
    if (running) return;
    running = execute().finally(() => {
      running = null;
      if (pending && !disposed) void kick();
    });
  }

  return {
    schedule(delayMs?: number) {
      if (disposed) return;
      if (timer) clearTimeout(timer);
      const wait = delayMs ?? options.delayMs ?? 400;
      timer = setTimeout(() => {
        timer = null;
        kick();
      }, wait);
    },
    async runNow() {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      kick();
      if (running) await running;
    },
    dispose() {
      disposed = true;
      if (timer) clearTimeout(timer);
    },
  };
}
