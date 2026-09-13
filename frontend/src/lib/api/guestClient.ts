/**
 * Browser-only API client for guest-token-authenticated requests
 * (docs/DECISIONS.md § Stage 14). Mirrors apiRequest's shape (./client.ts)
 * but authenticates via an explicit guest token instead of cookies/CSRF:
 * no `credentials: "include"`, no `document.cookie` read, no
 * `X-CSRFToken` — the `X-Guest-Token` header is the only credential.
 */
import { ApiError } from "./errors";

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

export async function guestApiRequest<T>(
  slug: string,
  path: string,
  token: string,
  options: RequestInit = {},
): Promise<T> {
  const apiBase = process.env.NEXT_PUBLIC_API_URL;
  const method = (options.method ?? "GET").toUpperCase();

  const headers = new Headers(options.headers);
  if (options.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("X-Guest-Token", token);

  const response = await fetch(`${apiBase}/api/v1/salons/${slug}${path}`, {
    ...options,
    method,
    headers,
  });

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
