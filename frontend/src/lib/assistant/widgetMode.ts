/**
 * The assistant widget's mode in `sessionStorage` (docs/DECISIONS.md §
 * "Stage 17 sub-step order", 17.7d). Browser-only.
 *
 * Kept next to the session id under its own key, for the life of the tab,
 * so a full reload brings the chat back as it was: collapsed, the small
 * window or the side panel. The server cannot read it, so the widget reads
 * it in an effect after mount, never during render.
 *
 * sessionStorage can throw (disabled storage, quota, privacy modes). A
 * failure means "collapsed", never a broken widget.
 */

export type WidgetMode = "collapsed" | "window" | "panel";

const MODE_KEY = "salonhub:assistant-mode";

const MODES: readonly WidgetMode[] = ["collapsed", "window", "panel"];

function isWidgetMode(value: string | null): value is WidgetMode {
  return MODES.some((mode) => mode === value);
}

/** The stored mode; "collapsed" when nothing valid is stored. */
export function readWidgetMode(): WidgetMode {
  try {
    const value = sessionStorage.getItem(MODE_KEY);
    return isWidgetMode(value) ? value : "collapsed";
  } catch {
    return "collapsed";
  }
}

export function storeWidgetMode(mode: WidgetMode): void {
  try {
    sessionStorage.setItem(MODE_KEY, mode);
  } catch {
    // Ignored -- see the module comment.
  }
}

export function clearWidgetMode(): void {
  try {
    sessionStorage.removeItem(MODE_KEY);
  } catch {
    // Ignored -- see the module comment.
  }
}
