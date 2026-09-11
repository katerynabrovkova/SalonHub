"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { resolveSlugFromHost } from "@/lib/routing/resolveSlugFromHost";

// Mirrors middleware.ts's PLATFORM_DOMAIN resolution, but client-side only
// `NEXT_PUBLIC_*` env vars are ever inlined into the browser bundle
// (docs/DECISIONS.md § "Frontend routing: subdomain-based").
const PLATFORM_DOMAIN = process.env.NEXT_PUBLIC_PLATFORM_DOMAIN ?? "salonhub.com";

interface Me {
  email: string;
  role: "admin" | "client";
}

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Guarded: this is a "use client" component but Next.js still renders it
  // once on the server for the initial HTML, where `window` doesn't exist.
  const slug =
    typeof window !== "undefined"
      ? (resolveSlugFromHost(window.location.host, PLATFORM_DOMAIN) ?? "")
      : "";

  useEffect(() => {
    // Prime the CSRF cookie before the user can submit the form — login is a
    // POST, so it needs `csrftoken` set ahead of time (docs/DECISIONS.md
    // § "Login-CSRF is in scope").
    void apiRequest(slug, "/auth/csrf/");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);

    try {
      await apiRequest(slug, "/auth/login/", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      const me = await apiRequest<Me>(slug, "/auth/me/");
      router.push(me.role === "admin" ? "/admin" : "/client");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          // The backend collapses "no such email" and "wrong password" into
          // one response — do not re-introduce that distinction here.
          setError("Email or password is incorrect.");
        } else if (err.status === 429) {
          setError("Too many attempts. Please try again later.");
        } else {
          setError("Something went wrong. Please try again.");
        }
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="flex flex-1 flex-col items-center justify-center p-8">
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

        <div className="flex flex-col gap-1">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </div>

        {error !== null ? <p role="alert">{error}</p> : null}

        <button type="submit" disabled={pending}>
          Log in
        </button>
      </form>
    </main>
  );
}
