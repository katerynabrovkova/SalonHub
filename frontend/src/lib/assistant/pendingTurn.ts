/**
 * The customer's sent text of a turn that has not finished with `done`, in
 * `sessionStorage` (docs/DECISIONS.md § "Stage 17 sub-step order", 17.7e).
 * Browser-only.
 *
 * Kept next to the session id under its own key from send until `done`, so a
 * full reload, mid-reply or after a failure, can show the message again with
 * "Повторити". Separate from the 17.7b draft: clearing one never touches the
 * other.
 *
 * sessionStorage can throw (disabled storage, quota, privacy modes). A
 * failure means "no pending text", never a broken widget.
 */

const PENDING_KEY = "salonhub:assistant-pending";

export function readPendingText(): string | null {
  try {
    return sessionStorage.getItem(PENDING_KEY);
  } catch {
    return null;
  }
}

export function storePendingText(text: string): void {
  try {
    sessionStorage.setItem(PENDING_KEY, text);
  } catch {
    // Ignored -- see the module comment.
  }
}

export function clearPendingText(): void {
  try {
    sessionStorage.removeItem(PENDING_KEY);
  } catch {
    // Ignored -- see the module comment.
  }
}
