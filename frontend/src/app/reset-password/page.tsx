/**
 * Password reset landing page (docs/DECISIONS.md § "Item 11 decisions
 * (forgot password)", points 10-12). Public, outside /client. Stays a
 * Server Component and resolves `slug` the same way as
 * `confirm-email-change/page.tsx` (headers() -> SALON_SLUG_HEADER ->
 * null-slug "still in development" fallback).
 *
 * `uid` and `token` live in the URL fragment, never sent to the server on
 * the initial request, so reading them and everything that follows is
 * delegated to the client component below.
 */
import { headers } from "next/headers";

import { SALON_SLUG_HEADER } from "@/middleware";

import ResetPasswordForm from "./ResetPasswordForm";

export default async function ResetPasswordPage() {
  const slug = (await headers()).get(SALON_SLUG_HEADER);
  if (slug === null) {
    return (
      <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
        <p>The platform is still in development.</p>
      </main>
    );
  }

  return (
    <main className="flex flex-col gap-8 p-8">
      <ResetPasswordForm slug={slug} />
    </main>
  );
}
