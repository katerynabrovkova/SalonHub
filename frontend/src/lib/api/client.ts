/**
 * Browser-only API client for the SalonHub backend (docs/DECISIONS.md
 * § Stage 12 "API client wrapper"; base-URL construction revised per
 * § "Dev environment: API hostname breaks the same-site assumption").
 *
 * - Explicit `slug` per call; no module-level salon/session state.
 * - Every request is credentialed (`credentials: 'include'`) so the httpOnly
 *   auth cookies ride along.
 * - Unsafe methods echo the `csrftoken` cookie in `X-CSRFToken`.
 * - Failed responses become `ApiError` from the backend error envelope.
 *   A `fetch` that throws before a response (network failure) is not wrapped.
 *
 * The base URL is built from `window.location.hostname` (the page's OWN
 * host) at call time, not a fixed `NEXT_PUBLIC_API_URL` origin -- a fixed
 * value (whether bare `localhost` or a separate `api.localhost`) can never
 * be the same registrable domain as every per-tenant `<slug>.localhost`
 * frontend, so `SameSite=Lax` cookies (csrftoken/access_token/refresh_token)
 * get silently dropped by the browser on this module's `fetch()` calls.
 * Using the literal current hostname, just swapping the port, guarantees an
 * identical registrable domain (genuinely same-site, not merely same-site
 * by wildcard coincidence) regardless of which salon subdomain is active.
 * Only the port is still configurable, via `NEXT_PUBLIC_API_PORT` -- there
 * is no full-origin var for this file to read anymore.
 *
 * Reads `document.cookie` and `window.location`, so this cannot run in
 * Server Components.
 */
import { apiBaseUrl, readCookie } from "./browserContext";
import { ApiError } from "./errors";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function isErrorEnvelope(
  body: unknown,
): body is { error: { code?: unknown; message?: unknown; details?: unknown } } {
  return (
    typeof body === "object" &&
    body !== null &&
    "error" in body &&
    typeof (body as { error: unknown }).error === "object" &&
    (body as { error: unknown }).error !== null
  );
}

export async function apiRequest<T>(
  slug: string,
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();

  const headers = new Headers(options.headers);
  if (options.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (!SAFE_METHODS.has(method)) {
    const csrfToken = readCookie("csrftoken");
    if (csrfToken !== null) {
      headers.set("X-CSRFToken", csrfToken);
    }
  }

  const response = await fetch(
    `${apiBaseUrl()}/api/v1/salons/${slug}${path}`,
    { ...options, method, headers, credentials: "include" },
  );

  if (response.status === 204) {
    return undefined as T;
  }

  let body: unknown;
  if (response.ok) {
    const text = await response.text();
    body = text === "" ? undefined : JSON.parse(text);
  } else {
    body = await response.json();
  }

  if (!response.ok) {
    if (isErrorEnvelope(body)) {
      const { code, message, details } = body.error;
      throw new ApiError(
        response.status,
        typeof code === "string" ? code : "unknown_error",
        typeof message === "string" ? message : "Request failed.",
        typeof details === "object" && details !== null
          ? (details as Record<string, unknown>)
          : {},
      );
    }
    throw new ApiError(response.status, "unknown_error", "Request failed.");
  }

  return body as T;
}
