/**
 * Booking step 5 — payment status/action page (docs/DECISIONS.md § Stage 14
 * step 5: `/booking/pay` architecture). Stays a Server Component and
 * resolves `slug` the same way every other booking-flow step does
 * (headers() → SALON_SLUG_HEADER → null-slug "still in development"
 * fallback) — mirrors `booking/page.tsx`'s existing pattern exactly, so the
 * slug-resolution logic is not duplicated or re-derived client-side.
 *
 * `appointment_id`/`token` live in the URL fragment, never sent to the
 * server (§ Stage 3), so reading them and everything that follows —
 * status branching, the pay action — is delegated to the client component
 * below.
 */
import { headers } from "next/headers";

import { SALON_SLUG_HEADER } from "@/middleware";

import PaymentStatus from "./PaymentStatus";

export default async function BookingPayPage() {
  const slug = (await headers()).get(SALON_SLUG_HEADER);
  if (slug === null) {
    return (
      <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
        <p>The platform is still in development.</p>
      </main>
    );
  }

  return (
    <main className="flex flex-col gap-6 p-8">
      <PaymentStatus slug={slug} />
    </main>
  );
}
