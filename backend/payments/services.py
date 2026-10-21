"""
Stage 8.C — payment-initiation core (docs/ARCHITECTURE.md § 8;
docs/DECISIONS.md § Stage 8 decisions, § Stage 8.C decisions).

Requires tenant context to already be bound (core.tenancy.tenant_context) —
this function does not bind it itself, same convention as
booking/services.py's create_appointment/cancel_appointment.

`reference` passed to `provider.start_payment()` is `str(appointment.id)`:
on a fresh attempt no `Payment` row exists yet at call time (§ Stage 8.C's
"write the row only after a successful provider response"), so a
not-yet-existing pk can't be used. `Payment.appointment_id` is unique
(one-to-one), so `appointment.id` is an equally valid webhook-correlation
key. See `payments/providers/base.py`'s `start_payment` docstring.
"""

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from booking.models import Appointment, AppointmentStatus
from core.exceptions import InvalidStateTransitionError, PaymentProviderError
from payments.constants import STUCK_REFUND_THRESHOLD
from payments.models import Payment, PaymentStatus
from payments.providers.base import PaymentProvider
from tenants.models import Salon

logger = logging.getLogger(__name__)


def _round_half_up_to_whole(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class DepositBreakdown:
    deposit: Decimal
    due_at_visit: Decimal


def compute_deposit_breakdown(*, price: Decimal, percentage: Decimal) -> DepositBreakdown:
    """
    The deposit for `price` at `percentage`, and what is left to pay at the
    visit, without an existing Appointment (docs/DECISIONS.md § Stage 16
    planning, "Sub-step 4 details", 4a). The AI assistant's only source of
    these amounts.
    """
    deposit = _round_half_up_to_whole(price * percentage / Decimal("100"))
    return DepositBreakdown(
        deposit=deposit, due_at_visit=(price - deposit).quantize(Decimal("0.01"))
    )


def compute_deposit_amount(appointment: Appointment) -> Decimal:
    return compute_deposit_breakdown(
        price=appointment.service_price_at_booking,
        percentage=appointment.deposit_percentage_at_booking,
    ).deposit


def initiate_payment(
    *,
    appointment_id: int,
    salon: Salon,
    provider: PaymentProvider,
    now: dt.datetime,
) -> tuple[Payment, object | None, bool]:
    """
    docs/DECISIONS.md § Stage 8.C decisions. The select_for_update() fetch,
    the PENDING_PAYMENT recheck, and the existing-Payment check all happen
    inside one short atomic() block — locked, decided, released — before the
    network call to the provider, mirroring the booking/payment-separation
    rule (DB transactions stay short; network calls live outside them).

    Idempotency: an existing PENDING Payment is returned as-is, with its
    already-stored provider_data (whatever the original start_payment call
    got back — None for MockPaymentProvider), and the provider is not
    called again. An existing FAILED Payment is retried in place — same row
    (same pk), transitioned FAILED -> PENDING with a new
    provider_reference_id and provider_data overwritten from the new
    intent — never a second row, since Payment.appointment is a
    OneToOneField.

    The third return element, `created`, is a domain fact for the caller to
    map to an HTTP status (docs/DECISIONS.md § Stage 8.D decisions) — this
    function has no notion of HTTP status codes itself. False on the
    idempotency (existing-PENDING) branch; True otherwise, including the
    FAILED -> PENDING retry-in-place case.
    """
    with transaction.atomic():
        appointment = Appointment.objects.select_for_update().get(salon=salon, pk=appointment_id)
        if appointment.status != AppointmentStatus.PENDING_PAYMENT:
            raise InvalidStateTransitionError(details={"current_status": appointment.status})

        existing_payment = Payment.objects.filter(appointment=appointment).first()
        if existing_payment is not None and existing_payment.status == PaymentStatus.PENDING:
            return existing_payment, existing_payment.provider_data, False

    deposit_amount = compute_deposit_amount(appointment)
    try:
        intent = provider.start_payment(
            amount=deposit_amount,
            currency=salon.currency,
            reference=str(appointment.id),
        )
    except Exception as exc:
        raise PaymentProviderError() from exc

    if existing_payment is not None:
        existing_payment.status = PaymentStatus.PENDING
        existing_payment.provider_reference_id = intent.provider_reference_id
        existing_payment.provider_data = intent.provider_data
        existing_payment.save(update_fields=["status", "provider_reference_id", "provider_data"])
        payment = existing_payment
    else:
        payment = Payment.objects.create(
            salon=salon,
            appointment=appointment,
            amount=deposit_amount,
            currency=salon.currency,
            status=PaymentStatus.PENDING,
            provider_reference_id=intent.provider_reference_id,
            provider_data=intent.provider_data,
        )

    return payment, intent.provider_data, True


def initiate_refund(
    *, payment_id: int, salon: Salon, provider: PaymentProvider, now: dt.datetime
) -> Payment:
    """
    docs/DECISIONS.md § Stage 8.F decisions. Mirror-opposite order from
    `initiate_payment`: the status is written to `REFUND_PENDING` and
    committed *before* the network call, not after, because here the
    `Payment` row already exists — there is no "row doesn't exist yet"
    constraint forcing the provider call first. Writing first makes the
    failure mode reversible: if `provider.refund()` fails, the row is left
    `REFUND_PENDING` for the Stage 8.G sweep to flag, rather than risking a
    double refund from a retry after a crash between a successful provider
    call and the DB write.

    Status gate is an allowlist, not a reject-list: only `SUCCEEDED`
    proceeds. `REFUND_PENDING`/`REFUNDED` are idempotent no-ops (refund
    already in flight or already done). Every other status — including
    `PROCESSING`, currently never assigned anywhere — is a 409, since none of
    them ever received money to refund.

    `now` is stamped onto `refund_initiated_at` once, only at the real
    `SUCCEEDED` -> `REFUND_PENDING` transition below — frozen thereafter.
    The idempotent no-op paths above (`REFUND_PENDING`/`REFUNDED`) return
    before touching it, deliberately: re-stamping on a duplicate call would
    move the Stage 8.G sweep's clock every time a retry lands, defeating the
    field's purpose (docs/DECISIONS.md § Stage 8.G decisions).
    """
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(salon=salon, pk=payment_id)
        if payment.status in (PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED):
            return payment
        if payment.status != PaymentStatus.SUCCEEDED:
            raise InvalidStateTransitionError(details={"current_status": payment.status})

        payment.status = PaymentStatus.REFUND_PENDING
        payment.refund_initiated_at = now
        payment.save(update_fields=["status", "refund_initiated_at"])

    try:
        provider.refund(
            provider_reference_id=payment.provider_reference_id,
            reference=str(payment.appointment_id),
            amount=payment.amount,
            currency=payment.currency,
        )
    except Exception as exc:
        raise PaymentProviderError() from exc

    return payment


def flag_stuck_refunds(*, salon: Salon, now: dt.datetime) -> int:
    """
    docs/DECISIONS.md § Stage 8.G decisions. Requires tenant context to
    already be bound (core.tenancy.tenant_context) — this function does not
    bind it itself, same convention as expire_overdue_appointments. Called
    once per salon by payments.tasks.flag_stuck_refund_payments, which owns
    the cross-salon loop and the tenant-context binding.

    Candidate ids are found with an unlocked query, then each row is locked
    and rechecked independently under its own transaction.atomic() — not one
    lock over the whole batch — so a single row can't hold up or roll back
    every other row in the same sweep. A row whose recheck finds it's no
    longer REFUND_PENDING (a refund_succeeded webhook won the race between
    the unlocked query and the lock) or is already flagged (an overlapping
    sweep run) is silently skipped, not raised: same unattended-background-
    cleanup reasoning as expire_overdue_appointments, and the skip is also
    what makes a redelivered/overlapping run idempotent.

    refund_initiated_at is NULL for a payment that never entered a refund,
    or for a REFUND_PENDING row predating the Stage 8.G field migration —
    __lte on NULL is never true in SQL, so those rows are never swept
    (correct: there is no recorded stuck-since time to measure).
    """
    candidate_ids = Payment.objects.filter(
        salon=salon,
        status=PaymentStatus.REFUND_PENDING,
        refund_initiated_at__lte=now - STUCK_REFUND_THRESHOLD,
        flagged_for_review=False,
    ).values_list("id", flat=True)

    flagged_count = 0
    for payment_id in candidate_ids:
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(salon=salon, pk=payment_id)
            if payment.status != PaymentStatus.REFUND_PENDING:
                continue
            if payment.flagged_for_review:
                continue
            payment.flagged_for_review = True
            payment.save(update_fields=["flagged_for_review"])
            flagged_count += 1
    return flagged_count


def retry_owed_refunds(*, salon: Salon, provider: PaymentProvider, now: dt.datetime) -> int:
    """
    docs/DECISIONS.md § "Refund retry sweep". Requires tenant context to
    already be bound (core.tenancy.tenant_context) — this function does not
    bind it itself, same convention as flag_stuck_refunds. Called once per
    salon by payments.tasks.retry_owed_refund_payments, which owns the
    cross-salon loop, the tenant-context binding and the provider.

    Candidate ids are found with an unlocked query; each row then goes
    through initiate_refund, which locks it, rechecks it is still SUCCEEDED
    and commits REFUND_PENDING in its own transaction before calling the
    provider. A row already refunded by the cancel or webhook after-commit
    call in the meantime is a no-op there, so no grace period is needed.

    PaymentProviderError is caught per row, logged and skipped: the row is
    already REFUND_PENDING, so flag_stuck_refunds covers it, and a provider
    outage must not block the other rows. Any other exception propagates to
    the task's per-salon try/except, as in the other sweeps — it is a bug
    and must not be swallowed.

    Returns the number of rows for which initiate_refund completed.
    """
    candidate_ids = Payment.objects.filter(
        salon=salon, status=PaymentStatus.SUCCEEDED, refund_due=True
    ).values_list("id", flat=True)

    refunded_count = 0
    for payment_id in candidate_ids:
        try:
            initiate_refund(payment_id=payment_id, salon=salon, provider=provider, now=now)
        except PaymentProviderError:
            logger.warning(
                "retry_owed_refunds: provider refund failed for payment_id=%s salon_id=%s",
                payment_id,
                salon.id,
                exc_info=True,
            )
            continue
        refunded_count += 1
    return refunded_count
