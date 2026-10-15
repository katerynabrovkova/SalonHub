"use client";

/**
 * Change the account's password (docs/DECISIONS.md § "Item 9 decisions
 * (change password): ending sessions"). Only renders the form; the form is
 * ChangePasswordForm.
 */

import ChangePasswordForm from "./ChangePasswordForm";

export default function ChangePasswordPage() {
  return <ChangePasswordForm />;
}
