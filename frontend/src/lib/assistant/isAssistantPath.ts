/**
 * Whether the assistant widget shows on a page (docs/DECISIONS.md § "Stage 17
 * block A" and § "Stage 17 sub-step order", 17.3b).
 *
 * An allowlist of exact paths with no trailing slash, so a new page has no
 * widget until it is added here (safe default). Booking steps are query
 * params on `/booking`, so the exact match covers all of them while
 * `/booking/pay` stays excluded. Never a prefix match.
 */

const ALLOWED_PATHS = new Set(["/", "/services", "/specialists", "/reviews", "/booking"]);

// Specialist detail: exactly one segment after /specialists/.
const SPECIALIST_DETAIL = /^\/specialists\/[^/]+$/;

export function isAssistantPath(pathname: string): boolean {
  return ALLOWED_PATHS.has(pathname) || SPECIALIST_DETAIL.test(pathname);
}
