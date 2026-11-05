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

/*
 * 17.7a: a stored conversation was found expired, and the customer has not
 * seen the notice yet. Kept under its own key until the chat is opened on an
 * allowed page, so the notice survives pages where the widget is hidden and
 * several full page loads; the session id itself is cleared at once.
 * Clearing one never clears the other.
 */

const EXPIRED_KEY = "salonhub:assistant-expired";

export function markConversationExpired(): void {
  try {
    sessionStorage.setItem(EXPIRED_KEY, "1");
  } catch {
    // Ignored -- see the module comment.
  }
}

export function readConversationExpired(): boolean {
  try {
    return sessionStorage.getItem(EXPIRED_KEY) !== null;
  } catch {
    return false;
  }
}

export function clearConversationExpired(): void {
  try {
    sessionStorage.removeItem(EXPIRED_KEY);
  } catch {
    // Ignored -- see the module comment.
  }
}
