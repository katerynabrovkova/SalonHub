/**
 * Return-after-login path (docs/DECISIONS.md § "Session renewal and session
 * lifetime", "S3 design details", items 2, 3 and 6). Browser-only. Must not
 * import from ../api/client.ts: tests mock that module with a factory that
 * returns only `apiRequest`.
 */

const RETURN_PATH_KEY = "salonhub:return-path";

// sessionStorage can throw (disabled storage, quota, privacy modes). A failure
// means "no return path", never a broken login.
export function saveReturnPath(path: string): void {
  try {
    sessionStorage.setItem(RETURN_PATH_KEY, path);
  } catch {
    // Ignored -- see comment above.
  }
}

export function consumeReturnPath(): string | null {
  try {
    const path = sessionStorage.getItem(RETURN_PATH_KEY);
    sessionStorage.removeItem(RETURN_PATH_KEY);
    return path;
  } catch {
    return null;
  }
}

// Checks the parsed URL, not string prefixes: "/client/../admin" starts with
// "/client/" but resolves to "/admin".
export function isSafeReturnPath(path: string, role: string): boolean {
  if (role !== "client") {
    return false;
  }
  let url: URL;
  try {
    url = new URL(path, window.location.origin);
  } catch {
    return false;
  }
  if (url.origin !== window.location.origin) {
    return false;
  }
  return url.pathname === "/client" || url.pathname.startsWith("/client/");
}
