"use client";

/**
 * Change-password form for /client/profile/password (docs/DECISIONS.md §
 * "Item 9 decisions (change password): ending sessions", "Endpoint and
 * page, decided 06.10.2026" points 7-10 and "Frontend details, decided
 * 06.10.2026"). Auth is already enforced by app/client/layout.tsx.
 *
 * The confirmation is checked in the browser only, like registration, and
 * is never sent. A 204 means the server has already ended every session:
 * the local session ends through `logout()`, unchanged, and only then does
 * the page navigate, since a deliberate logout leaves the navigation to its
 * caller (client/layout.tsx does not redirect).
 *
 * Errors map by status and code to fixed texts, never the server's message.
 * Only `invalid_password` clears a field (the current password); every
 * other error keeps all values.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useAuth } from "@/app/AuthContext";
import PasswordField from "@/app/PasswordField";
import { apiRequest } from "@/lib/api/client";
import { ApiError, RenewalUnsureError } from "@/lib/api/errors";
import { resolveSlugFromHost } from "@/lib/routing/resolveSlugFromHost";

// Mirrors AuthContext.tsx/login/page.tsx's PLATFORM_DOMAIN resolution.
const PLATFORM_DOMAIN = process.env.NEXT_PUBLIC_PLATFORM_DOMAIN ?? "salonhub.com";

const PROFILE_PATH = "/client/profile";
const SUCCESS_PATH = "/login?notice=password_changed";

function changeErrorMessage(err: unknown): string {
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
      return "Неправильний поточний пароль.";
    }
    if (err.code === "same_password") {
      return "Новий пароль має відрізнятися від поточного.";
    }
    // Generic until the Stage 18-21 i18n pass (point 9).
    if (err.status === 400 && "new_password" in err.details) {
      return "Цей пароль занадто простий. Оберіть інший.";
    }
  }
  return "Не вдалося змінити пароль. Спробуйте ще раз.";
}

function isInvalidPassword(err: unknown): boolean {
  return err instanceof ApiError && err.code === "invalid_password";
}

export default function ChangePasswordForm() {
  const router = useRouter();
  const { logout } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Guarded: this is a "use client" component but Next.js still renders it
  // once on the server for the initial HTML, where `window` doesn't exist.
  const slug =
    typeof window !== "undefined"
      ? (resolveSlugFromHost(window.location.host, PLATFORM_DOMAIN) ?? "")
      : "";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (newPassword !== confirmPassword) {
      setError("Паролі не збігаються.");
      return;
    }

    setPending(true);
    try {
      await apiRequest<void>(slug, "/auth/me/password-change/", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
    } catch (err) {
      if (isInvalidPassword(err)) {
        setCurrentPassword("");
      }
      setError(changeErrorMessage(err));
      setPending(false);
      return;
    }

    await logout();
    router.replace(SUCCESS_PATH);
  }

  return (
    <main className="flex flex-col gap-8 p-8">
      <Link href={PROFILE_PATH} className="text-sm text-blue-600 underline">
        &larr; Профіль
      </Link>

      <h1 className="text-xl font-semibold">Змінити пароль</h1>

      <p className="w-full max-w-sm">
        Після зміни пароля ви вийдете з акаунта на всіх пристроях, і потрібно буде увійти
        знову.
      </p>

      <form onSubmit={handleSubmit} className="flex w-full max-w-sm flex-col gap-4">
        <PasswordField
          id="current-password"
          label="Поточний пароль"
          value={currentPassword}
          onChange={setCurrentPassword}
          autoComplete="current-password"
          required
        />

        <PasswordField
          id="new-password"
          label="Новий пароль"
          value={newPassword}
          onChange={setNewPassword}
          autoComplete="new-password"
          hint="Пароль має містити щонайменше 8 символів."
          required
        />

        <PasswordField
          id="confirm-password"
          label="Підтвердіть новий пароль"
          value={confirmPassword}
          onChange={setConfirmPassword}
          autoComplete="new-password"
          required
        />

        {error !== null ? <p role="alert">{error}</p> : null}

        <button type="submit" disabled={pending}>
          Змінити пароль
        </button>
      </form>
    </main>
  );
}
