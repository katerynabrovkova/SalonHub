/**
 * Site-wide pending-navigation store (docs/DECISIONS.md § Stage 17 sub-step
 * order, 17.6c). `start()` marks a navigation as pending; the visible state
 * follows only after SHOW_DELAY_MS, so a navigation that finishes sooner
 * shows nothing. `end()` clears both. A navigation that never reports its
 * end (an aborted or failed route change) is ended by the safety timeout.
 *
 * Plain module state with subscribe/getSnapshot-style accessors, so React
 * reads it through useSyncExternalStore.
 */

const SHOW_DELAY_MS = 150;
const SAFETY_TIMEOUT_MS = 10_000;

let pending = false;
let visible = false;
let showTimer: ReturnType<typeof setTimeout> | null = null;
let safetyTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) {
    listener();
  }
}

function clearTimers(): void {
  if (showTimer !== null) {
    clearTimeout(showTimer);
    showTimer = null;
  }
  if (safetyTimer !== null) {
    clearTimeout(safetyTimer);
    safetyTimer = null;
  }
}

/** Marks a navigation as pending. Idempotent: a second call keeps the timers. */
export function start(): void {
  if (pending) {
    return;
  }
  pending = true;
  showTimer = setTimeout(() => {
    showTimer = null;
    visible = true;
    notify();
  }, SHOW_DELAY_MS);
  safetyTimer = setTimeout(end, SAFETY_TIMEOUT_MS);
  notify();
}

/** Ends the pending navigation, if any. */
export function end(): void {
  clearTimers();
  if (!pending && !visible) {
    return;
  }
  pending = false;
  visible = false;
  notify();
}

export function isPending(): boolean {
  return pending;
}

export function isVisible(): boolean {
  return visible;
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
