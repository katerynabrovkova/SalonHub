"use client";

/**
 * Change-email form for /client/profile/email (docs/DECISIONS.md § "Item 8
 * decisions (change email)", "Frontend" and "Frontend details, decided
 * 02.10.2026"). Auth is already enforced by app/client/layout.tsx.
 *
 * A 202 replaces the form with the "sent" screen. Nothing on the account
 * changes until the link in the letter is confirmed, so no `refresh()` here.
 * "Надіслати ще раз" repeats the request with the values of the last
 * successful send, kept in state. "Змінити", and an `invalid_password` on
 * resend (the password changed in between), return to the form with the
 * address kept and the password cleared, like registration.
 *
 * Errors map by status and code to fixed texts, never the server's message.
 * The resend's own 429 text is deliberate: unlike registration's public
 * resend, the password here was already accepted, so nothing is hidden.
 */

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { useAuth } from "@/app/AuthContext";
import PasswordField from "@/app/PasswordField";
import { apiRequest } from "@/lib/api/client";
import { ApiError, RenewalUnsureError } from "@/lib/api/errors";
import { resolveSlugFromHost } from "@/lib/routing/resolveSlugFromHost";

// Mirrors AuthContext.tsx/login/page.tsx's PLATFORM_DOMAIN resolution.
const PLATFORM_DOMAIN = process.env.NEXT_PUBLIC_PLATFORM_DOMAIN ?? "salonhub.com";

const PROFILE_PATH = "/client/profile";

const INVALID_PASSWORD_MESSAGE = "Неправильний пароль.";

interface SentValues {
  newEmail: string;
  password: string;
}

function sendErrorMessage(err: unknown): string {
  // Same connectivity reading as item 10's EditProfileField.
  if (err instanceof TypeError || err instanceof RenewalUnsureError) {
    return "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
  }
  if (err instanceof ApiError) {
    if (err.status === 401) {
      return "Сесія завершилась. Увійдіть знову.";
    }
    if (err.status === 429) {
      return "Забагато спроб. Спробуйте пізніше.";
    }
    if (err.code === "invalid_password") {
      return INVALID_PASSWORD_MESSAGE;
    }
    if (err.code === "same_email") {
      return "Це вже ваша адреса.";
    }
    if (err.status === 400 && "new_email" in err.details) {
      return "Перевірте адресу пошти.";
    }
  }
  return "Не вдалося надіслати. Спробуйте ще раз.";
}

function isInvalidPassword(err: unknown): boolean {
  return err instanceof ApiError && err.code === "invalid_password";
}

export default function ChangeEmailForm() {
  const { me } = useAuth();
  const [newEmail, setNewEmail] = useState("");
  const [password, setPassword] = useState("");
  const [sent, setSent] = useState<SentValues | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resent, setResent] = useState(false);
  const [pending, setPending] = useState(false);

  // Guarded: this is a "use client" component but Next.js still renders it
  // once on the server for the initial HTML, where `window` doesn't exist.
  const slug =
    typeof window !== "undefined"
      ? (resolveSlugFromHost(window.location.host, PLATFORM_DOMAIN) ?? "")
      : "";

  // Defensive only: app/client/layout.tsx never renders this subtree unless
  // `me` is already non-null.
  if (me === null) {
    return null;
  }

  function send(values: SentValues): Promise<void> {
    return apiRequest<void>(slug, "/auth/me/email-change/", {
      method: "POST",
      body: JSON.stringify({ new_email: values.newEmail, password: values.password }),
    });
  }

  function backToForm(address: string) {
    setNewEmail(address);
    setPassword("");
    setSent(null);
    setResent(false);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);

    const values = { newEmail, password };
    try {
      await send(values);
      setSent(values);
      setResent(false);
    } catch (err) {
      setError(sendErrorMessage(err));
    } finally {
      setPending(false);
    }
  }

  async function handleResend(values: SentValues) {
    setError(null);
    setResent(false);
    setPending(true);

    try {
      await send(values);
      setResent(true);
    } catch (err) {
      if (isInvalidPassword(err)) {
        backToForm(values.newEmail);
      }
      setError(sendErrorMessage(err));
    } finally {
      setPending(false);
    }
  }

  function handleChangeAddress(values: SentValues) {
    setError(null);
    backToForm(values.newEmail);
  }

  return (
    <main className="flex flex-col gap-8 p-8">
      <Link href={PROFILE_PATH} className="text-sm text-blue-600 underline">
        &larr; Профіль
      </Link>

      <h1 className="text-xl font-semibold">Змінити email</h1>

      {sent !== null ? (
        <div className="flex w-full max-w-sm flex-col gap-4">
          <p>
            Ми надіслали лист на {sent.newEmail}. Перейдіть за посиланням у листі, щоб
            підтвердити нову адресу.
          </p>

          <button type="button" onClick={() => void handleResend(sent)} disabled={pending}>
            Надіслати ще раз
          </button>

          {resent ? <p role="status">Лист надіслано ще раз.</p> : null}
          {error !== null ? <p role="alert">{error}</p> : null}

          <button
            type="button"
            onClick={() => handleChangeAddress(sent)}
            className="text-left text-blue-600 underline"
          >
            Вказали не ту адресу? Змінити
          </button>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="flex w-full max-w-sm flex-col gap-4">
          <div className="flex flex-col gap-1">
            <span className="text-sm text-zinc-500 dark:text-zinc-400">Поточна адреса</span>
            <span>{me.email}</span>
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="new-email">Нова адреса</label>
            <input
              id="new-email"
              type="email"
              autoComplete="email"
              value={newEmail}
              onChange={(event) => setNewEmail(event.target.value)}
              required
            />
          </div>

          <PasswordField
            id="current-password"
            label="Поточний пароль"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
            required
          />

          <p className="text-sm text-gray-500">
            Ми надішлемо лист із підтвердженням на нову адресу. Поточна адреса лишається
            робочою для входу, доки ви її не підтвердите.
          </p>

          {error !== null ? <p role="alert">{error}</p> : null}

          <button type="submit" disabled={pending}>
            Надіслати підтвердження
          </button>
        </form>
      )}
    </main>
  );
}
