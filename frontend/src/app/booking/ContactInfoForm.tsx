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
 * On success, stores the token in sessionStorage under the pay page's key
 * and redirects to `/booking/pay#appointment_id=<id>`, so the token never
 * enters the URL; if storing fails, the fragment also carries the token, the
 * same format as the backend's BOOKING_CREATED email link
 * (`backend/notifications/services.py`). `PaymentStatus.tsx` reads either on
 * mount (docs/DECISIONS.md "Frontend > Booking flow", "Booking > Guest
 * token").
 */
import { useNavigatingRouter } from "@/lib/navigation/useNavigatingRouter";
import { useState, type FormEvent } from "react";

import { readSessionId } from "@/lib/assistant/sessionId";
import { createGuestBooking } from "@/lib/booking/createGuestBooking";
import { isSlotGoneError } from "@/lib/booking/isSlotGoneError";
import { isSpecialistNotOfferingError } from "@/lib/booking/step3Errors";
import { guestPayTokenKey } from "@/lib/security/fragmentTokenScript";

import { useBookingContactInfo } from "./BookingContactInfoContext";
import SpecialistNotOfferingNotice from "./SpecialistNotOfferingNotice";

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

export function buildStep3Url(
  entry: "service" | "specialist",
  service: string,
  specialist: string,
  startDatetime: string,
): string {
  return `/booking?entry=${entry}&service=${service}&specialist=${specialist}&step=3&date_from=${dateFromOf(startDatetime)}`;
}

/** Step-3 URL for the slot-gone branch: `buildStep3Url` plus the notice the
 * step-3 page turns into "this time was just taken" (docs/DECISIONS.md,
 * "Slot-taken notice on step 3", decided 21.09.2026). */
export function buildSlotTakenUrl(
  entry: "service" | "specialist",
  service: string,
  specialist: string,
  startDatetime: string,
): string {
  return `${buildStep3Url(entry, service, specialist, startDatetime)}&notice=slot_taken`;
}

/** Stores the token under the pay page's key and returns the URL with only
 * the id; if storing fails, the token goes in the fragment as before, so the
 * guest can still pay (docs/DECISIONS.md "Frontend > Booking flow"). */
function payPageUrl(appointmentId: number, token: string): string {
  try {
    window.sessionStorage.setItem(guestPayTokenKey(appointmentId), token);
    return `/booking/pay#appointment_id=${appointmentId}`;
  } catch {
    return `/booking/pay#appointment_id=${appointmentId}&token=${token}`;
  }
}

export default function ContactInfoForm({
  slug,
  entry,
  service,
  specialist,
  startDatetime,
}: ContactInfoFormProps) {
  const router = useNavigatingRouter();
  const {
    customerName,
    setCustomerName,
    customerEmail,
    setCustomerEmail,
    customerPhone,
    setCustomerPhone,
  } = useBookingContactInfo();
  const [error, setError] = useState<string | null>(null);
  const [specialistNotOffering, setSpecialistNotOffering] = useState(false);
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSpecialistNotOffering(false);
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
        // 17.6b: read at submit time; the id itself is never sent.
        ...(readSessionId() !== null ? { source: "assistant" as const } : {}),
      });
      router.push(payPageUrl(result.appointment.id, result.guestToken));
      return;
    } catch (err) {
      if (isSlotGoneError(err)) {
        router.push(buildSlotTakenUrl(entry, service, specialist, startDatetime));
        return;
      }
      if (isSpecialistNotOfferingError(err)) {
        setSpecialistNotOffering(true);
        return;
      }
      setError("Щось пішло не так. Спробуйте ще раз.");
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
      {specialistNotOffering ? <SpecialistNotOfferingNotice service={service} /> : null}

      <button type="submit" disabled={pending}>
        Підтвердити запис
      </button>
    </form>
  );
}
