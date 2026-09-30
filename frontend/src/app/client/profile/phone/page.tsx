"use client";

/**
 * Edit the linked Customer's phone (docs/DECISIONS.md § "Item 10 design
 * details (edit name and phone)", point 6). Only passes settings; the form
 * is EditProfileField.
 */

import EditProfileField from "../EditProfileField";

export default function EditPhonePage() {
  return (
    <EditProfileField
      field="phone"
      title="Змінити телефон"
      label="Телефон"
      inputType="tel"
      maxLength={32}
    />
  );
}
