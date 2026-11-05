/**
 * The unsent text in the assistant chat input, in `sessionStorage`
 * (docs/DECISIONS.md § "Stage 17 sub-step order", 17.7b). Browser-only.
 *
 * Kept next to the session id under its own key, for the life of the tab,
 * not of the server conversation: an expired conversation leaves it, and
 * clearing the session id or the expired flag never touches it. Written on
 * every change, because a real page reload runs no React unmount. Blank or
 * whitespace-only text is not stored. Never sent to the server.
 *
 * sessionStorage can throw (disabled storage, quota, privacy modes). A
 * failure means "no draft", never a broken widget.
 */

const DRAFT_KEY = "salonhub:assistant-draft";

export function readDraft(): string {
  try {
    return sessionStorage.getItem(DRAFT_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Stores the text as typed; blank text removes the stored draft instead. */
export function storeDraft(text: string): void {
  try {
    if (text.trim() === "") {
      sessionStorage.removeItem(DRAFT_KEY);
    } else {
      sessionStorage.setItem(DRAFT_KEY, text);
    }
  } catch {
    // Ignored -- see the module comment.
  }
}

export function clearDraft(): void {
  try {
    sessionStorage.removeItem(DRAFT_KEY);
  } catch {
    // Ignored -- see the module comment.
  }
}
