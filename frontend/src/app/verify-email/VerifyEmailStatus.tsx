"use client";

/**
 * Email-verification client half (docs/DECISIONS.md § Stage 15 planning,
 * item 6). Reads the token from the URL hash and POSTs it once. Mirrors
 * `booking/pay/PaymentStatus.tsx`'s conventions.
 *
 * The hash is read through `useSyncExternalStore` (null on the server
 * render), so "no token" is derived during render instead of being set from
 * inside an effect, like `confirm-email-change/ConfirmEmailChangeStatus.tsx`.
 * The effect only starts the request, and state is set only after it
 * settles.
 *
 * `VerifyEmailView` collapses every failure mode (expired, tampered,
 * wrong-salon, missing token) into one neutral message — this component
 * does the same rather than trying to distinguish them, and does not add
 * a resend/retry action (out of scope; covered by registration/resend
 * flows elsewhere).
 *
 * `firedRef` guards the POST against a second call from React
 * strict-mode's mount → cleanup → mount double-invoke in development, or
 * any other re-render — exactly one verify request per page load.
 * Success does not set auth cookies (`VerifyEmailView` isn't
 * cookie-setting, matching Stage 12's decision that only `LoginView` is).
 *
 * After a success (docs/DECISIONS.md § "Item 13 decisions (verification
 * reminder)", point 6) the page calls `refresh()`, so a logged-in visitor's
 * verification banner goes away, and the link mirrors the login page's
 * redirect: a client goes to /client, an admin to /admin, both labelled
 * "Продовжити"; nobody logged in goes to /login, labelled "Увійти".
 * `refresh()` never rejects, so a logged-out visitor stays logged out.
 */
import Link from "next/link";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { useAuth } from "@/app/AuthContext";
import { apiRequest } from "@/lib/api/client";
import type { Me } from "@/lib/auth/Me";

interface VerifyEmailStatusProps {
  slug: string;
}

type Outcome = "success" | "error";

const ERROR_MESSAGE = "Посилання недійсне або застаріле.";

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

function parseToken(hash: string): string | null {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  if (raw === "") {
    return null;
  }

  const params = new URLSearchParams(raw);
  const token = params.get("token");
  return token !== null && token !== "" ? token : null;
}

function successLinkFor(me: Me | null): { href: string; label: string } {
  if (me === null) {
    return { href: "/login", label: "Увійти" };
  }
  return { href: me.role === "admin" ? "/admin" : "/client", label: "Продовжити" };
}

export default function VerifyEmailStatus({ slug }: VerifyEmailStatusProps) {
  const { me, refresh } = useAuth();
  const hash = useSyncExternalStore(subscribeToHash, readHash, readServerHash);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const firedRef = useRef(false);

  // undefined: not known yet (server render); null: no usable token.
  const token = hash === null ? undefined : parseToken(hash);

  useEffect(() => {
    if (typeof token !== "string" || firedRef.current) {
      return;
    }
    firedRef.current = true;

    // No unmount guard around `setOutcome`: `firedRef` already guarantees
    // at most one request ever fires, and React 19 no longer warns (or does
    // anything unsafe) when a state update lands after unmount.
    void (async () => {
      let result: Outcome;
      try {
        await apiRequest<void>(slug, "/auth/verify-email/", {
          method: "POST",
          body: JSON.stringify({ token }),
        });
        result = "success";
      } catch {
        result = "error";
      }
      setOutcome(result);
      if (result === "success") {
        void refresh();
      }
    })();
  }, [token, slug, refresh]);

  if (token === null || outcome === "error") {
    return <p>{ERROR_MESSAGE}</p>;
  }

  if (outcome === null) {
    return <p>Підтверджуємо пошту...</p>;
  }

  const link = successLinkFor(me);
  return (
    <div className="flex flex-col gap-4 text-center">
      <p>Пошту підтверджено.</p>
      <Link href={link.href} className="text-blue-600 underline">
        {link.label}
      </Link>
    </div>
  );
}
