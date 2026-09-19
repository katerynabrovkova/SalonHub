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
 * No "Оплатити" button anywhere here -- that's Stage 15 item 14, a
 * separate follow-up (AccountAppointmentPayView doesn't exist yet).
 *
 * Cancel: on success, refetches the full list rather than patching the
 * cancelled item in place -- recon-confirmed there is no existing
 * "patch one item in an array" precedent in this codebase, so a full
 * refetch is the simplest safe approach for this first wave.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { getMyAppointments, type MyAppointment } from "@/lib/booking/getMyAppointments";
import { resolveSlugFromHost } from "@/lib/routing/resolveSlugFromHost";

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

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("uk-UA", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

interface AppointmentCardProps {
  appointment: MyAppointment;
  onCancel: (id: number) => void;
  cancelling: boolean;
}

function AppointmentCard({ appointment, onCancel, cancelling }: AppointmentCardProps) {
  const badge = STATUS_BADGES[appointment.status];
  const line = paymentLine(appointment);
  const cancellable = CANCELLABLE_STATUSES.has(appointment.status);

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
      {cancellable ? (
        <button
          type="button"
          onClick={() => onCancel(appointment.id)}
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
  onCancel: (id: number) => void;
  cancellingId: number | null;
}

function AppointmentSection({
  title,
  appointments,
  emptyMessage,
  onCancel,
  cancellingId,
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

  async function handleCancel(id: number) {
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

  if (loaded === null) {
    return (
      <main className="p-8">
        {loadError !== null ? <p role="alert">{loadError}</p> : <p>Завантаження...</p>}
      </main>
    );
  }

  const { appointments, asOfMs } = loaded;

  if (appointments.length === 0) {
    return (
      <main className="flex flex-col items-center gap-4 p-8 text-center">
        <h1 className="text-xl font-semibold">Мої записи</h1>
        <p>У вас поки немає жодного запису.</p>
        <Link href="/services" className="text-blue-600 underline">
          Забронювати візит
        </Link>
      </main>
    );
  }

  const upcoming = appointments.filter((appointment) => isUpcoming(appointment, asOfMs));
  const past = appointments.filter((appointment) => !isUpcoming(appointment, asOfMs));

  return (
    <main className="flex flex-col gap-8 p-8">
      <h1 className="text-xl font-semibold">Мої записи</h1>

      {actionError !== null ? <p role="alert">{actionError}</p> : null}

      <AppointmentSection
        title="Найближчі"
        appointments={upcoming}
        emptyMessage="Немає майбутніх записів."
        onCancel={handleCancel}
        cancellingId={cancellingId}
      />

      <AppointmentSection
        title="Минулі"
        appointments={past}
        emptyMessage="Немає минулих записів."
        onCancel={handleCancel}
        cancellingId={cancellingId}
      />
    </main>
  );
}
