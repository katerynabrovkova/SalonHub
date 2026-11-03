/**
 * The assistant chat's session id in `sessionStorage` (docs/DECISIONS.md §
 * "Stage 17 sub-step order", 17.4a; Stage 16 "Session"). Browser-only.
 *
 * One shared key: the widget stores the id only after a successful turn
 * (block B), and the 17.6 booking forms read it to send
 * `source=assistant` (block C). `sessionStorage` is per tab and per origin,
 * so each salon subdomain has its own. The id is not an auth credential, so
 * keeping it here does not conflict with the Stage 12 cookie rule.
 *
 * sessionStorage can throw (disabled storage, quota, privacy modes). A
 * failure means "no stored conversation", never a broken widget.
 */

const SESSION_ID_KEY = "salonhub:assistant-session";

export function readSessionId(): string | null {
  try {
    return sessionStorage.getItem(SESSION_ID_KEY);
  } catch {
    return null;
  }
}

export function storeSessionId(id: string): void {
  try {
    sessionStorage.setItem(SESSION_ID_KEY, id);
  } catch {
    // Ignored -- see the module comment.
  }
}

export function clearSessionId(): void {
  try {
    sessionStorage.removeItem(SESSION_ID_KEY);
  } catch {
    // Ignored -- see the module comment.
  }
}
