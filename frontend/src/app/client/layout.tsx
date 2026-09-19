"use client";

/**
 * Route guard for the /client subtree (docs/DECISIONS.md § Stage 15
 * planning, item 12). Wraps every /client page — the existing placeholder
 * page.tsx today, and the dashboard/profile sub-pages items 4/7-11 add
 * later — without touching any of their own content.
 *
 * Uses `router.replace`, not `push` (the convention login/page.tsx's
 * post-login navigation uses): this redirect fires because the visitor
 * shouldn't be here, not as a forward step in a flow, so the protected URL
 * must not stay in browser history — a `push` would let the back button
 * bounce the visitor straight back into this same redirect.
 *
 * `loading` and "logged out" are different states: while `loading` is true,
 * `me` briefly reads `null` before the mount fetch to `auth/me/` resolves,
 * so redirecting on that reading here would incorrectly bounce an already
 * signed-in visitor through /login on every load. Render nothing (no
 * children, no redirect) until `loading` settles.
 */

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/app/AuthContext";

export default function ClientLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { me, loading } = useAuth();

  useEffect(() => {
    if (!loading && me === null) {
      router.replace("/login");
    }
  }, [loading, me, router]);

  if (loading || me === null) {
    return null;
  }

  return <>{children}</>;
}
