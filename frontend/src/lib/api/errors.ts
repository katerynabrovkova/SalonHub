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
