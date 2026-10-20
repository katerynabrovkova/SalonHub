/**
 * Request a password reset link (docs/DECISIONS.md § "Item 11 decisions
 * (forgot password)", points 8 and 9). Public. Renders the form; the form
 * is ForgotPasswordForm.
 *
 * A Server Component so the back link is chosen from `from` before the
 * first paint, with no switch after hydration (point 8, changed
 * 20.10.2026). Only the exact value "change-password" is allowed; anything
 * else, including a repeated key, keeps the login link. The value itself
 * is never passed on.
 */

import ForgotPasswordForm, { type ForgotPasswordBackLink } from "./ForgotPasswordForm";

interface ForgotPasswordPageProps {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}

function backLinkFor(from: string | string[] | undefined): ForgotPasswordBackLink {
  return from === "change-password" ? "change-password" : "login";
}

export default async function ForgotPasswordPage({ searchParams }: ForgotPasswordPageProps) {
  const { from } = await searchParams;
  return <ForgotPasswordForm backLink={backLinkFor(from)} />;
}
