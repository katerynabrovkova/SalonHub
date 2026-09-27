"use client";

/**
 * Client dashboard (docs/DECISIONS.md § Stage 15 planning, item 4, first
 * wave: list + sections + cancel action). Replaces the Stage 12 placeholder.
 * Auth is already enforced by app/client/layout.tsx -- this page assumes it
 * only ever renders for a signed-in Account and does no auth checking of
 * its own.
 *
 * The mockup files this task references (ClientDashboard.dc.html,
 * ClientDashboardEmpty.dc.html, CardStates.dc.html) were not found anywhere
 * in this repository (recon-confirmed) -- this implementation follows the
 * literal badge text / payment-line copy / section rules spelled out in
 * the task instead of a visual mockup, using this app's existing plain
 * Tailwind-utility styling (no design system exists yet, matching
 * login/register/etc.).
 *
 * Pay (docs/DECISIONS.md § Stage 15 planning, item 14 design details): a
 * pending_payment card gets an "Оплатити" button calling
 * `payForAppointment`. Unlike cancel's single page-level `actionError`, pay
 * state (in flight, payment link, awaiting-confirmation flag, error) is kept
 * per card, keyed by appointment id, so an error on one card never shows on
 * another and survives the list refetch. The link and the awaiting text
 * render only while the card is still pending_payment, so a refetch that
 * finds it confirmed (or expired) drops them; the error stays, so a 409's
 * "no longer available" message still explains why the card moved.
 * `provider_data` is provider-controlled, so only an https: URL is ever
 * rendered as a link.
 *
 * Cancel: on success, refetches the full list rather than patching the
 * cancelled item in place -- recon-confirmed there is no existing
 * "patch one item in an array" precedent in this codebase, so a full
 * refetch is the simplest safe approach for this first wave.
 *
 * Avatar + dropdown menu (item 7): kept in the header shared across every
 * render branch below (loading, error, empty, and the populated state), not
 * just the populated one -- profile access shouldn't be gated behind the
 * appointments list finishing its own fetch.
 */

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { apiRequest } from "@/lib/api/client";
import { ApiError, RenewalUnsureError } from "@/lib/api/errors";
import { getMyAppointments, type MyAppointment } from "@/lib/booking/getMyAppointments";
import { payForAppointment } from "@/lib/booking/payForAppointment";
import { resolveSlugFromHost } from "@/lib/routing/resolveSlugFromHost";

import ClientAvatarMenu from "./ClientAvatarMenu";

// Mirrors AuthContext.tsx/login/page.tsx's PLATFORM_DOMAIN resolution.
const PLATFORM_DOMAIN = process.env.NEXT_PUBLIC_PLATFORM_DOMAIN ?? "salonhub.com";

const STATUS_BADGES: Record<string, { text: string; className: string }> = {
  confirmed: {
    text: "Підтверджено",
    className: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
  },
  pending_payment: {
    text: "Очікує оплати",
    className: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
  },
  completed: {
    text: "Завершено",
    className: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  },
  cancelled: {
    text: "Скасовано",
    className: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200",
  },
  expired: {
    text: "Термін минув",
    className: "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400",
  },
};

const CANCELLABLE_STATUSES = new Set(["confirmed", "pending_payment"]);
const UPCOMING_ELIGIBLE_STATUSES = new Set(["confirmed", "pending_payment"]);

function isUpcoming(appointment: MyAppointment, nowMs: number): boolean {
  return (
    UPCOMING_ELIGIBLE_STATUSES.has(appointment.status) &&
    new Date(appointment.start_datetime).getTime() > nowMs
  );
}

/** CardStates.dc.html's status/payment_status -> payment-line mapping. */
function paymentLine(appointment: MyAppointment): string | null {
  if (appointment.status === "completed") {
    return null;
  }
  if (appointment.status === "confirmed" && appointment.payment_status === "succeeded") {
    return `Оплата при візиті: ${appointment.amount_due_at_visit} ₴`;
  }
  if (appointment.status === "cancelled" || appointment.status === "expired") {
    if (appointment.payment_status === "succeeded") {
      return `Депозит утримано: ${appointment.payment_amount} ₴`;
    }
    if (appointment.payment_status === "refund_pending") {
      return "Повернення обробляється";
    }
    if (appointment.payment_status === "refunded") {
      return `Повернено: ${appointment.payment_amount} ₴`;
    }
  }
  return null;
}

const AWAITING_CONFIRMATION_TEXT = "Очікуємо підтвердження оплати";

/** Per-card pay state, keyed by appointment id in the page. */
interface PayState {
  inFlight: boolean;
  /** Validated https: payment link from the pay response, if any. */
  link: string | null;
  /** A pay call succeeded in this session (awaiting the webhook). */
  started: boolean;
  error: string | null;
}

const IDLE_PAY_STATE: PayState = { inFlight: false, link: null, started: false, error: null };

/** `provider_data` if it is an https: URL, else null (never a javascript:, http: or garbage link). */
function httpsLinkOrNull(providerData: string | null): string | null {
  if (!providerData) {
    return null;
  }
  try {
    return new URL(providerData).protocol === "https:" ? providerData : null;
  } catch {
    return null;
  }
}

function payErrorMessage(err: unknown): string {
  if (err instanceof ApiError && err.status === 409) {
    return "Це бронювання більше недоступне для оплати.";
  }
  if (err instanceof ApiError && err.status === 404) {
    return "Бронювання не знайдено.";
  }
  // The request never reached the server (fetch threw), or apiRequest's S2
  // silent renewal couldn't tell whether the session survived: both read as
  // a connectivity problem to the user.
  if (err instanceof TypeError || err instanceof RenewalUnsureError) {
    return "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
  }
  // Reached the server, which (or whose payment provider) failed: 502 and
  // any other error.
  return "Сервіс оплати тимчасово не відповідає. Спробуйте за кілька хвилин.";
}

/**
 * The cancel confirmation text, chosen from the clock at the moment of the
 * click (`nowMs`), never cached at list-load time: `refund_deadline` comes
 * from the backend's own refund rule (booking.services.refund_deadline), and
 * `now <= refund_deadline` mirrors its `_is_refund_eligible` boundary. The
 * backend still makes the final refund decision at cancel time
 * (docs/DECISIONS.md § Stage 15 planning, item 14 design details).
 *
 * "Paid" means a SUCCEEDED Payment. A paid card without a deadline cannot
 * come from the backend today (it only nulls the deadline when nothing is
 * refundable); if it ever does, the dialog makes no refund claim either way.
 */
function cancelConfirmText(appointment: MyAppointment, nowMs: number): string {
  if (appointment.payment_status !== "succeeded" || appointment.refund_deadline === null) {
    return "Скасувати бронювання?";
  }
  return nowMs <= new Date(appointment.refund_deadline).getTime()
    ? "Скасувати бронювання? Передоплату буде повернено."
    : "Скасувати бронювання? До візиту менше 24 годин, тому передоплата не повертається.";
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("uk-UA", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

interface AppointmentCardProps {
  appointment: MyAppointment;
  onCancel: (appointment: MyAppointment) => void;
  cancelling: boolean;
  onPay: (id: number) => void;
  payState: PayState;
}

function AppointmentCard({
  appointment,
  onCancel,
  cancelling,
  onPay,
  payState,
}: AppointmentCardProps) {
  const badge = STATUS_BADGES[appointment.status];
  const line = paymentLine(appointment);
  const cancellable = CANCELLABLE_STATUSES.has(appointment.status);
  const payable = appointment.status === "pending_payment";
  const awaitingConfirmation =
    payable && (payState.started || appointment.payment_status === "pending");
  const payLink = payable ? payState.link : null;

  return (
    <li className="flex flex-col gap-2 rounded border border-zinc-200 p-4 dark:border-zinc-700">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{appointment.service.name}</span>
        {badge !== undefined ? (
          <span className={`rounded px-2 py-0.5 text-xs ${badge.className}`}>{badge.text}</span>
        ) : null}
      </div>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">{appointment.specialist.name}</p>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        {formatDateTime(appointment.start_datetime)}
      </p>
      {line !== null ? <p className="text-sm">{line}</p> : null}
      {awaitingConfirmation ? <p className="text-sm">{AWAITING_CONFIRMATION_TEXT}</p> : null}
      {payLink !== null ? (
        <a href={payLink} className="self-start text-sm text-blue-600 underline">
          Перейти до оплати
        </a>
      ) : null}
      {payState.error !== null ? (
        <p role="alert" className="text-sm text-red-600">
          {payState.error}
        </p>
      ) : null}
      {payable ? (
        <button
          type="button"
          onClick={() => onPay(appointment.id)}
          disabled={payState.inFlight}
          className="self-start text-sm underline disabled:opacity-50"
        >
          Оплатити
        </button>
      ) : null}
      {cancellable ? (
        <button
          type="button"
          onClick={() => onCancel(appointment)}
          disabled={cancelling}
          className="self-start text-sm text-red-600 underline disabled:opacity-50"
        >
          Скасувати
        </button>
      ) : null}
    </li>
  );
}

interface AppointmentSectionProps {
  title: string;
  appointments: MyAppointment[];
  emptyMessage: string;
  onCancel: (appointment: MyAppointment) => void;
  cancellingId: number | null;
  onPay: (id: number) => void;
  payStates: Record<number, PayState>;
}

function AppointmentSection({
  title,
  appointments,
  emptyMessage,
  onCancel,
  cancellingId,
  onPay,
  payStates,
}: AppointmentSectionProps) {
  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-lg font-medium">{title}</h2>
      {appointments.length === 0 ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">{emptyMessage}</p>
      ) : (
        <ul className="flex flex-col gap-4">
          {appointments.map((appointment) => (
            <AppointmentCard
              key={appointment.id}
              appointment={appointment}
              onCancel={onCancel}
              cancelling={cancellingId === appointment.id}
              onPay={onPay}
              payState={payStates[appointment.id] ?? IDLE_PAY_STATE}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

export default function ClientDashboardPage() {
  // `appointments`/`asOfMs` are set together, as one fetch result, never
  // recomputed independently -- Date.now() is impure, so "now" is read once
  // at fetch time (inside loadAppointments below, a side-effect context),
  // not live in the render body on every render.
  const [loaded, setLoaded] = useState<{ appointments: MyAppointment[]; asOfMs: number } | null>(
    null,
  );
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [cancellingId, setCancellingId] = useState<number | null>(null);
  const [payStates, setPayStates] = useState<Record<number, PayState>>({});

  function updatePayState(id: number, patch: Partial<PayState>) {
    setPayStates((prev) => ({ ...prev, [id]: { ...(prev[id] ?? IDLE_PAY_STATE), ...patch } }));
  }

  // Guarded: this is a "use client" component but Next.js still renders it
  // once on the server for the initial HTML, where `window` doesn't exist.
  const slug =
    typeof window !== "undefined"
      ? (resolveSlugFromHost(window.location.host, PLATFORM_DOMAIN) ?? "")
      : "";

  async function loadAppointments() {
    try {
      const result = await getMyAppointments(slug);
      setLoaded({ appointments: result, asOfMs: Date.now() });
      setLoadError(null);
    } catch {
      setLoadError("Не вдалося завантажити ваші записи. Спробуйте ще раз.");
    }
  }

  useEffect(() => {
    void loadAppointments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleCancel(appointment: MyAppointment) {
    // Declining changes nothing: no request, no state touched. Native
    // confirm(), same as the logout confirmation in ClientAvatarMenu.
    if (!window.confirm(cancelConfirmText(appointment, Date.now()))) {
      return;
    }
    const { id } = appointment;
    setActionError(null);
    setCancellingId(id);
    try {
      await apiRequest(slug, `/appointments/${id}/cancel/`, { method: "POST" });
      await loadAppointments();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError("Це бронювання вже не можна скасувати.");
      } else if (err instanceof ApiError && err.status === 404) {
        setActionError("Бронювання не знайдено.");
      } else {
        setActionError("Не вдалося скасувати запис. Спробуйте ще раз.");
      }
    } finally {
      setCancellingId(null);
    }
  }

  async function handlePay(id: number) {
    updatePayState(id, { inFlight: true, error: null });
    try {
      const response = await payForAppointment(slug, id);
      updatePayState(id, {
        inFlight: false,
        started: true,
        link: httpsLinkOrNull(response.provider_data),
      });
      await loadAppointments();
    } catch (err) {
      updatePayState(id, { inFlight: false, error: payErrorMessage(err) });
      if (err instanceof ApiError && err.status === 409) {
        await loadAppointments();
      }
    }
  }

  let body: ReactNode;

  if (loaded === null) {
    body = loadError !== null ? <p role="alert">{loadError}</p> : <p>Завантаження...</p>;
  } else if (loaded.appointments.length === 0) {
    body = (
      <div className="flex flex-col items-center gap-4 text-center">
        <p>У вас поки немає жодного запису.</p>
        <Link href="/services" className="text-blue-600 underline">
          Забронювати візит
        </Link>
      </div>
    );
  } else {
    const { appointments, asOfMs } = loaded;
    const upcoming = appointments.filter((appointment) => isUpcoming(appointment, asOfMs));
    const past = appointments.filter((appointment) => !isUpcoming(appointment, asOfMs));

    body = (
      <>
        {actionError !== null ? <p role="alert">{actionError}</p> : null}

        <AppointmentSection
          title="Найближчі"
          appointments={upcoming}
          emptyMessage="Немає майбутніх записів."
          onCancel={handleCancel}
          cancellingId={cancellingId}
          onPay={handlePay}
          payStates={payStates}
        />

        <AppointmentSection
          title="Минулі"
          appointments={past}
          emptyMessage="Немає минулих записів."
          onCancel={handleCancel}
          cancellingId={cancellingId}
          onPay={handlePay}
          payStates={payStates}
        />
      </>
    );
  }

  return (
    <main className="flex flex-col gap-8 p-8">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Мої записи</h1>
        <ClientAvatarMenu />
      </header>

      {body}
    </main>
  );
}
