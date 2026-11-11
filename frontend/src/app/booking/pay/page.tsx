/**
 * Booking step 5 — payment status/action page (docs/DECISIONS.md § Stage 14
 * step 5: `/booking/pay` architecture). Stays a Server Component and
 * resolves `slug` the same way every other booking-flow step does
 * (headers() → SALON_SLUG_HEADER → null-slug "still in development"
 * fallback) — mirrors `booking/page.tsx`'s existing pattern exactly, so the
 * slug-resolution logic is not duplicated or re-derived client-side.
 *
 * `appointment_id`/`token` live in the URL fragment, never sent to the
 * server (§ Stage 3). The inline script below moves the token to
 * sessionStorage before hydration; reading it and everything that follows —
 * status branching, the pay action — is delegated to the client component
 * below.
 */
import { headers } from "next/headers";

import FragmentTokenScript from "@/app/FragmentTokenScript";
import { PAY_TOKEN_SCRIPT_CONFIG } from "@/lib/security/fragmentTokenScript";
import { SALON_SLUG_HEADER } from "@/middleware";

import PaymentStatus from "./PaymentStatus";

export default async function BookingPayPage() {
  const slug = (await headers()).get(SALON_SLUG_HEADER);
  if (slug === null) {
    return (
      <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
        <p>Платформа ще в розробці.</p>
      </main>
    );
  }

  return (
    <main className="flex flex-col gap-6 p-8">
      {/* Before the component: moves the token to sessionStorage and keeps
          only #appointment_id=<id> before Next.js reads the URL
          (docs/DECISIONS.md "Booking > Guest token"). */}
      <FragmentTokenScript config={PAY_TOKEN_SCRIPT_CONFIG} />
      <PaymentStatus slug={slug} />
    </main>
  );
}
