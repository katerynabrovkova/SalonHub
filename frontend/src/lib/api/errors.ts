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
