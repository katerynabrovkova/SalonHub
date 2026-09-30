"use client";

/**
 * One-field edit form for the linked Customer's name or phone
 * (docs/DECISIONS.md § "Item 10 design details (edit name and phone)",
 * point 6 and its "Frontend details"). Shared by /client/profile/name and
 * /client/profile/phone, which only pass settings. Auth is already enforced
 * by app/client/layout.tsx.
 *
 * Submit sends a PATCH with only this field, then `refresh()`, then
 * navigates to the profile -- the same order as AccountBookingForm, so the
 * profile never renders the old value. `refresh()` never rejects.
 *
 * An Account with no linked Customer (`me.name === null`) is sent back to
 * the profile, which doesn't link here in that state. A 404 from the PATCH
 * means the same thing (the backend found no linked Customer), so it
 * redirects the same way instead of showing an error.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { useAuth } from "@/app/AuthContext";
import { apiRequest } from "@/lib/api/client";
import { ApiError, RenewalUnsureError } from "@/lib/api/errors";
import { hasLinkedCustomer } from "@/lib/auth/hasLinkedCustomer";
import { resolveSlugFromHost } from "@/lib/routing/resolveSlugFromHost";

// Mirrors AuthContext.tsx/login/page.tsx's PLATFORM_DOMAIN resolution.
const PLATFORM_DOMAIN = process.env.NEXT_PUBLIC_PLATFORM_DOMAIN ?? "salonhub.com";

const PROFILE_PATH = "/client/profile";

export interface EditProfileFieldProps {
  field: "name" | "phone";
  title: string;
  label: string;
  inputType: "text" | "tel";
  maxLength: number;
}

function saveErrorMessage(err: unknown): string {
  // The request never reached the server (fetch threw), or apiRequest's
  // silent renewal couldn't tell whether the session survived: both read as
  // a connectivity problem, same as the dashboard's pay action.
  if (err instanceof TypeError || err instanceof RenewalUnsureError) {
    return "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
  }
  // With a live session hint the layout has already redirected to /login;
  // this text covers a 401 without one.
  if (err instanceof ApiError && err.status === 401) {
    return "Сесія завершилась. Увійдіть знову.";
  }
  // Includes 400: fixed text, never DRF's field messages.
  return "Не вдалося зберегти. Спробуйте ще раз.";
}

export default function EditProfileField({
  field,
  title,
  label,
  inputType,
  maxLength,
}: EditProfileFieldProps) {
  const router = useRouter();
  const { me, refresh } = useAuth();
  const [value, setValue] = useState(me?.[field] ?? "");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const linked = me !== null && hasLinkedCustomer(me);

  useEffect(() => {
    if (me !== null && !hasLinkedCustomer(me)) {
      router.replace(PROFILE_PATH);
    }
  }, [me, router]);

  // Guarded: this is a "use client" component but Next.js still renders it
  // once on the server for the initial HTML, where `window` doesn't exist.
  const slug =
    typeof window !== "undefined"
      ? (resolveSlugFromHost(window.location.host, PLATFORM_DOMAIN) ?? "")
      : "";

  if (!linked) {
    return null;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);

    try {
      await apiRequest(slug, "/auth/me/customer/", {
        method: "PATCH",
        body: JSON.stringify({ [field]: value }),
      });
      await refresh();
      router.push(PROFILE_PATH);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        router.replace(PROFILE_PATH);
        return;
      }
      setError(saveErrorMessage(err));
    } finally {
      setPending(false);
    }
  }

  const inputId = `profile-${field}`;

  return (
    <main className="flex flex-col gap-8 p-8">
      <Link href={PROFILE_PATH} className="text-sm text-blue-600 underline">
        &larr; Профіль
      </Link>

      <h1 className="text-xl font-semibold">{title}</h1>

      <form onSubmit={handleSubmit} className="flex w-full max-w-sm flex-col gap-4">
        <div className="flex flex-col gap-1">
          <label htmlFor={inputId}>{label}</label>
          <input
            id={inputId}
            type={inputType}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            maxLength={maxLength}
            required
          />
        </div>

        {error !== null ? <p role="alert">{error}</p> : null}

        <button type="submit" disabled={pending}>
          Зберегти
        </button>
      </form>
    </main>
  );
}
