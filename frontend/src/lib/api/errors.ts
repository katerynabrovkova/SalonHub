/**
 * Single error type for every failed API response, mirroring the backend's
 * `{ error: { code, message, details } }` envelope (docs/DECISIONS.md
 * § Stage 12, backend `core/exceptions.py`).
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
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

/**
 * Builds the `ApiError` for a failed response's parsed body. Shared by
 * `apiRequest` (browser) and server-side fetch helpers such as
 * `getAvailability`, so the envelope is parsed in one place only.
 */
export function apiErrorFromBody(status: number, body: unknown): ApiError {
  if (isErrorEnvelope(body)) {
    const { code, message, details } = body.error;
    return new ApiError(
      status,
      typeof code === "string" ? code : "unknown_error",
      typeof message === "string" ? message : "Не вдалося виконати запит.",
      typeof details === "object" && details !== null
        ? (details as Record<string, unknown>)
        : {},
    );
  }
  return new ApiError(status, "unknown_error", "Не вдалося виконати запит.");
}

/**
 * Thrown by `apiRequest` (docs/DECISIONS.md § "Session renewal and session
 * lifetime", "S2 design details", point 2) when a 401 triggered a renewal
 * attempt that resolved "unsure" (network error, timeout, 403, 5xx, ...).
 * Deliberately not an `ApiError` with status 401: callers that treat any
 * such error as "signed out" (today, `AuthContext`) must not do that here,
 * since the session's actual state is unknown, not lost.
 */
export class RenewalUnsureError extends Error {
  constructor() {
    super("Session renewal outcome was unsure.");
    this.name = "RenewalUnsureError";
  }
}
