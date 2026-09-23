/**
 * Browser-only helpers shared by `client.ts` and `renewSession.ts`, moved out
 * of `client.ts` so the renewal code does not have to import it (tests mock
 * `@/lib/api/client` with a factory that returns only `apiRequest`). See
 * `client.ts`'s module comment for why the base URL is built from
 * `window.location.hostname`.
 *
 * Reads `document.cookie` and `window.location`, so this cannot run in
 * Server Components.
 */
const API_PORT = process.env.NEXT_PUBLIC_API_PORT;

export function apiBaseUrl(): string {
  return `${window.location.protocol}//${window.location.hostname}:${API_PORT}`;
}

/** Read a single cookie value from `document.cookie`, or `null` if absent. */
export function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const cookie = part.trim();
    if (cookie.startsWith(prefix)) {
      return decodeURIComponent(cookie.slice(prefix.length));
    }
  }
  return null;
}
