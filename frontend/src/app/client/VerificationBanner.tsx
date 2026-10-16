"use client";

/**
 * Reminder to verify the account's email (docs/DECISIONS.md § "Item 13
 * decisions (verification reminder)", points 3-5; mockups on the Stage 15
 * design canvas, row "Item 13"). Rendered by app/client/layout.tsx above
 * every /client page, shown only to a client whose `email_verified` is
 * false, and not dismissible: it goes away once `me` says the email is
 * verified.
 *
 * The resend button calls the public `auth/resend-verification/` endpoint
 * with the account's own email (point 2). That endpoint answers the same
 * 202 whatever happens, and this banner shows the same text for every
 * outcome, so it reveals nothing. Errors map to fixed texts; the server's
 * message is never shown.
 *
 * Verified in another tab (point 5): while the banner is shown, a
 * `visibilitychange` to visible calls `refresh()` from AuthContext, so the
 * banner disappears without a reload. The listener exists only while the
 * banner is shown and is removed on unmount.
 */

import { useEffect, useState } from "react";

import { useAuth } from "@/app/AuthContext";
import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { resolveSlugFromHost } from "@/lib/routing/resolveSlugFromHost";

// Same client-side PLATFORM_DOMAIN resolution as register/page.tsx.
const PLATFORM_DOMAIN = process.env.NEXT_PUBLIC_PLATFORM_DOMAIN ?? "salonhub.com";

const THROTTLED_MESSAGE = "Забагато спроб. Спробуйте пізніше.";
const CONNECTION_MESSAGE = "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
const GENERIC_SEND_MESSAGE = "Не вдалося надіслати. Спробуйте ще раз.";

type Outcome = { kind: "sent"; email: string } | { kind: "error"; text: string } | null;

function resendErrorText(err: unknown): string {
  if (err instanceof ApiError && err.status === 429) {
    return THROTTLED_MESSAGE;
  }
  if (err instanceof TypeError) {
    return CONNECTION_MESSAGE;
  }
  return GENERIC_SEND_MESSAGE;
}

export default function VerificationBanner() {
  const { me, refresh } = useAuth();
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);

  const shown = me !== null && me.role === "client" && !me.email_verified;

  useEffect(() => {
    if (!shown) {
      return;
    }

    function handleVisibilityChange() {
      if (document.visibilityState === "visible") {
        void refresh();
      }
    }

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [shown, refresh]);

  if (me === null || !shown) {
    return null;
  }

  const email = me.email;

  async function handleResend() {
    // Guarded: this is a "use client" component but Next.js still renders it
    // once on the server, where `window` doesn't exist. A click only ever
    // happens in the browser.
    const slug = resolveSlugFromHost(window.location.host, PLATFORM_DOMAIN) ?? "";
    setPending(true);
    setOutcome(null);
    try {
      await apiRequest(slug, "/auth/resend-verification/", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      setOutcome({ kind: "sent", email });
    } catch (err) {
      setOutcome({ kind: "error", text: resendErrorText(err) });
    } finally {
      setPending(false);
    }
  }

  return (
    <section
      aria-label="Підтвердження пошти"
      className="mx-8 mt-8 flex flex-col gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-950 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100"
    >
      <div className="flex items-start gap-3">
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className="mt-0.5 flex-none"
        >
          <rect x="3" y="5" width="18" height="14" rx="2" />
          <path d="M3 7l9 6 9-6" />
        </svg>
        <div className="flex flex-col gap-1">
          <span className="font-semibold">Підтвердіть пошту</span>
          <span className="text-sm">
            Ми надіслали лист на {email}. Після підтвердження тут з&apos;являться ваші записи, і ви
            зможете бронювати з акаунта.
          </span>
        </div>
      </div>

      <button
        type="button"
        onClick={() => void handleResend()}
        disabled={pending}
        className="min-h-11 self-start rounded-lg border border-amber-700 px-4 py-2 text-sm font-semibold disabled:opacity-60 dark:border-amber-400"
      >
        {pending ? "Надсилаємо..." : "Надіслати лист ще раз"}
      </button>

      {outcome?.kind === "sent" ? (
        <p role="status" className="text-sm font-semibold text-green-800 dark:text-green-300">
          Лист надіслано на {outcome.email}.
        </p>
      ) : null}
      {outcome?.kind === "error" ? (
        <p role="alert" className="text-sm text-red-700 dark:text-red-300">
          {outcome.text}
        </p>
      ) : null}
    </section>
  );
}
