/**
 * Browser-only API client for the SalonHub backend (docs/DECISIONS.md
 * § Stage 12 "API client wrapper").
 *
 * - Explicit `slug` per call; no module-level salon/session state.
 * - Every request is credentialed (`credentials: 'include'`) so the httpOnly
 *   auth cookies ride along.
 * - Unsafe methods echo the `csrftoken` cookie in `X-CSRFToken`.
 * - Failed responses become `ApiError` from the backend error envelope.
 *   A `fetch` that throws before a response (network failure) is not wrapped.
 *
 * Reads `document.cookie`, so this cannot run in Server Components.
 */
import { ApiError } from "./errors";

const API_BASE = process.env.NEXT_PUBLIC_API_URL;

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/** Read a single cookie value from `document.cookie`, or `null` if absent. */
function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const cookie = part.trim();
    if (cookie.startsWith(prefix)) {
      return decodeURIComponent(cookie.slice(prefix.length));
    }
  }
  return null;
}

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
    `${API_BASE}/api/v1/salons/${slug}${path}`,
    { ...options, method, headers, credentials: "include" },
  );

  if (response.status === 204) {
    return undefined as T;
  }

  const body: unknown = await response.json();

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
