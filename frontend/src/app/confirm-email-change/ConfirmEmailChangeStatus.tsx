"use client";

/**
 * Email-change confirmation client half (docs/DECISIONS.md § "Item 8
 * decisions (change email)", "Frontend" and "Frontend details, decided
 * 02.10.2026"). Reads the token from `#token` like
 * verify-email/VerifyEmailStatus.tsx and POSTs it once.
 *
 * The hash is read through `useSyncExternalStore` (null on the server
 * render), so "no token" is derived during render instead of being set
 * from inside an effect. The effect only starts the request, and state is
 * set only after it settles.
 *
 * `firedRef` keeps it to one request per page load, including React
 * strict-mode's mount -> cleanup -> mount in development. `refresh()` runs
 * only after a 204, so a logged-in user's profile shows the new address; a
 * logged-out user stays logged out, since `refresh()` never rejects.
 *
 * Errors map by code to fixed texts, never the server's message.
 */
import Link from "next/link";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { useAuth } from "@/app/AuthContext";
import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";

interface ConfirmEmailChangeStatusProps {
  slug: string;
}

type Outcome = "success" | "unavailable" | "invalid" | "connection" | "unexpected";

const INVALID_LINK_MESSAGE = "Посилання недійсне або застаріле.";

const ERROR_MESSAGES: Record<Exclude<Outcome, "success">, string> = {
  unavailable: "Цю адресу не можна використати.",
  invalid: INVALID_LINK_MESSAGE,
  connection: "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.",
  unexpected: "Не вдалося підтвердити адресу. Оновіть сторінку, щоб спробувати ще раз.",
};

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

  const token = new URLSearchParams(raw).get("token");
  return token !== null && token !== "" ? token : null;
}

function outcomeFor(err: unknown): Exclude<Outcome, "success"> {
  if (err instanceof TypeError) {
    return "connection";
  }
  if (err instanceof ApiError) {
    if (err.code === "email_unavailable") {
      return "unavailable";
    }
    if (err.code === "invalid_or_expired_token") {
      return "invalid";
    }
  }
  return "unexpected";
}

export default function ConfirmEmailChangeStatus({ slug }: ConfirmEmailChangeStatusProps) {
  const { refresh } = useAuth();
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

    void (async () => {
      let result: Outcome;
      try {
        await apiRequest<void>(slug, "/auth/email-change/confirm/", {
          method: "POST",
          body: JSON.stringify({ token }),
        });
        result = "success";
      } catch (err) {
        result = outcomeFor(err);
      }
      setOutcome(result);
      if (result === "success") {
        void refresh();
      }
    })();
  }, [token, slug, refresh]);

  if (token === null) {
    return <p>{INVALID_LINK_MESSAGE}</p>;
  }

  if (outcome === null) {
    return <p>Підтверджуємо нову адресу...</p>;
  }

  if (outcome === "success") {
    return (
      <div className="flex flex-col gap-4 text-center">
        <p>Адресу пошти змінено.</p>
        <p>Для входу використовуйте нову адресу.</p>
        <Link href="/client/profile" className="text-blue-600 underline">
          Перейти до профілю
        </Link>
      </div>
    );
  }

  return <p>{ERROR_MESSAGES[outcome]}</p>;
}
