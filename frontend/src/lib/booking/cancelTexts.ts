/**
 * The cancel-confirmation texts and deposit-outcome labels shared by the
 * client dashboard (app/client/page.tsx) and the guest manage page
 * (app/appointments/[id]/manage), so the two can never word a cancellation
 * differently (docs/DECISIONS.md "Frontend > Guest manage page").
 *
 * The refund wording is chosen from `refund_deadline` at the moment of the
 * click, never cached at load time: the deadline comes from the backend's own
 * refund rule (booking.services.refund_deadline) and `now <= refund_deadline`
 * mirrors its cancel-time comparison. The backend still makes the final
 * refund decision when the cancel request arrives.
 */
import type { DepositOutcomeValue } from "./getMyAppointments";

export const CANCEL_CONFIRM_PLAIN = "Скасувати бронювання?";
export const CANCEL_CONFIRM_REFUND = "Скасувати бронювання? Передоплату буде повернено.";
export const CANCEL_CONFIRM_NO_REFUND =
  "Скасувати бронювання? До візиту менше 24 годин, тому передоплата не повертається.";

export const DEPOSIT_OUTCOME_LABELS: Record<DepositOutcomeValue, string> = {
  refund_processing: "Повернення обробляється",
  refunded: "Повернено",
  withheld: "Передоплату утримано",
};

/**
 * The confirmation text for a cancellation at `nowMs`. A null deadline means
 * nothing is refundable (the backend nulls it without a succeeded payment),
 * so the dialog makes no refund claim either way.
 */
export function cancelConfirmText(refundDeadline: string | null, nowMs: number): string {
  if (refundDeadline === null) {
    return CANCEL_CONFIRM_PLAIN;
  }
  return nowMs <= new Date(refundDeadline).getTime()
    ? CANCEL_CONFIRM_REFUND
    : CANCEL_CONFIRM_NO_REFUND;
}
