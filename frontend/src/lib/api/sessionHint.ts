/**
 * Session hint cookie helpers (docs/DECISIONS.md § "Session renewal and
 * session lifetime", "S2 design details", points 1 and 11). Browser-only.
 * Must not import from ./client.ts: tests mock that module with a factory
 * that returns only `apiRequest`.
 *
 * The backend sets a non-httpOnly `session_hint` cookie next to the auth
 * cookies. It holds no secret, is never trusted by the backend, and only
 * tells the frontend whether a silent renewal is worth trying.
 */
import { readCookie } from "./browserContext";

const SESSION_HINT_COOKIE = "session_hint";

/** Dispatched on `window`, with no payload, when a session is definitively lost. */
export const SESSION_EXPIRED_EVENT = "salonhub:session-expired";

/** True when the backend's `session_hint` cookie is present. */
export function hasSessionHint(): boolean {
  return readCookie(SESSION_HINT_COOKIE) !== null;
}

/**
 * Deletes the hint cookie, no event dispatch (docs/DECISIONS.md § "Session
 * renewal and session lifetime", "S2 design details", point 10). Used
 * directly by `AuthContext.logout()`, which must clear the cookie without
 * declaring the session lost.
 */
export function deleteSessionHint(): void {
  document.cookie = `${SESSION_HINT_COOKIE}=; max-age=0; path=/`;
}

/** Deletes the hint cookie and dispatches `SESSION_EXPIRED_EVENT` once. */
export function markSessionLost(): void {
  deleteSessionHint();
  window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
}
