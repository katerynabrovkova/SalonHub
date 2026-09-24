"use client";

/**
 * Shared session-state container (docs/DECISIONS.md § Stage 15 planning,
 * item 2). Lives in the root layout (frontend/src/app/layout.tsx), not
 * scoped to /login+/client — /booking (Stage 15 item 5) is a sibling route
 * tree that also needs session awareness, the same "layout persists across
 * page-level navigations" reasoning as BookingContactInfoContext, one level
 * higher up the route tree.
 *
 * On mount, fetches `auth/me/` exactly once. A 401 (not logged in) is a
 * normal, expected outcome here — every response failure collapses to
 * `me: null` rather than a thrown error, so an anonymous visitor never sees
 * a crash from the root layout itself.
 */

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { whenRenewalIdle } from "@/lib/api/renewSession";
import { SESSION_EXPIRED_EVENT, deleteSessionHint } from "@/lib/api/sessionHint";
import type { Me } from "@/lib/auth/Me";
import { consumeReturnPath } from "@/lib/auth/returnPath";
import { resolveSlugFromHost } from "@/lib/routing/resolveSlugFromHost";

// Mirrors middleware.ts's PLATFORM_DOMAIN resolution, but client-side only
// `NEXT_PUBLIC_*` env vars are ever inlined into the browser bundle
// (docs/DECISIONS.md § "Frontend routing: subdomain-based") — same pattern
// app/login/page.tsx used before this refactor.
const PLATFORM_DOMAIN = process.env.NEXT_PUBLIC_PLATFORM_DOMAIN ?? "salonhub.com";

interface AuthContextValue {
  me: Me | null;
  loading: boolean;
  loggedOutDeliberately: boolean;
  login: (email: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  // docs/DECISIONS.md § "Session renewal and session lifetime", "S3 design
  // details", item 4. Set only by logout() and reset only by login(), so
  // client/layout.tsx can tell a deliberate logout from a lost session.
  const [loggedOutDeliberately, setLoggedOutDeliberately] = useState(false);

  // Guarded: this is a "use client" component but Next.js still renders it
  // once on the server for the initial HTML, where `window` doesn't exist.
  const slug =
    typeof window !== "undefined"
      ? (resolveSlugFromHost(window.location.host, PLATFORM_DOMAIN) ?? "")
      : "";

  // Registered before the mount effect below so the listener is active
  // before the first /auth/me/ call can fail (docs/DECISIONS.md § "Session
  // renewal and session lifetime", "S2 design details").
  useEffect(() => {
    function handleSessionExpired() {
      setMe(null);
    }

    window.addEventListener(SESSION_EXPIRED_EVENT, handleSessionExpired);
    return () => {
      window.removeEventListener(SESSION_EXPIRED_EVENT, handleSessionExpired);
    };
  }, []);

  useEffect(() => {
    let active = true;

    apiRequest<Me>(slug, "/auth/me/")
      .then((result) => {
        if (active) {
          setMe(result);
        }
      })
      .catch(() => {
        // A 401 means "not logged in" -- not an error to surface or throw
        // (docs/DECISIONS.md § Stage 15 planning, item 2). Any other
        // failure (network, 5xx) degrades to the same signed-out state
        // rather than crashing the app from the root layout.
        if (active) {
          setMe(null);
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function login(email: string, password: string): Promise<Me> {
    // Errors (401 wrong credentials, 429 throttled, ...) are not caught
    // here -- they propagate to the caller, which owns how to present them
    // (app/login/page.tsx's existing error-handling behavior, unchanged by
    // this refactor).
    await apiRequest(slug, "/auth/login/", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    const result = await apiRequest<Me>(slug, "/auth/me/");
    // `me` first, then the reset: the reverse order could render
    // `me === null` with the flag false.
    setMe(result);
    setLoggedOutDeliberately(false);
    return result;
  }

  async function logout(): Promise<void> {
    // docs/DECISIONS.md § "Session renewal and session lifetime", "S2 design
    // details", point 10 (logout invariant). Delete the hint immediately so
    // no new renewal can start (apiRequest gates renewal on its presence),
    // then wait for any renewal already in flight so a late "renewed"
    // outcome can't re-set the cookies after this function has cleared them
    // -- deleting the hint a second time, unconditionally, undoes exactly
    // that re-set. No SESSION_EXPIRED_EVENT here: a deliberate logout is not
    // a lost session.
    deleteSessionHint();
    await whenRenewalIdle(slug);
    deleteSessionHint();

    // LogoutView clears both auth cookies on both success (205) and on a
    // blacklist failure (400 via InvalidOrExpiredTokenError) -- the server
    // has already signed the client out either way, so local state must
    // follow regardless of whether this call resolves or rejects (e.g. an
    // already-expired refresh token cookie).
    try {
      await apiRequest(slug, "/auth/logout/", { method: "POST" });
    } catch {
      // Ignored -- see comment above.
    }
    consumeReturnPath();
    // Flag first, then `me`: the order, not React's batching, is what keeps
    // client/layout.tsx from ever seeing `me === null` with a stale flag.
    setLoggedOutDeliberately(true);
    setMe(null);
  }

  // Re-fetches `auth/me/` and replaces `me`, without touching `loading` --
  // ClientLayout and the booking wrapper render nothing while `loading` is
  // true, so flipping it here would unmount whatever called this (e.g. right
  // after a successful account booking, on its way to /client).
  const refresh = useCallback(async (): Promise<void> => {
    try {
      const result = await apiRequest<Me>(slug, "/auth/me/");
      setMe(result);
    } catch (err) {
      // A 401 means the session ended -- same "not an error to surface"
      // reasoning as the mount effect above. Any other failure (network,
      // 5xx) leaves `me` as it was rather than guessing; this promise still
      // resolves either way, never rejects, so a caller can always await it
      // and move on.
      if (err instanceof ApiError && err.status === 401) {
        setMe(null);
      }
    }
  }, [slug]);

  return (
    <AuthContext.Provider value={{ me, loading, loggedOutDeliberately, login, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

/** Throws outside an AuthProvider — the root layout wraps every route, so
 * an out-of-tree usage is a real bug, not a case to silently tolerate with
 * a default value (same pattern as useBookingContactInfo()). */
export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return value;
}
