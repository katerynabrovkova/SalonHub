"use client";

/**
 * Guest manage page, client half (docs/DECISIONS.md "Frontend > Guest manage
 * page", "Booking > Guest token").
 *
 * Token: the page's inline script (lib/security/fragmentTokenScript.ts) has
 * already moved it from `#token=...` into sessionStorage under
 * `guest-manage-token:<id>` and removed the hash before hydration; this
 * component never removes the hash itself. The hash is read through
 * `useSyncExternalStore` (null on the server render), like
 * booking/pay/PaymentStatus.tsx: a token still there means the script could
 * not store it (blocked storage), and it is read on every render. Otherwise
 * the stored token for this appointment id only is used. The storage read is
 * wrapped in try/catch: storage can be blocked.
 *
 * Data: every value shown comes from the guest detail/cancel response; the
 * duration is end minus start, times are read from the salon-offset strings.
 * The cancel confirmation and the deposit-outcome labels are the client
 * dashboard's own (lib/booking/cancelTexts.ts), the wording chosen from
 * `refund_deadline` at the moment of the click.
 *
 * Cancel errors: a 409 (the booking changed meanwhile, e.g. the salon
 * cancelled it) or a 400 (the token's cancel capability is spent or expired)
 * reloads the detail, which shows the real state or the invalid-link message;
 * anything else (network, 5xx) shows an error and keeps the button.
 */
import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";

import { ApiError } from "@/lib/api/errors";
import { guestApiRequest } from "@/lib/api/guestClient";
import { cancelConfirmText, DEPOSIT_OUTCOME_LABELS } from "@/lib/booking/cancelTexts";
import type { DepositOutcomeValue } from "@/lib/booking/getMyAppointments";
import { INVALID_GUEST_LINK_MESSAGE } from "@/lib/booking/guestLinkTexts";
import { formatAppointmentDuration } from "@/lib/datetime/formatDuration";
import { formatSalonDateTime } from "@/lib/datetime/formatSalonDateTime";
import { formatPrice } from "@/lib/pricing/formatPrice";
import { guestManageTokenKey } from "@/lib/security/fragmentTokenScript";

interface ManageAppointmentProps {
  slug: string;
  appointmentId: number;
}

interface GuestAppointment {
  id: number;
  status: string;
  start_datetime: string;
  end_datetime: string;
  specialist: { id: number; name: string };
  service: { id: number; name: string };
  service_price_at_booking: string;
  deposit_amount: string | null;
  amount_due_at_visit: string | null;
  currency: string;
  deposit_outcome: DepositOutcomeValue | null;
  refund_deadline: string | null;
}

type Phase =
  | { kind: "loading" }
  | { kind: "invalid" }
  | { kind: "loaded"; appointment: GuestAppointment };

const CANCELLABLE_STATUSES = new Set(["confirmed", "pending_payment"]);
const PAST_STATUSES = new Set(["no_show", "expired"]);
const CANCEL_ERROR_MESSAGE = "Не вдалося скасувати запис. Перевірте інтернет і спробуйте ще раз.";

function readStoredToken(appointmentId: number): string | null {
  try {
    const value = window.sessionStorage.getItem(guestManageTokenKey(appointmentId));
    return value === null || value === "" ? null : value;
  } catch {
    return null;
  }
}

function tokenFromHash(hash: string): string | null {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  const token = new URLSearchParams(raw).get("token");
  return token === null || token === "" ? null : token;
}

function subscribeNever(): () => void {
  return () => {};
}

function readHash(): string | null {
  return window.location.hash;
}

function readServerHash(): string | null {
  return null;
}

function InvalidLink() {
  return (
    <div className="flex flex-col gap-4">
      <p>{INVALID_GUEST_LINK_MESSAGE}</p>
      <Link href="/" className="self-start text-blue-600 underline">
        На головну
      </Link>
    </div>
  );
}

export default function ManageAppointment({ slug, appointmentId }: ManageAppointmentProps) {
  const hash = useSyncExternalStore(subscribeNever, readHash, readServerHash);

  if (hash === null) {
    return <p>Завантаження...</p>;
  }
  if (!Number.isInteger(appointmentId)) {
    return <InvalidLink />;
  }

  const token = tokenFromHash(hash) ?? readStoredToken(appointmentId);
  if (token === null) {
    return <InvalidLink />;
  }

  return <ManageFlow slug={slug} appointmentId={appointmentId} token={token} />;
}

interface ManageFlowProps {
  slug: string;
  appointmentId: number;
  token: string;
}

function ManageFlow({ slug, appointmentId, token }: ManageFlowProps) {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  function loadDetail(): Promise<void> {
    return guestApiRequest<GuestAppointment>(
      slug,
      `/guest/appointments/${appointmentId}/`,
      token,
    ).then(
      (appointment) => setPhase({ kind: "loaded", appointment }),
      () => setPhase({ kind: "invalid" }),
    );
  }

  useEffect(() => {
    void loadDetail();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleCancel(appointment: GuestAppointment) {
    if (!window.confirm(cancelConfirmText(appointment.refund_deadline, Date.now()))) {
      return;
    }
    setError(null);
    setPending(true);
    try {
      const updated = await guestApiRequest<GuestAppointment>(
        slug,
        `/guest/appointments/${appointmentId}/cancel/`,
        token,
        { method: "POST" },
      );
      setPhase({ kind: "loaded", appointment: updated });
    } catch (err) {
      if (err instanceof ApiError && (err.status === 409 || err.status === 400)) {
        await loadDetail();
      } else {
        setError(CANCEL_ERROR_MESSAGE);
      }
    } finally {
      setPending(false);
    }
  }

  if (phase.kind === "loading") {
    return <p>Завантаження...</p>;
  }
  if (phase.kind === "invalid") {
    return <InvalidLink />;
  }

  const { appointment } = phase;
  const format = (amount: string) => formatPrice(amount, appointment.currency);
  const outcomeLine =
    appointment.deposit_outcome !== null && appointment.deposit_amount !== null
      ? `${DEPOSIT_OUTCOME_LABELS[appointment.deposit_outcome]}: ${format(appointment.deposit_amount)}`
      : null;

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">Ваш запис</h1>

      <div className="flex flex-col gap-1">
        <p>Послуга: {appointment.service.name}</p>
        <p>Майстер: {appointment.specialist.name}</p>
        <p>Коли: {formatSalonDateTime(appointment.start_datetime)}</p>
        <p>
          Тривалість:{" "}
          {formatAppointmentDuration(appointment.start_datetime, appointment.end_datetime)}
        </p>
      </div>

      <div className="flex flex-col gap-1">
        <p>Вартість: {format(appointment.service_price_at_booking)}</p>
        {appointment.deposit_amount !== null ? (
          <p>Передоплата: {format(appointment.deposit_amount)}</p>
        ) : null}
        {appointment.amount_due_at_visit !== null ? (
          <p>Оплата при візиті: {format(appointment.amount_due_at_visit)}</p>
        ) : null}
      </div>

      {appointment.status === "cancelled" ? (
        <div className="flex flex-col gap-1">
          <p>Запис скасовано.</p>
          {outcomeLine !== null ? <p>{outcomeLine}</p> : null}
        </div>
      ) : null}
      {appointment.status === "completed" ? <p>Дякуємо за візит</p> : null}
      {PAST_STATUSES.has(appointment.status) ? <p>Цей запис уже минув.</p> : null}

      {error !== null ? (
        <p role="alert" className="text-red-600">
          {error}
        </p>
      ) : null}

      {CANCELLABLE_STATUSES.has(appointment.status) ? (
        <button
          type="button"
          onClick={() => void handleCancel(appointment)}
          disabled={pending}
          className="self-start text-red-600 underline disabled:opacity-50"
        >
          Скасувати запис
        </button>
      ) : null}
    </div>
  );
}
