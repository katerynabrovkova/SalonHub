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
 * An unverified `"client"` also sees a line above it asking to verify the
 * email (docs/DECISIONS.md § "Item 13 decisions (verification reminder)",
 * point 7); an admin does not.
 *
 * `BookingSummary` does not depend on the session, so it is rendered at
 * once, including in the server HTML (17.7f, docs/DECISIONS.md § "Stage 17
 * sub-step order"; an exception to the Stage 15 "render nothing while
 * loading" rule). Only the form area waits for auth: while `loading` it is a
 * skeleton shaped like the guest form, with no inputs, button or email.
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

function SkeletonField() {
  return (
    <div className="flex flex-col gap-1">
      <div className="h-5 w-20 rounded bg-zinc-200 dark:bg-zinc-700" />
      <div className="h-7 w-full rounded bg-zinc-200 dark:bg-zinc-700" />
    </div>
  );
}

/** Placeholder for the contact form while auth is loading; shaped like the
 * guest form (three fields and the button) to limit the layout shift. */
function ContactFormSkeleton() {
  return (
    <div className="flex w-full max-w-sm flex-col gap-4">
      <div
        data-testid="contact-form-skeleton"
        aria-hidden="true"
        className="flex animate-pulse flex-col gap-4 motion-reduce:animate-none"
      >
        <SkeletonField />
        <SkeletonField />
        <SkeletonField />
        <div className="h-7 w-36 rounded bg-zinc-200 dark:bg-zinc-700" />
      </div>
      <div role="status">
        <span className="sr-only">Завантаження…</span>
      </div>
    </div>
  );
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

  return (
    <>
      <BookingSummary serviceName={serviceName} specialistName={specialistName} slot={startDatetime} />
      {/* `loading` is true until the mount fetch to `auth/me/` resolves (and
          always on the server), so rendering either form here would risk a
          flash-then-swap, or a guest booking by a logged-in client, once `me`
          settles. The skeleton holds the form's place instead. */}
      {loading ? (
        <ContactFormSkeleton />
      ) : me !== null && me.role === "client" && me.email_verified ? (
        <AccountBookingForm
          slug={slug}
          entry={entry}
          service={service}
          specialist={specialist}
          startDatetime={startDatetime}
          linked={hasLinkedCustomer(me)}
        />
      ) : (
        <>
          {me !== null && me.role === "client" && !me.email_verified ? (
            <p>Підтвердіть пошту, щоб бронювати з акаунта.</p>
          ) : null}
          <ContactInfoForm
            slug={slug}
            entry={entry}
            service={service}
            specialist={specialist}
            startDatetime={startDatetime}
          />
        </>
      )}
    </>
  );
}
