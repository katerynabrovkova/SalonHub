/**
 * Client-side helper for `POST appointments/<id>/pay/` — the account pay
 * endpoint (docs/DECISIONS.md § Stage 15 planning, item 14 design details).
 * Same shape as `createAccountBooking.ts`: goes through `apiRequest`
 * (`lib/api/client.ts`) so cookie credentials, the CSRF header and the S2
 * silent-renewal retry all apply; errors are not caught here, so the
 * dashboard can branch on `ApiError.status`.
 *
 * The response mirrors the guest pay endpoint's (`GuestAppointmentPayView`):
 * `provider_data` is the provider's `PaymentIntent.provider_data`
 * (`str | None` on the backend) passed through untouched — `null` for
 * MockPaymentProvider, an invoice URL for WayForPayProvider. It is
 * provider-controlled data, so callers must validate it before rendering it
 * as a link.
 */
import { apiRequest } from "@/lib/api/client";

export interface PayForAppointmentResponse {
  payment: { id: number; status: string; amount: string; currency: string };
  provider_data: string | null;
}

export async function payForAppointment(
  slug: string,
  appointmentId: number,
): Promise<PayForAppointmentResponse> {
  return apiRequest<PayForAppointmentResponse>(slug, `/appointments/${appointmentId}/pay/`, {
    method: "POST",
  });
}
