"use client";

/**
 * Request a password reset link (docs/DECISIONS.md § "Item 11 decisions
 * (forgot password)", points 8 and 9). Public. Only renders the form; the
 * form is ForgotPasswordForm.
 */

import ForgotPasswordForm from "./ForgotPasswordForm";

export default function ForgotPasswordPage() {
  return <ForgotPasswordForm />;
}
