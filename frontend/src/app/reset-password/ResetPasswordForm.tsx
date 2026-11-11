"use client";

/**
 * Reset-password form for /reset-password (docs/DECISIONS.md § "Item 11
 * decisions (forgot password)", points 10-12).
 *
 * The page's inline script (lib/security/fragmentTokenScript.ts,
 * docs/DECISIONS.md "Booking > Guest token") has already moved `uid` and
 * `token` into sessionStorage and removed the hash before hydration; this
 * component never removes the hash itself. The hash is read through
 * `useSyncExternalStore` (null on the server render and during hydration, so
 * the first client render matches the server HTML); a link still in it means
 * the script could not store it (blocked storage). The link, from the hash
 * or else from storage, is copied into state once, while rendering, the
 * first time the hash is known.
 *
 * A stored complete link is then checked once ("Early link check, decided
 * 15.10.2026", point 3), so a dead link is reported before the person types
 * a new password. `checkFiredRef` keeps it to one request per page load,
 * including React strict-mode's mount -> cleanup -> mount, like
 * confirm-email-change; state is set only after the request settles. Only
 * `invalid_or_expired_token` shows the invalid-link state. Any other
 * failure shows the form anyway: confirm validates the link on submit.
 *
 * The confirmation is checked in the browser only, like registration, and
 * is never sent. A 204 ends the local session through `logout()`, then
 * navigates, like item 9. Errors map to fixed texts, never the server's
 * message; `invalid_or_expired_token` shows the same state as a missing or
 * partial hash.
 */

import Link from "next/link";
import { useNavigatingRouter } from "@/lib/navigation/useNavigatingRouter";
import { useEffect, useRef, useState, useSyncExternalStore, type FormEvent } from "react";

import { useAuth } from "@/app/AuthContext";
import PasswordField from "@/app/PasswordField";
import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { PASSWORD_RESET_LINK_KEY } from "@/lib/security/fragmentTokenScript";

interface ResetPasswordFormProps {
  slug: string;
}

interface ResetLink {
  uid: string;
  token: string;
}

const SUCCESS_PATH = "/login?notice=password_changed";
const FORGOT_PASSWORD_PATH = "/forgot-password";

function subscribeToHash(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function readHash(): string | null {
  return window.location.hash;
}

function readServerHash(): string | null {
  return null;
}

function parseLink(hash: string): ResetLink | null {
  const params = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash);
  const uid = params.get("uid");
  const token = params.get("token");
  if (uid === null || uid === "" || token === null || token === "") {
    return null;
  }
  return { uid, token };
}

/** The link the inline script stored (`uid=...&token=...`); null when there
 * is none or storage is blocked. */
function readStoredLink(): ResetLink | null {
  try {
    const value = window.sessionStorage.getItem(PASSWORD_RESET_LINK_KEY);
    return value === null ? null : parseLink(value);
  } catch {
    return null;
  }
}

function isInvalidLink(err: unknown): boolean {
  return err instanceof ApiError && err.code === "invalid_or_expired_token";
}

function resetErrorMessage(err: unknown): string {
  if (err instanceof TypeError) {
    return "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
  }
  // Generic until the Stage 18-21 i18n pass, like item 9.
  if (err instanceof ApiError && err.status === 400 && "new_password" in err.details) {
    return "Цей пароль занадто простий. Оберіть інший.";
  }
  return "Не вдалося змінити пароль. Спробуйте ще раз.";
}

export default function ResetPasswordForm({ slug }: ResetPasswordFormProps) {
  const router = useNavigatingRouter();
  const { logout } = useAuth();
  const hash = useSyncExternalStore(subscribeToHash, readHash, readServerHash);
  // undefined: the hash has not been read yet; null: no usable link.
  const [link, setLink] = useState<ResetLink | null | undefined>(undefined);
  const [linkRejected, setLinkRejected] = useState(false);
  const [linkChecked, setLinkChecked] = useState(false);
  const checkFiredRef = useRef(false);
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Stored once, while rendering, the first time the hash is known.
  if (link === undefined && hash !== null) {
    setLink(parseLink(hash) ?? readStoredLink());
  }

  useEffect(() => {
    if (link === undefined || link === null || checkFiredRef.current) {
      return;
    }
    checkFiredRef.current = true;

    void (async () => {
      let rejected = false;
      try {
        await apiRequest<void>(slug, "/auth/password-reset/check/", {
          method: "POST",
          body: JSON.stringify({ uid: link.uid, token: link.token }),
        });
      } catch (err) {
        rejected = isInvalidLink(err);
      }
      setLinkRejected(rejected);
      setLinkChecked(true);
    })();
  }, [link, slug]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>, resetLink: ResetLink) {
    event.preventDefault();
    setError(null);

    if (newPassword !== confirmPassword) {
      setError("Паролі не збігаються.");
      return;
    }

    setPending(true);
    try {
      await apiRequest<void>(slug, "/auth/password-reset/confirm/", {
        method: "POST",
        body: JSON.stringify({
          uid: resetLink.uid,
          token: resetLink.token,
          new_password: newPassword,
        }),
      });
    } catch (err) {
      if (isInvalidLink(err)) {
        setLinkRejected(true);
      } else {
        setError(resetErrorMessage(err));
      }
      setPending(false);
      return;
    }

    await logout();
    router.replace(SUCCESS_PATH);
  }

  if (link === undefined) {
    return null;
  }

  return (
    <div className="flex flex-col gap-8">
      <h1 className="text-xl font-semibold">Новий пароль</h1>

      {link === null || linkRejected ? (
        <div className="flex w-full max-w-sm flex-col gap-4">
          <p>Посилання недійсне або застаріле.</p>
          <Link href={FORGOT_PASSWORD_PATH} className="text-blue-600 underline">
            Надіслати новий лист
          </Link>
        </div>
      ) : !linkChecked ? (
        <p>Перевіряємо посилання...</p>
      ) : (
        <form
          onSubmit={(event) => void handleSubmit(event, link)}
          className="flex w-full max-w-sm flex-col gap-4"
        >
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
            Зберегти пароль
          </button>
        </form>
      )}
    </div>
  );
}
