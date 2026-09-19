"use client";

/**
 * Email-verification client half (docs/DECISIONS.md § Stage 15 planning,
 * item 6). Reads the token from `window.location.hash` inside a
 * `useEffect` — unavailable during any server render, hence a "use
 * client" component receiving only `slug` as a prop, with the server
 * component above it doing no fragment handling of its own. Mirrors
 * `booking/pay/PaymentStatus.tsx`'s conventions.
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
 * cookie-setting, matching Stage 12's decision that only `LoginView` is),
 * so the success state links to `/login` rather than auto-logging in.
 */
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { apiRequest } from "@/lib/api/client";

interface VerifyEmailStatusProps {
  slug: string;
}

type Phase = "loading" | "success" | "error";

const ERROR_MESSAGE = "Посилання недійсне або застаріле.";

function parseToken(hash: string): string | null {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  if (raw === "") {
    return null;
  }

  const params = new URLSearchParams(raw);
  const token = params.get("token");
  return token !== null && token !== "" ? token : null;
}

export default function VerifyEmailStatus({ slug }: VerifyEmailStatusProps) {
  const [phase, setPhase] = useState<Phase>("loading");
  const firedRef = useRef(false);

  useEffect(() => {
    const token = parseToken(window.location.hash);
    if (token === null) {
      setPhase("error");
      return;
    }

    if (firedRef.current) {
      return;
    }
    firedRef.current = true;

    // No unmount guard around these `setPhase` calls: `firedRef` already
    // guarantees at most one request ever fires, so there's no stale
    // second request whose result needs to be ignored, and React 19 no
    // longer warns (or does anything unsafe) when a state update lands
    // after the component has unmounted.
    void (async () => {
      try {
        await apiRequest<void>(slug, "/auth/verify-email/", {
          method: "POST",
          body: JSON.stringify({ token }),
        });
        setPhase("success");
      } catch {
        setPhase("error");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (phase === "loading") {
    return <p>Підтверджуємо пошту...</p>;
  }

  if (phase === "success") {
    return (
      <div className="flex flex-col gap-4 text-center">
        <p>Пошту підтверджено.</p>
        <Link href="/login" className="text-blue-600 underline">
          Увійти
        </Link>
      </div>
    );
  }

  return <p>{ERROR_MESSAGE}</p>;
}
