/**
 * Client-side fetch helper for `POST appointments/` — the account-aware
 * booking endpoint (docs/DECISIONS.md § Stage 15 planning, item 5, "Cycle D
 * — frontend account-aware booking (step 4)": "Submit uses a new helper
 * `createAccountBooking` built on `apiRequest` (cookie credentials + CSRF),
 * not the credential-less `createGuestBooking`. Payload mirrors the guest
 * one minus `customer_email`; `name`/`phone` are sent only in the unlinked
 * case.").
 *
 * Unlike `createGuestBooking.ts`, this goes through `apiRequest`
 * (`lib/api/client.ts`) so cookie credentials and the CSRF header are
 * attached automatically — never reimplemented here. Errors are not caught:
 * `apiRequest` already throws `ApiError` on a non-2xx response, and it
 * propagates unchanged so the step-4 form can branch on `status`/`code`
 * (409 `SLOT_NO_LONGER_AVAILABLE`, 403 `email_not_verified`).
 */
import { apiRequest } from "@/lib/api/client";

export interface CreateAccountBookingParams {
  slug: string;
  service: string;
  /** A real specialist id, or the literal "any" (same convention as
   * `createGuestBooking`'s `specialist` param). */
  specialist: string;
  startDatetime: string;
  /** Present only when the Account has no linked Customer yet; when
   * omitted, `customer_name`/`customer_phone` are left out of the request
   * body entirely (not sent as null). */
  contact?: { name: string; phone: string };
}

export async function createAccountBooking({
  slug,
  service,
  specialist,
  startDatetime,
  contact,
}: CreateAccountBookingParams): Promise<unknown> {
  const body: Record<string, unknown> = {
    service,
    specialist,
    start_datetime: startDatetime,
  };
  if (contact !== undefined) {
    body.customer_name = contact.name;
    body.customer_phone = contact.phone;
  }

  return apiRequest(slug, "/appointments/", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
