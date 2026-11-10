"""
What a customer is told about the money and the refund deadline of their
own appointment: the one implementation behind the account and guest
appointment serializers (booking/serializers.py) and the booking emails
(notifications/services.py), so a value can never differ between the
dashboard, the guest manage page and an email (docs/DECISIONS.md "Booking >
Guest token", "Notifications").

Pure reads, no writes. Every Payment read goes through the OneToOneField
reverse accessor `appointment.payment`, which raises `Payment.DoesNotExist`
when no row exists, so `payment_of()` guards it with `getattr(..., None)`.
`None` means "nothing to show", never a placeholder like 0.

Not in booking.services: notifications.services imports this module, and
booking.services imports notifications.services, so living there would be a
circular import. `refund_deadline_or_none` still calls
`booking.services.refund_deadline`, the function `cancel_appointment`
evaluates, imported at call time for the same reason (and so a monkeypatched
`REFUND_ELIGIBILITY_CUTOFF` is honoured).
"""

import datetime as dt
from decimal import Decimal

from booking.models import ACTIVE_APPOINTMENT_STATUSES, Appointment, AppointmentStatus
from payments.models import Payment, PaymentStatus
from payments.services import compute_deposit_amount

# amount_due_at_visit is only meaningful once a deposit has actually been
# collected against a still-honored booking (docs/DECISIONS.md § Stage 15
# planning, item 4). COMPLETED is included alongside CONFIRMED for
# consistency (a completed visit's balance is exactly as well-defined as a
# confirmed one's).
_AMOUNT_DUE_ELIGIBLE_STATUSES = frozenset(
    {AppointmentStatus.CONFIRMED, AppointmentStatus.COMPLETED}
)


def payment_of(appointment: Appointment) -> Payment | None:
    return getattr(appointment, "payment", None)


def has_succeeded_payment(appointment: Appointment) -> bool:
    payment = payment_of(appointment)
    return payment is not None and payment.status == PaymentStatus.SUCCEEDED


def deposit_amount(appointment: Appointment) -> Decimal | None:
    """`Payment.amount` when a Payment exists, otherwise
    `payments.services.compute_deposit_amount` (the helper `initiate_payment`
    charges with, so the rounding rule exists once); None whenever it is 0."""
    payment = payment_of(appointment)
    amount = payment.amount if payment is not None else compute_deposit_amount(appointment)
    return amount if amount != 0 else None


def amount_due_at_visit(appointment: Appointment) -> Decimal | None:
    """Price minus the collected deposit, for a confirmed or completed
    appointment with a succeeded Payment; None otherwise."""
    payment = payment_of(appointment)
    if (
        appointment.status not in _AMOUNT_DUE_ELIGIBLE_STATUSES
        or payment is None
        or payment.status != PaymentStatus.SUCCEEDED
    ):
        return None
    return appointment.service_price_at_booking - payment.amount


def currency(appointment: Appointment) -> str:
    """`Payment.currency` when a Payment exists (a historic payment keeps the
    currency it was paid in), otherwise the salon's."""
    payment = payment_of(appointment)
    return payment.currency if payment is not None else appointment.salon.currency


def deposit_outcome(appointment: Appointment) -> str | None:
    """What happened to a paid deposit, from the Payment and its persisted
    `refund_due` (never recomputed from the cancellation rules):
    "refunded", "refund_processing", "withheld", or None."""
    payment = payment_of(appointment)
    if payment is None:
        return None
    if payment.status == PaymentStatus.REFUNDED:
        return "refunded"
    if payment.status == PaymentStatus.REFUND_PENDING:
        return "refund_processing"
    if payment.status == PaymentStatus.SUCCEEDED and payment.refund_due:
        return "refund_processing"
    if (
        payment.status == PaymentStatus.SUCCEEDED
        and appointment.status == AppointmentStatus.CANCELLED
    ):
        return "withheld"
    return None


def refund_deadline_or_none(appointment: Appointment) -> dt.datetime | None:
    """
    When a customer cancellation stops being refund-eligible
    (docs/DECISIONS.md § Stage 15 planning, item 14 design details). A
    deadline, not a boolean: the dashboard and the guest manage page compare
    it with the clock at click time. Derived from
    `booking.services.refund_deadline`, the same rule `cancel_appointment`
    evaluates at cancel time, which remains the final decision.

    None whenever a customer cancellation could refund nothing: no SUCCEEDED
    payment, or an appointment that can no longer be cancelled (anything
    outside ACTIVE_APPOINTMENT_STATUSES).
    """
    from booking.services import refund_deadline

    if appointment.status not in ACTIVE_APPOINTMENT_STATUSES or not has_succeeded_payment(
        appointment
    ):
        return None
    return refund_deadline(appointment.start_datetime)


def cancellation_refunds_at(appointment: Appointment, now: dt.datetime) -> bool:
    """Whether a customer cancelling this appointment at `now` would still be
    refunded, by `booking.services.customer_cancellation_refunds`, the
    comparison `cancel_appointment` makes (imported at call time, see the
    module docstring)."""
    from booking.services import customer_cancellation_refunds

    return customer_cancellation_refunds(now=now, start_datetime=appointment.start_datetime)
