/**
 * Client-side fetch helper for `POST /bookings/` — the guest booking-
 * creation endpoint (docs/DECISIONS.md § Stage 14 implementation decisions:
 * step 4 contact-info form, "a new, minimal client-side fetch helper").
 *
 * Neither existing client fetcher fits: `api/guestClient.ts`'s
 * `guestApiRequest` requires an already-issued guest token as a parameter,
 * but this call is the one that *issues* the token — there is no token yet
 * to send. `api/client.ts`'s `apiRequest` sends `credentials: "include"`
 * and echoes a CSRF cookie, built for the cookie/session-authenticated
 * admin/client flows; this endpoint is `AllowAny` with no session to speak
 * of. This helper mirrors both files' URL-building convention
 * (`NEXT_PUBLIC_API_URL` + `/api/v1/salons/<slug>` + path) and their
 * error-envelope-to-`ApiError` translation, with neither's extra
 * credential/token header.
 *
 * On a non-2xx response this throws `ApiError`, same as every other client
 * helper in this codebase — its `status`/`code` fields already surface the
 * two outcomes the caller (the step-4 form) needs to tell apart distinctly,
 * not collapsed into one generic message: a 409 with
 * `code === "SLOT_NO_LONGER_AVAILABLE"` (send the guest back to step 3)
 * versus any other failure.
 */
import { ApiError } from "@/lib/api/errors";

export interface CreateGuestBookingResult {
  appointment: {
    id: number;
    status: string;
    startDatetime: string;
    endDatetime: string;
  };
  guestToken: string;
}

export interface CreateGuestBookingParams {
  slug: string;
  service: string;
  /** A real specialist id, or the literal "any" (docs/DECISIONS.md §
   * Stage 14 implementation decisions, "'Any specialist' is encoded as
   * the literal URL value specialist=any"). */
  specialist: string;
  startDatetime: string;
  customerName: string;
  customerEmail: string;
  customerPhone: string;
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

export async function createGuestBooking({
  slug,
  service,
  specialist,
  startDatetime,
  customerName,
  customerEmail,
  customerPhone,
}: CreateGuestBookingParams): Promise<CreateGuestBookingResult> {
  const apiBase = process.env.NEXT_PUBLIC_API_URL;

  const response = await fetch(`${apiBase}/api/v1/salons/${slug}/bookings/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      service,
      specialist,
      start_datetime: startDatetime,
      customer_name: customerName,
      customer_email: customerEmail,
      customer_phone: customerPhone,
    }),
  });

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

  const parsed = body as {
    appointment: { id: number; status: string; start_datetime: string; end_datetime: string };
    guest_token: string;
  };

  return {
    appointment: {
      id: parsed.appointment.id,
      status: parsed.appointment.status,
      startDatetime: parsed.appointment.start_datetime,
      endDatetime: parsed.appointment.end_datetime,
    },
    guestToken: parsed.guest_token,
  };
}
