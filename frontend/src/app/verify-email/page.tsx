/**
 * Stage 15 item 6 — email verification landing page (docs/DECISIONS.md
 * § Stage 15 planning, item 6). Stays a Server Component and resolves
 * `slug` the same way every other salon-scoped page does (headers() →
 * SALON_SLUG_HEADER → null-slug "still in development" fallback) —
 * mirrors `booking/pay/page.tsx`'s existing pattern exactly, so the
 * slug-resolution logic is not duplicated or re-derived client-side.
 *
 * The token lives in the URL fragment, never sent to the server on the
 * initial request (same reasoning as `/booking/pay`), so reading it and
 * everything that follows — the verify POST, state branching — is
 * delegated to the client component below.
 */
import { headers } from "next/headers";

import { SALON_SLUG_HEADER } from "@/middleware";

import VerifyEmailStatus from "./VerifyEmailStatus";

export default async function VerifyEmailPage() {
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
      <VerifyEmailStatus slug={slug} />
    </main>
  );
}
