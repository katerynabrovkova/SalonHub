"use client";

/**
 * Booking step 4's auth-aware wrapper (Stage 15 item 5, Cycle D2b): decides
 * between the guest and account flows and renders `BookingSummary` above
 * whichever form applies. `booking/page.tsx`'s step-4 branch is a Server
 * Component and has no session awareness of its own -- this is the client
 * boundary that reads `useAuth()`, mirroring `ClientLayout`'s own
 * loading/session-branch shape.
 *
 * Cycle D2b scope only: every non-loading state (no session, `"admin"`,
 * unverified `"client"`, and -- for now -- a verified `"client"` too) renders
 * the existing guest form unchanged. The account-aware branches (verified
 * client, linked vs. unlinked Customer) are Cycle D3's scope, not added
 * here.
 */
import { useAuth } from "@/app/AuthContext";

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
  const { loading } = useAuth();

  // Mirrors ClientLayout's own reasoning: `loading` briefly reads before the
  // mount fetch to `auth/me/` resolves, so rendering the guest form here
  // would risk a flash-then-swap once the account branches land in Cycle D3.
  if (loading) {
    return null;
  }

  return (
    <>
      <BookingSummary serviceName={serviceName} specialistName={specialistName} slot={startDatetime} />
      <ContactInfoForm
        slug={slug}
        entry={entry}
        service={service}
        specialist={specialist}
        startDatetime={startDatetime}
      />
    </>
  );
}
