/**
 * Client-side fetch helper for `GET appointments/mine/`, the authenticated
 * Account's own appointments (docs/DECISIONS.md § Stage 15 planning, item
 * 4). Deliberately NOT shaped like this codebase's getXPage.ts server-fetch
 * convention (getSpecialistsPage.ts, getServicesPage.ts, getReviews.ts,
 * getSalonInfoPage.ts): those are explicitly "no document.cookie, no
 * import from api/client.ts -- safe to call from a Server Component."
 * `appointments/mine/` is cookie-authenticated Account data, so it must go
 * through `apiRequest()` (api/client.ts), which is browser-only by its own
 * docstring -- this helper is meant to be called from a "use client"
 * component (app/client/page.tsx), the same posture as AuthContext's own
 * apiRequest() calls, not from a Server Component.
 *
 * Field names/types mirror AppointmentAccountSerializer
 * (backend/booking/serializers.py) exactly, including its two
 * inconsistent-looking but verified representations: `service_price_at_booking`/
 * `deposit_percentage_at_booking` are real DecimalFields (string), while
 * `payment_amount`/`amount_due_at_visit` are SerializerMethodFields
 * explicitly coerced to the same string representation with `str(...)` on
 * the backend -- both are `string | null` here, never `number`.
 *
 * The backend paginates this endpoint (DRF's DefaultPagination, page_size
 * 20, backend/core/pagination.py), but this first wave of the client
 * dashboard only fetches the first page and does not surface
 * `next`/`previous` -- no "load more"/pagination UI is in scope yet.
 */
import { apiRequest } from "@/lib/api/client";

export type AppointmentStatusValue =
  | "pending_payment"
  | "confirmed"
  | "cancelled"
  | "expired"
  | "completed"
  | "no_show";

export type PaymentStatusValue =
  | "pending"
  | "processing"
  | "succeeded"
  | "failed"
  | "expired"
  | "cancelled"
  | "refund_pending"
  | "refunded";

export interface AppointmentSpecialist {
  id: number;
  name: string;
}

export interface AppointmentService {
  id: number;
  name: string;
}

export interface MyAppointment {
  id: number;
  status: AppointmentStatusValue;
  start_datetime: string;
  end_datetime: string;
  specialist: AppointmentSpecialist;
  service: AppointmentService;
  cancelled_at: string | null;
  cancelled_by: string | null;
  cancellation_reason: string;
  service_price_at_booking: string;
  deposit_percentage_at_booking: string;
  payment_status: PaymentStatusValue | null;
  payment_amount: string | null;
  amount_due_at_visit: string | null;
}

interface DrfPage<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export async function getMyAppointments(slug: string): Promise<MyAppointment[]> {
  const page = await apiRequest<DrfPage<MyAppointment>>(slug, "/appointments/mine/");
  return page.results;
}
