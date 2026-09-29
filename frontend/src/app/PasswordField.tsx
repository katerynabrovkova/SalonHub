"use client";

/**
 * Shared password input with a show/hide toggle (docs/DECISIONS.md § Stage 15,
 * "Shared password field design details").
 *
 * Only the user changes visibility: no timers, no hiding on blur or on any
 * other event. `required` is an explicit prop rather than a spread of
 * arbitrary input props, so a caller can never override `type` and break the
 * toggle.
 */

import { useState } from "react";

export interface PasswordFieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: "current-password" | "new-password";
  hint?: string;
  required?: boolean;
}

export default function PasswordField({
  id,
  label,
  value,
  onChange,
  autoComplete,
  hint,
  required,
}: PasswordFieldProps) {
  const [shown, setShown] = useState(false);
  const hintId = hint !== undefined ? `${id}-hint` : undefined;

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id}>{label}</label>
      <div className="relative">
        <input
          id={id}
          type={shown ? "text" : "password"}
          autoComplete={autoComplete}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          required={required}
          aria-describedby={hintId}
          className="w-full pr-24"
        />
        <button
          type="button"
          aria-controls={id}
          onClick={() => setShown((current) => !current)}
          className="absolute inset-y-0 right-0 px-2 text-sm text-blue-600 underline"
        >
          {shown ? "Сховати" : "Показати"}
        </button>
      </div>
      {hint !== undefined ? (
        <p id={hintId} className="text-sm text-gray-500">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
