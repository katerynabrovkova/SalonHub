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
 *
 * No `logout()` yet — no page needs it today (YAGNI, matching `/me/`'s own
 * deferred-verification precedent, docs/DECISIONS.md § "`/me/` endpoint
 * (Stage 12)").
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { apiRequest } from "@/lib/api/client";
import type { Me } from "@/lib/auth/Me";
import { resolveSlugFromHost } from "@/lib/routing/resolveSlugFromHost";

// Mirrors middleware.ts's PLATFORM_DOMAIN resolution, but client-side only
// `NEXT_PUBLIC_*` env vars are ever inlined into the browser bundle
// (docs/DECISIONS.md § "Frontend routing: subdomain-based") — same pattern
// app/login/page.tsx used before this refactor.
const PLATFORM_DOMAIN = process.env.NEXT_PUBLIC_PLATFORM_DOMAIN ?? "salonhub.com";

interface AuthContextValue {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<Me>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  // Guarded: this is a "use client" component but Next.js still renders it
  // once on the server for the initial HTML, where `window` doesn't exist.
  const slug =
    typeof window !== "undefined"
      ? (resolveSlugFromHost(window.location.host, PLATFORM_DOMAIN) ?? "")
      : "";

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
    setMe(result);
    return result;
  }

  return <AuthContext.Provider value={{ me, loading, login }}>{children}</AuthContext.Provider>;
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
