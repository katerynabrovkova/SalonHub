"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";

import { useAuth } from "@/app/AuthContext";
import PasswordField from "@/app/PasswordField";
import { readCookie } from "@/lib/api/browserContext";
import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { consumeReturnPath, isSafeReturnPath } from "@/lib/auth/returnPath";
import { resolveSlugFromHost } from "@/lib/routing/resolveSlugFromHost";

const GENERIC_ERROR = "Something went wrong. Please try again.";

// Mirrors middleware.ts's PLATFORM_DOMAIN resolution, but client-side only
// `NEXT_PUBLIC_*` env vars are ever inlined into the browser bundle
// (docs/DECISIONS.md § "Frontend routing: subdomain-based").
const PLATFORM_DOMAIN = process.env.NEXT_PUBLIC_PLATFORM_DOMAIN ?? "salonhub.com";

// Shown after a password change ends the session (docs/DECISIONS.md § "Item
// 9 decisions (change password): ending sessions", "Frontend details,
// decided 06.10.2026", point 3).
const PASSWORD_CHANGED_NOTICE = "Пароль змінено. Увійдіть з новим паролем.";

function subscribeToSearch(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  return () => window.removeEventListener("popstate", onChange);
}

function readSearch(): string | null {
  return window.location.search;
}

function readServerSearch(): string | null {
  return null;
}

export default function LoginPage() {
  const router = useRouter();
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const search = useSyncExternalStore(subscribeToSearch, readSearch, readServerSearch);
  const showPasswordChanged =
    search !== null && new URLSearchParams(search).get("notice") === "password_changed";

  // Guarded: this is a "use client" component but Next.js still renders it
  // once on the server for the initial HTML, where `window` doesn't exist.
  const slug =
    typeof window !== "undefined"
      ? (resolveSlugFromHost(window.location.host, PLATFORM_DOMAIN) ?? "")
      : "";

  useEffect(() => {
    // Prime the CSRF cookie before the user can submit the form — login is a
    // POST, so it needs `csrftoken` set ahead of time (docs/DECISIONS.md
    // § "Login-CSRF is in scope"). A failure is ignored here: submit
    // re-primes if the cookie is still missing (docs/DECISIONS.md, "S3
    // design details", item 7).
    apiRequest(slug, "/auth/csrf/").catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);

    try {
      if (readCookie("csrftoken") === null) {
        try {
          await apiRequest(slug, "/auth/csrf/");
        } catch {
          setError(GENERIC_ERROR);
          return;
        }
      }
      const me = await login(email, password);
      // Consumed only after a successful login, and removed even when it is
      // then rejected (docs/DECISIONS.md, "S3 design details", item 6).
      const returnPath = consumeReturnPath();
      if (returnPath !== null && isSafeReturnPath(returnPath, me.role)) {
        router.push(returnPath);
      } else {
        router.push(me.role === "admin" ? "/admin" : "/client");
      }
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          // The backend collapses "no such email" and "wrong password" into
          // one response — do not re-introduce that distinction here.
          setError("Email or password is incorrect.");
        } else if (err.status === 429) {
          setError("Забагато спроб. Спробуйте пізніше.");
        } else {
          setError(GENERIC_ERROR);
        }
      } else {
        setError(GENERIC_ERROR);
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="flex flex-1 flex-col items-center justify-center p-8">
      {showPasswordChanged ? (
        <p role="status" className="mb-4 w-full max-w-sm">
          {PASSWORD_CHANGED_NOTICE}
        </p>
      ) : null}

      <form
        onSubmit={handleSubmit}
        className="flex w-full max-w-sm flex-col gap-4"
      >
        <h1 className="text-xl font-semibold">Log in</h1>

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

        <PasswordField
          id="password"
          label="Пароль"
          autoComplete="current-password"
          value={password}
          onChange={setPassword}
          required
        />

        {error !== null ? <p role="alert">{error}</p> : null}

        <button type="submit" disabled={pending}>
          Log in
        </button>
      </form>

      {/* docs/DECISIONS.md § "Item 11 decisions (forgot password)", point 13. */}
      <p className="mt-4 text-center text-sm">
        <Link href="/forgot-password" className="text-blue-600 underline">
          Забули пароль?
        </Link>
      </p>

      <p className="mt-4 text-center text-sm">
        Немає акаунту?{" "}
        <Link href="/register" className="text-blue-600 underline">
          Зареєструватися
        </Link>
      </p>
    </main>
  );
}
