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
import { useEffect, useRef, type ReactNode } from "react";

import { useAuth } from "@/app/AuthContext";
import { saveReturnPath } from "@/lib/auth/returnPath";

import VerificationBanner from "./VerificationBanner";

export default function ClientLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { me, loading, loggedOutDeliberately } = useAuth();
  // Whether this mount has seen an account. Tells a logout started while
  // the layout was mounted from arriving here already logged out.
  const hadAccountRef = useRef(false);

  useEffect(() => {
    if (me !== null) {
      hadAccountRef.current = true;
    }
    if (loading || me !== null) {
      return;
    }
    // docs/DECISIONS.md § "Item 9 decisions (change password): ending
    // sessions", "Frontend details, decided 06.10.2026", point 2, refined
    // 15.10.2026. A deliberate logout started while the layout was mounted
    // is navigated by whoever started it (ClientAvatarMenu, the profile
    // page's logout button, the change-password form), so the layout stays
    // out. Arriving with no account after a deliberate logout (Back, a
    // typed URL) goes to plain "/login" and saves nothing.
    if (loggedOutDeliberately) {
      if (hadAccountRef.current) {
        return;
      }
      router.replace("/login");
      return;
    }
    // Only a lost session remembers where the user was and goes to plain
    // "/login" (§ "Session renewal and session lifetime", "S3 design
    // details", item 5).
    saveReturnPath(window.location.pathname + window.location.search);
    router.replace("/login");
  }, [loading, me, loggedOutDeliberately, router]);

  if (loading || me === null) {
    return null;
  }

  return (
    <>
      <VerificationBanner />
      {children}
    </>
  );
}
