"use client";

import Link from "next/link";
import { useState } from "react";

import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { resolveSlugFromHost } from "@/lib/routing/resolveSlugFromHost";

// Mirrors middleware.ts's PLATFORM_DOMAIN resolution, but client-side only
// `NEXT_PUBLIC_*` env vars are ever inlined into the browser bundle
// (docs/DECISIONS.md § "Frontend routing: subdomain-based") — same pattern
// login/page.tsx uses.
const PLATFORM_DOMAIN = process.env.NEXT_PUBLIC_PLATFORM_DOMAIN ?? "salonhub.com";

const CHECK_EMAIL_MESSAGE =
  "Якщо ви реєструєтесь уперше, лист із посиланням для підтвердження вже у вашій поштовій скриньці.";
const RESEND_MESSAGE = "Якщо у нас є ваша адреса, ми щойно надіслали новий лист.";

/**
 * `RegisterSerializer`'s `password` field runs Django's `validate_password`
 * (similarity/common-password/numeric-only checks beyond length, backend
 * `accounts/serializers.py`) — rules this page can't know client-side, so a
 * 400 must surface whatever message the server actually returned. DRF field
 * errors land in the error envelope's `details` (keyed by field name), not
 * its generic top-level `message` — see `core/exceptions.py`'s
 * `exception_handler`: only DRF's `{"detail": ...}` shape (not a per-field
 * validation dict) maps to `message`.
 */
function registerErrorMessage(err: ApiError): string {
  const passwordErrors = err.details["password"];
  if (Array.isArray(passwordErrors) && typeof passwordErrors[0] === "string") {
    return passwordErrors[0];
  }
  const emailErrors = err.details["email"];
  if (Array.isArray(emailErrors) && typeof emailErrors[0] === "string") {
    return emailErrors[0];
  }
  return err.message;
}

type View = "form" | "check-email";

/**
 * Unlike login/page.tsx, this page does not prime the `csrftoken` cookie on
 * mount. `RegisterView`/`ResendVerificationView` are `AllowAny` and never
 * cookie-authenticated, so — per accounts/views.py's module docstring —
 * they never call `enforce_csrf_on_unsafe`; only the three cookie-driven
 * views (login/refresh/logout) enforce CSRF. Priming here would be inert.
 */
export default function RegisterPage() {
  const [view, setView] = useState<View>("form");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const [resendEmail, setResendEmail] = useState("");
  const [resendPending, setResendPending] = useState(false);
  const [resendMessage, setResendMessage] = useState<string | null>(null);

  // Guarded: this is a "use client" component but Next.js still renders it
  // once on the server for the initial HTML, where `window` doesn't exist.
  const slug =
    typeof window !== "undefined"
      ? (resolveSlugFromHost(window.location.host, PLATFORM_DOMAIN) ?? "")
      : "";

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    // Client-side only -- RegisterSerializer has no confirm-password field,
    // this check never reaches the server.
    if (password !== confirmPassword) {
      setError("Паролі не збігаються.");
      return;
    }

    setPending(true);
    try {
      await apiRequest(slug, "/auth/register/", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      // No auto-login: RegisterView doesn't set auth cookies -- registration
      // only starts the email-verification flow.
      setResendEmail(email);
      setResendMessage(null);
      setView("check-email");
    } catch (err) {
      setError(err instanceof ApiError ? registerErrorMessage(err) : "Щось пішло не так. Спробуйте ще раз.");
    } finally {
      setPending(false);
    }
  }

  async function handleResend(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setResendPending(true);
    setResendMessage(null);
    try {
      await apiRequest(slug, "/auth/resend-verification/", {
        method: "POST",
        body: JSON.stringify({ email: resendEmail }),
      });
    } catch {
      // ResendVerificationView is 202-always, no-enumeration by design --
      // the outcome shown below is identical whether this resolves or
      // rejects, so the network/5xx failure path here is deliberately a
      // silent no-op rather than a distinct error message.
    } finally {
      setResendPending(false);
      setResendMessage(RESEND_MESSAGE);
    }
  }

  function handleRestart() {
    setView("form");
    setError(null);
    setPassword("");
    setConfirmPassword("");
    setResendMessage(null);
  }

  if (view === "check-email") {
    return (
      <main className="flex flex-1 flex-col items-center justify-center p-8">
        <div className="flex w-full max-w-sm flex-col gap-4">
          <h1 className="text-xl font-semibold">Перевірте пошту</h1>
          <p>{CHECK_EMAIL_MESSAGE}</p>

          <form onSubmit={handleResend} className="flex flex-col gap-2">
            <label htmlFor="resend-email">Email</label>
            <input
              id="resend-email"
              type="email"
              autoComplete="email"
              value={resendEmail}
              onChange={(event) => setResendEmail(event.target.value)}
              required
            />
            <button type="submit" disabled={resendPending}>
              Надіслати ще раз
            </button>
          </form>

          {resendMessage !== null ? <p role="status">{resendMessage}</p> : null}

          <button type="button" onClick={handleRestart} className="text-left text-blue-600 underline">
            Вказали не ту адресу? Змінити email
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="flex flex-1 flex-col items-center justify-center p-8">
      <form onSubmit={handleSubmit} className="flex w-full max-w-sm flex-col gap-4">
        <h1 className="text-xl font-semibold">Зареєструватися</h1>

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

        <div className="flex flex-col gap-1">
          <label htmlFor="password">Пароль</label>
          <input
            id="password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
          {/* Cosmetic only -- real validation is server-side via Django's
              validate_password, which enforces more than just length. */}
          <p className="text-sm text-gray-500">Щонайменше 8 символів</p>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="confirm-password">Підтвердіть пароль</label>
          <input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            required
          />
        </div>

        {error !== null ? <p role="alert">{error}</p> : null}

        <button type="submit" disabled={pending}>
          Зареєструватися
        </button>
      </form>

      <p className="mt-4 text-center text-sm">
        Вже маєте акаунт?{" "}
        <Link href="/login" className="text-blue-600 underline">
          Увійти
        </Link>
      </p>
    </main>
  );
}
