"use client";

/**
 * Change the account's email (docs/DECISIONS.md § "Item 8 decisions (change
 * email)"). Only renders the form; the form is ChangeEmailForm.
 */

import ChangeEmailForm from "./ChangeEmailForm";

export default function ChangeEmailPage() {
  return <ChangeEmailForm />;
}
