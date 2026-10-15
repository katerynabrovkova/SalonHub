"use client";

/**
 * Forgot-password form for /forgot-password (docs/DECISIONS.md § "Item 11
 * decisions (forgot password)", points 8 and 9). Public.
 *
 * A 202 replaces the form with the "sent" screen, whose text is the same
 * whether or not the address is registered. "Надіслати ще раз" repeats the
 * request with the address of the last successful send, kept in state.
 * "Змінити" returns to the form with the address kept, like registration
 * and item 8.
 *
 * Errors map by status to fixed texts, never the server's message. The 429
 * limit is per IP and reveals nothing about accounts.
 */

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { resolveSlugFromHost } from "@/lib/routing/resolveSlugFromHost";

// Mirrors AuthContext.tsx/login/page.tsx's PLATFORM_DOMAIN resolution.
const PLATFORM_DOMAIN = process.env.NEXT_PUBLIC_PLATFORM_DOMAIN ?? "salonhub.com";

const LOGIN_PATH = "/login";

function sendErrorMessage(err: unknown): string {
  if (err instanceof TypeError) {
    return "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
  }
  if (err instanceof ApiError) {
    if (err.status === 429) {
      return "Забагато спроб. Спробуйте пізніше.";
    }
    if (err.status === 400 && "email" in err.details) {
      return "Перевірте адресу пошти.";
    }
  }
  return "Не вдалося надіслати. Спробуйте ще раз.";
}

export default function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [sentEmail, setSentEmail] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resent, setResent] = useState(false);
  const [pending, setPending] = useState(false);

  // Guarded: this is a "use client" component but Next.js still renders it
  // once on the server for the initial HTML, where `window` doesn't exist.
  const slug =
    typeof window !== "undefined"
      ? (resolveSlugFromHost(window.location.host, PLATFORM_DOMAIN) ?? "")
      : "";

  function send(address: string): Promise<void> {
    return apiRequest<void>(slug, "/auth/password-reset/", {
      method: "POST",
      body: JSON.stringify({ email: address }),
    });
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);

    const address = email;
    try {
      await send(address);
      setSentEmail(address);
      setResent(false);
    } catch (err) {
      setError(sendErrorMessage(err));
    } finally {
      setPending(false);
    }
  }

  async function handleResend(address: string) {
    setError(null);
    setResent(false);
    setPending(true);

    try {
      await send(address);
      setResent(true);
    } catch (err) {
      setError(sendErrorMessage(err));
    } finally {
      setPending(false);
    }
  }

  function handleChangeAddress(address: string) {
    setError(null);
    setEmail(address);
    setSentEmail(null);
    setResent(false);
  }

  return (
    <main className="flex flex-col gap-8 p-8">
      <Link href={LOGIN_PATH} className="text-sm text-blue-600 underline">
        &larr; Вхід
      </Link>

      <h1 className="text-xl font-semibold">Відновлення пароля</h1>

      {sentEmail !== null ? (
        <div className="flex w-full max-w-sm flex-col gap-4">
          <p>
            Якщо ця адреса зареєстрована, ми надіслали на неї лист із посиланням. Посилання
            діє 1 годину.
          </p>

          <button type="button" onClick={() => void handleResend(sentEmail)} disabled={pending}>
            Надіслати ще раз
          </button>

          {resent ? <p role="status">Лист надіслано ще раз.</p> : null}
          {error !== null ? <p role="alert">{error}</p> : null}

          <button
            type="button"
            onClick={() => handleChangeAddress(sentEmail)}
            className="text-left text-blue-600 underline"
          >
            Вказали не ту адресу? Змінити
          </button>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="flex w-full max-w-sm flex-col gap-4">
          <p>
            Вкажіть адресу пошти, з якою ви входите. Ми надішлемо посилання, щоб задати новий
            пароль.
          </p>

          <div className="flex flex-col gap-1">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>

          {error !== null ? <p role="alert">{error}</p> : null}

          <button type="submit" disabled={pending}>
            Надіслати посилання
          </button>
        </form>
      )}
    </main>
  );
}
