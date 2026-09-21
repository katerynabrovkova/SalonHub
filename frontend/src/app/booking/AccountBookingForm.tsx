"use client";

/**
 * Booking step 4's account-aware form (docs/DECISIONS.md § Stage 15
 * planning, item 5, "Cycle D — frontend account-aware booking (step 4)").
 * Rendered by `BookingStep4.tsx` in place of the guest `ContactInfoForm`
 * once `me` is a verified `"client"`. Mirrors `ContactInfoForm.tsx`'s
 * conventions: a single `error: string | null`, a `pending: boolean` reset
 * in `finally`, `handleSubmit` with `preventDefault`, HTML5 `required` as
 * the only client-side validation, submit disabled while pending — except
 * it goes through `createAccountBooking` (cookie credentials + CSRF via
 * `apiRequest`), not the credential-less `createGuestBooking`, and reads
 * `me`/`refresh` from `useAuth()` instead of taking contact fields as
 * component state of its own.
 *
 * `me` should never be null here — `BookingStep4` only renders this
 * component for a non-null, verified, `"client"` `me` — but a null `me` is
 * handled by rendering nothing rather than crashing, in case that
 * invariant is ever violated by a future caller.
 */
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useAuth } from "@/app/AuthContext";
import { ApiError } from "@/lib/api/errors";
import { createAccountBooking } from "@/lib/booking/createAccountBooking";

import { useBookingContactInfo } from "./BookingContactInfoContext";
import { buildStep3Url } from "./ContactInfoForm";

interface AccountBookingFormProps {
  slug: string;
  entry: "service" | "specialist";
  service: string;
  specialist: string;
  startDatetime: string;
  /** Whether the Account already has a linked Customer (docs/DECISIONS.md
   * § Cycle D: derived via `hasLinkedCustomer(me)` by the caller). */
  linked: boolean;
}

export default function AccountBookingForm({
  slug,
  entry,
  service,
  specialist,
  startDatetime,
  linked,
}: AccountBookingFormProps) {
  const router = useRouter();
  const { me, refresh } = useAuth();
  const { customerName, setCustomerName, customerPhone, setCustomerPhone } = useBookingContactInfo();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  if (me === null) {
    return null;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);

    try {
      await createAccountBooking({
        slug,
        service,
        specialist,
        startDatetime,
        ...(linked ? {} : { contact: { name: customerName, phone: customerPhone } }),
      });
      await refresh();
      router.push("/client");
      return;
    } catch (err) {
      if (err instanceof ApiError && err.code === "SLOT_NO_LONGER_AVAILABLE") {
        router.push(buildStep3Url(entry, service, specialist, startDatetime));
        return;
      }
      if (err instanceof ApiError && err.code === "email_not_verified") {
        setError("Спершу підтвердіть пошту, щоб бронювати з облікового запису.");
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex w-full max-w-sm flex-col gap-4">
      {linked ? (
        <p>Запис на ім&apos;я вашого облікового запису: {me.email}</p>
      ) : (
        <>
          <div className="flex flex-col gap-1">
            <label htmlFor="accountEmail">Email</label>
            <input id="accountEmail" type="email" value={me.email} readOnly />
            <span className="text-sm text-zinc-500 dark:text-zinc-400">
              Email береться з вашого облікового запису.
            </span>
          </div>

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
            <label htmlFor="customerPhone">Телефон</label>
            <input
              id="customerPhone"
              type="tel"
              value={customerPhone}
              onChange={(event) => setCustomerPhone(event.target.value)}
              required
            />
          </div>
        </>
      )}

      {error !== null ? <p role="alert">{error}</p> : null}

      <button type="submit" disabled={pending}>
        Підтвердити запис
      </button>
      <p className="text-sm text-zinc-500 dark:text-zinc-400">
        Місце тримається 15 хвилин. Оплатити запис можна в особистому кабінеті.
      </p>
    </form>
  );
}
