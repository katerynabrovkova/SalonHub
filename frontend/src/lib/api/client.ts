/**
 * Browser-only API client for the SalonHub backend (docs/DECISIONS.md
 * § Stage 12 "API client wrapper"; base-URL construction revised per
 * § "Dev environment: API hostname breaks the same-site assumption").
 *
 * - Explicit `slug` per call; `client.ts` itself holds no module-level
 *   salon/session state. (The one module-level state in this area, the
 *   single-flight-per-slug renewal map, lives in `./renewSession`, not
 *   here -- see that module's comment.)
 * - Every request is credentialed (`credentials: 'include'`) so the httpOnly
 *   auth cookies ride along.
 * - Unsafe methods echo the `csrftoken` cookie in `X-CSRFToken`.
 * - Failed responses become `ApiError` from the backend error envelope.
 *   A `fetch` that throws before a response (network failure) is not wrapped.
 * - A 401 outside the excluded auth paths, with the `session_hint` cookie
 *   present, triggers one silent renewal and retry (docs/DECISIONS.md
 *   § "Session renewal and session lifetime", "S2 design details").
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
import { ApiError, RenewalUnsureError } from "./errors";
import { renewSession } from "./renewSession";
import { hasSessionHint } from "./sessionHint";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

// docs/DECISIONS.md, "S2 design details", point 5: exact match against the
// salon-relative path passed to `apiRequest`, no prefix matching.
const RENEWAL_EXCLUDED_PATHS = new Set([
  "/auth/login/",
  "/auth/refresh/",
  "/auth/logout/",
  "/auth/csrf/",
]);

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

/** Builds the request headers fresh, re-reading the CSRF cookie each time. */
function buildRequestHeaders(method: string, options: RequestInit): Headers {
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
  return headers;
}

/** Sends one request and parses the response, throwing `ApiError` on failure. */
async function sendAndParse<T>(
  slug: string,
  path: string,
  method: string,
  headers: Headers,
  options: RequestInit,
): Promise<T> {
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

export async function apiRequest<T>(
  slug: string,
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();

  try {
    return await sendAndParse<T>(slug, path, method, buildRequestHeaders(method, options), options);
  } catch (error) {
    if (
      !(error instanceof ApiError) ||
      error.status !== 401 ||
      RENEWAL_EXCLUDED_PATHS.has(path) ||
      !hasSessionHint()
    ) {
      throw error;
    }

    const outcome = await renewSession(slug);
    if (outcome === "lost") {
      throw error;
    }
    if (outcome === "unsure") {
      throw new RenewalUnsureError();
    }

    // "renewed": retry once, rebuilding headers so the CSRF cookie is
    // re-read at retry time (docs/DECISIONS.md, S2 design details, point 7).
    // A 401 on the retry is final -- it throws its own ApiError here,
    // outside this catch block, so no second renewal is attempted (point 3).
    return await sendAndParse<T>(slug, path, method, buildRequestHeaders(method, options), options);
  }
}
