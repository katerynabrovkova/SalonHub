"use client";

/**
 * Booking step 4 — contact-info form (docs/DECISIONS.md § Stage 14
 * implementation decisions: step 4 contact-info form). Mirrors
 * login/page.tsx's form conventions (single `error: string | null`, a
 * `pending: boolean`, `handleSubmit` with preventDefault/try-catch/finally,
 * HTML5 `required`/`type="email"` as the only client-side validation,
 * submit disabled while pending) — except the three contact fields
 * themselves come from `useBookingContactInfo()`, not local `useState`, so
 * they survive the "return to step 3 on slot conflict, then forward to
 * step 4 again" round trip (BookingContactInfoContext.tsx).
 *
 * On success, redirects to `/booking/pay` with `appointment_id`/`token` in
 * the URL fragment — the same format the backend's BOOKING_CREATED email
 * link uses (`build_salon_frontend_url(...) + "#appointment_id=...&token=..."`,
 * `backend/notifications/services.py`), which `PaymentStatus.tsx` reads on
 * mount (docs/DECISIONS.md § Stage 14 step 5).
 */
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/errors";
import { createGuestBooking } from "@/lib/booking/createGuestBooking";

import { useBookingContactInfo } from "./BookingContactInfoContext";

interface ContactInfoFormProps {
  slug: string;
  entry: "service" | "specialist";
  service: string;
  specialist: string;
  /** Already decoded (booking/page.tsx decodes the `slot` URL param before
   * passing it down here). */
  startDatetime: string;
}

/** `startDatetime`'s own first 10 characters are its salon-local calendar
 * date — same string-slicing convention as groupAvailabilityByDay.ts, used
 * here to send the guest back to the step-3 window containing the slot
 * they just tried, on a conflict. */
function dateFromOf(startDatetime: string): string {
  return startDatetime.slice(0, 10);
}

function buildStep3Url(
  entry: "service" | "specialist",
  service: string,
  specialist: string,
  startDatetime: string,
): string {
  return `/booking?entry=${entry}&service=${service}&specialist=${specialist}&step=3&date_from=${dateFromOf(startDatetime)}`;
}

export default function ContactInfoForm({
  slug,
  entry,
  service,
  specialist,
  startDatetime,
}: ContactInfoFormProps) {
  const router = useRouter();
  const {
    customerName,
    setCustomerName,
    customerEmail,
    setCustomerEmail,
    customerPhone,
    setCustomerPhone,
  } = useBookingContactInfo();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);

    try {
      const result = await createGuestBooking({
        slug,
        service,
        specialist,
        startDatetime,
        customerName,
        customerEmail,
        customerPhone,
      });
      router.push(`/booking/pay#appointment_id=${result.appointment.id}&token=${result.guestToken}`);
      return;
    } catch (err) {
      if (err instanceof ApiError && err.code === "SLOT_NO_LONGER_AVAILABLE") {
        router.push(buildStep3Url(entry, service, specialist, startDatetime));
        return;
      }
      setError("Something went wrong. Please try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex w-full max-w-sm flex-col gap-4">
      <div className="flex flex-col gap-1">
        <label htmlFor="customerName">Ім&apos;я</label>
        <input
          id="customerName"
          type="text"
          value={customerName}
          onChange={(event) => setCustomerName(event.target.value)}
          required
        />
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="customerEmail">Email</label>
        <input
          id="customerEmail"
          type="email"
          value={customerEmail}
          onChange={(event) => setCustomerEmail(event.target.value)}
          required
        />
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="customerPhone">Телефон</label>
        <input
          id="customerPhone"
          type="tel"
          value={customerPhone}
          onChange={(event) => setCustomerPhone(event.target.value)}
          required
        />
      </div>

      {error !== null ? <p role="alert">{error}</p> : null}

      <button type="submit" disabled={pending}>
        Забронювати
      </button>
    </form>
  );
}
