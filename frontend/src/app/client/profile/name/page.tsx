"use client";

/**
 * Edit the linked Customer's name (docs/DECISIONS.md § "Item 10 design
 * details (edit name and phone)", point 6). Only passes settings; the form
 * is EditProfileField.
 */

import EditProfileField from "../EditProfileField";

export default function EditNamePage() {
  return (
    <EditProfileField
      field="name"
      title="Змінити ім'я"
      label="Ім'я"
      inputType="text"
      maxLength={255}
    />
  );
}
