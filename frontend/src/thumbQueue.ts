/** Limit parallel thumbnail fetches so the contact sheet paints progressively. */
const MAX_CONCURRENT = 8;

let active = 0;
const waiters: Array<() => void> = [];

export function acquireThumbSlot(): Promise<() => void> {
  return new Promise((resolve) => {
    const grant = () => {
      active += 1;
      let released = false;
      resolve(() => {
        if (released) return;
        released = true;
        active = Math.max(0, active - 1);
        const next = waiters.shift();
        if (next) next();
      });
    };
    if (active < MAX_CONCURRENT) {
      grant();
    } else {
      waiters.push(grant);
    }
  });
}
