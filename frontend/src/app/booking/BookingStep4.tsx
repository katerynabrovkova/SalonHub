"use client";

/**
 * Booking step 4's auth-aware wrapper (Stage 15 item 5, Cycle D3b): decides
 * between the guest and account flows and renders `BookingSummary` above
 * whichever form applies. `booking/page.tsx`'s step-4 branch is a Server
 * Component and has no session awareness of its own -- this is the client
 * boundary that reads `useAuth()`, mirroring `ClientLayout`'s own
 * loading/session-branch shape.
 *
 * A verified `"client"` (`me !== null && me.role === "client" &&
 * me.email_verified`) renders `AccountBookingForm`, with `linked` derived
 * via `hasLinkedCustomer(me)`. Every other state -- no session, `"admin"`,
 * an unverified `"client"` -- renders the existing guest form unchanged.
 */
import { useAuth } from "@/app/AuthContext";
import { hasLinkedCustomer } from "@/lib/auth/hasLinkedCustomer";

import AccountBookingForm from "./AccountBookingForm";
import BookingSummary from "./BookingSummary";
import ContactInfoForm from "./ContactInfoForm";

interface BookingStep4Props {
  serviceName: string;
  specialistName: string | null;
  slug: string;
  entry: "service" | "specialist";
  service: string;
  specialist: string;
  startDatetime: string;
}

export default function BookingStep4({
  serviceName,
  specialistName,
  slug,
  entry,
  service,
  specialist,
  startDatetime,
}: BookingStep4Props) {
  const { me, loading } = useAuth();

  // Mirrors ClientLayout's own reasoning: `loading` briefly reads before the
  // mount fetch to `auth/me/` resolves, so rendering either form here would
  // risk a flash-then-swap once `me` settles.
  if (loading) {
    return null;
  }

  return (
    <>
      <BookingSummary serviceName={serviceName} specialistName={specialistName} slot={startDatetime} />
      {me !== null && me.role === "client" && me.email_verified ? (
        <AccountBookingForm
          slug={slug}
          entry={entry}
          service={service}
          specialist={specialist}
          startDatetime={startDatetime}
          linked={hasLinkedCustomer(me)}
        />
      ) : (
        <ContactInfoForm
          slug={slug}
          entry={entry}
          service={service}
          specialist={specialist}
          startDatetime={startDatetime}
        />
      )}
    </>
  );
}
