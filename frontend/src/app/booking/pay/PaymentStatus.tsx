"use client";

/**
 * Booking step 5 client half (docs/DECISIONS.md § Stage 14 step 5:
 * `/booking/pay` architecture). Reads `appointment_id`/`token` from
 * `window.location.hash` inside a `useEffect` — unavailable during any
 * server render, hence a "use client" component receiving only `slug` as a
 * prop, with the server component above it doing no fragment handling of
 * its own.
 *
 * Mirrors ContactInfoForm/LoginPage's conventions: a single
 * `error: string | null`, a `pending: boolean`, submit disabled while
 * pending. A failed detail fetch (bad/expired token) is deliberately not
 * distinguished from a missing/malformed fragment — both render the same
 * "link invalid or expired" message, per the decision.
 *
 * A successful pay call's `provider_data` decides what renders next:
 * `null` (MockPaymentProvider, and any provider with nothing further for
 * the guest to do) keeps the existing static "pending confirmation"
 * message, since payment status stays `pending` until a webhook confirms
 * it. A non-empty string (e.g. WayForPayProvider's invoiceUrl) is a link
 * the guest must follow to actually pay — rendered as a link rather than
 * an automatic `window.location` redirect, consistent with this file's
 * existing pattern of an explicit user action (the "Оплатити" button)
 * rather than anything auto-navigating on its own.
 */
import { useEffect, useState } from "react";

import { guestApiRequest } from "@/lib/api/guestClient";

interface PaymentStatusProps {
  slug: string;
}

interface AppointmentDetail {
  id: number;
  status: string;
}

type Phase =
  | { kind: "loading" }
  | { kind: "invalid" }
  | { kind: "paid"; providerData: string | null; amount: string; currency: string }
  | { kind: "status"; appointmentId: number; token: string; status: string };

interface PayResponse {
  payment: { amount: string; currency: string };
  provider_data: string | null;
}

const INVALID_LINK_MESSAGE = "Посилання недійсне або застаріле.";

function parseFragment(hash: string): { appointmentId: number; token: string } | null {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  if (raw === "") {
    return null;
  }

  const params = new URLSearchParams(raw);
  const appointmentIdRaw = params.get("appointment_id");
  const token = params.get("token");
  if (appointmentIdRaw === null || token === null || token === "") {
    return null;
  }

  const appointmentId = Number(appointmentIdRaw);
  if (!Number.isInteger(appointmentId)) {
    return null;
  }

  return { appointmentId, token };
}

export default function PaymentStatus({ slug }: PaymentStatusProps) {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    const parsed = parseFragment(window.location.hash);
    if (parsed === null) {
      setPhase({ kind: "invalid" });
      return;
    }

    let cancelled = false;

    void (async () => {
      try {
        const appointment = await guestApiRequest<AppointmentDetail>(
          slug,
          `/guest/appointments/${parsed.appointmentId}/`,
          parsed.token,
        );
        if (cancelled) {
          return;
        }
        setPhase({
          kind: "status",
          appointmentId: parsed.appointmentId,
          token: parsed.token,
          status: appointment.status,
        });
      } catch {
        if (cancelled) {
          return;
        }
        setPhase({ kind: "invalid" });
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handlePay() {
    if (phase.kind !== "status") {
      return;
    }
    const { appointmentId, token } = phase;

    setError(null);
    setPending(true);
    try {
      const response = await guestApiRequest<PayResponse>(
        slug,
        `/guest/appointments/${appointmentId}/pay/`,
        token,
        { method: "POST" },
      );
      setPhase({
        kind: "paid",
        providerData: response.provider_data,
        amount: response.payment.amount,
        currency: response.payment.currency,
      });
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setPending(false);
    }
  }

  if (phase.kind === "loading") {
    return <p>Завантаження...</p>;
  }

  if (phase.kind === "invalid") {
    return <p>{INVALID_LINK_MESSAGE}</p>;
  }

  if (phase.kind === "paid") {
    if (phase.providerData !== null && phase.providerData !== "") {
      return (
        <a href={phase.providerData}>
          Перейти до оплати ({phase.amount} {phase.currency})
        </a>
      );
    }
    return (
      <p>
        Очікуємо підтвердження оплати. Сума до сплати: {phase.amount} {phase.currency}.
      </p>
    );
  }

  if (phase.status === "pending_payment") {
    return (
      <div className="flex flex-col gap-4">
        {error !== null ? <p role="alert">{error}</p> : null}
        <button type="button" onClick={handlePay} disabled={pending}>
          Оплатити
        </button>
      </div>
    );
  }

  if (phase.status === "confirmed") {
    return <p>Вже оплачено.</p>;
  }

  return <p>Це бронювання більше недоступне.</p>;
}
