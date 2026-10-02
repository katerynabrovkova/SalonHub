/**
 * Item 8 email-change confirmation landing page (docs/DECISIONS.md § "Item 8
 * decisions (change email)"). Public, outside /client. Stays a Server
 * Component and resolves `slug` the same way as `verify-email/page.tsx`
 * (headers() -> SALON_SLUG_HEADER -> null-slug "still in development"
 * fallback).
 *
 * The token lives in the URL fragment, never sent to the server on the
 * initial request, so reading it and everything that follows is delegated
 * to the client component below.
 */
import { headers } from "next/headers";

import { SALON_SLUG_HEADER } from "@/middleware";

import ConfirmEmailChangeStatus from "./ConfirmEmailChangeStatus";

export default async function ConfirmEmailChangePage() {
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
      <ConfirmEmailChangeStatus slug={slug} />
    </main>
  );
}
