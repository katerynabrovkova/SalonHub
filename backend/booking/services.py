"""
Stage 7.C — appointment-creation core (docs/ARCHITECTURE.md § 2, § 7, § 14,
the State machines section; docs/DECISIONS.md § Stage 7.C decisions).
Scoped to `create_appointment` only — Customer get-or-create and
`GuestAccessToken` issuance are 7.C-bis, the `POST` endpoint is 7.D.

Requires tenant context to already be bound (core.tenancy.tenant_context) —
this function does not bind it itself, same convention as
scheduling/services.py and specialists/services.py.

`compute_candidate_start_times` is imported here as a bare module-global
name, and `_has_overlapping_active_appointment` is defined as one, on
purpose: both are pinned by the double-booking test suite's
`monkeypatch.setattr(booking.services, "<name>", ...)` calls
(docs/DECISIONS.md § Stage 7.C decisions, the "two implementation-detail
names" entry). Do not rename either, or replace the bare import with a
namespaced module reference, without updating
tests/test_booking_create_appointment.py in the same change.
"""

import datetime as dt
import logging
import random
from typing import Literal
from zoneinfo import ZoneInfo

import psycopg
from django.db import IntegrityError, transaction
from django.db.models import Count

from accounts.models import Customer
from accounts.services import get_or_create_guest_customer
from booking.constants import SLOT_HOLD_DURATION
from booking.guest_tokens import issue_guest_token
from booking.models import ACTIVE_APPOINTMENT_STATUSES, Appointment, AppointmentStatus, CancelledBy
from catalog.models import Service
from core.exceptions import (
    InvalidStateTransitionError,
    PaymentProviderError,
    SlotNotOfferedError,
    SlotUnavailableError,
)
from notifications.models import NotificationTrigger
from notifications.services import record_and_dispatch_notification
from payments.models import Payment, PaymentStatus
from payments.providers.base import PaymentProvider
from payments.services import initiate_refund
from scheduling.services import compute_candidate_start_times, compute_multi_specialist_availability
from specialists.models import Specialist
from tenants.models import Salon

logger = logging.getLogger(__name__)


def _has_overlapping_active_appointment(
    *, specialist: Specialist, start_datetime: dt.datetime, blocked_until: dt.datetime
) -> bool:
    """
    Narrow re-check mirroring the exclusion constraint's own condition
    (booking/models.py's `appointment_no_overlapping_active_bookings`) — not
    a re-run of the scheduling engine (docs/DECISIONS.md § Stage 7.C
    decisions).
    """
    return Appointment.objects.filter(
        specialist=specialist,
        status__in=ACTIVE_APPOINTMENT_STATUSES,
        start_datetime__lt=blocked_until,
        blocked_until__gt=start_datetime,
    ).exists()


def create_appointment(
    *,
    salon: Salon,
    specialist: Specialist,
    service: Service,
    customer: Customer,
    start_datetime: dt.datetime,
    now: dt.datetime,
) -> Appointment:
    """
    docs/DECISIONS.md § Stage 7.C decisions. Raises SlotNotOfferedError if
    `start_datetime` isn't among the engine's own candidates for this
    specialist/service/date, or SlotUnavailableError if it was offered but
    got taken between that check and the insert (application-level re-check,
    then the exclusion constraint as the final guard).
    """
    local_date = start_datetime.astimezone(ZoneInfo(salon.timezone)).date()
    candidates = compute_candidate_start_times(
        specialist=specialist,
        service=service,
        salon=salon,
        date_from=local_date,
        date_to=local_date,
        now=now,
    )
    if start_datetime not in candidates:
        logger.warning(
            "create_appointment: slot not offered "
            "(specialist_id=%s, start_datetime=%s, candidate_count=%d)",
            specialist.id,
            start_datetime.isoformat(),
            len(candidates),
        )
        raise SlotNotOfferedError()

    end_datetime = start_datetime + dt.timedelta(minutes=service.duration_minutes)
    blocked_until = end_datetime + dt.timedelta(minutes=service.buffer_minutes)

    with transaction.atomic():
        if _has_overlapping_active_appointment(
            specialist=specialist, start_datetime=start_datetime, blocked_until=blocked_until
        ):
            raise SlotUnavailableError()
        try:
            return Appointment.objects.create(
                salon=salon,
                customer=customer,
                specialist=specialist,
                service=service,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                blocked_until=blocked_until,
                service_price_at_booking=service.price,
                deposit_percentage_at_booking=salon.deposit_percentage,
                hold_expires_at=now + SLOT_HOLD_DURATION,
                status=AppointmentStatus.PENDING_PAYMENT,
            )
        except IntegrityError as exc:
            if isinstance(exc.__cause__, psycopg.errors.ExclusionViolation):
                raise SlotUnavailableError() from exc
            raise


def select_specialist_for_any(
    candidates: list[Specialist],
    salon: Salon,
    date: dt.date,
    rng: random.Random | None = None,
) -> list[Specialist]:
    """
    docs/DECISIONS.md § Stage 14 scope revision ("Assignment rule") and its
    15.09.2026 tie-break clarification. `candidates` is a list of
    specialists already confirmed free at the chosen slot — e.g. one value
    from compute_multi_specialist_availability's per-slot mapping, not the
    full qualifying-specialist set.

    Orders `candidates` by same-day ACTIVE_APPOINTMENT_STATUSES appointment
    count ascending (one aggregate query, not one per candidate),
    randomizing within equal-count groups via `rng` — a real random.Random()
    built here when not passed, so tests inject a seeded instance for a
    deterministic order. Returns the full ordering, not just the top pick:
    create_appointment_for_any_specialist walks it as the retry sequence on
    a later candidate's booking conflict — one ordering computed once, not
    re-rolled per attempt.
    """
    if not candidates:
        return []
    if rng is None:
        rng = random.Random()

    tz = ZoneInfo(salon.timezone)
    day_start = dt.datetime.combine(date, dt.time.min, tzinfo=tz)
    day_end = day_start + dt.timedelta(days=1)

    candidate_ids = [specialist.id for specialist in candidates]
    counts = dict.fromkeys(candidate_ids, 0)
    rows = (
        Appointment.objects.filter(
            salon=salon,
            specialist_id__in=candidate_ids,
            status__in=ACTIVE_APPOINTMENT_STATUSES,
            start_datetime__gte=day_start,
            start_datetime__lt=day_end,
        )
        .values("specialist_id")
        .annotate(appointment_count=Count("id"))
    )
    for row in rows:
        counts[row["specialist_id"]] = row["appointment_count"]

    groups: dict[int, list[Specialist]] = {}
    for specialist in candidates:
        groups.setdefault(counts[specialist.id], []).append(specialist)

    ordered: list[Specialist] = []
    for count in sorted(groups):
        group = groups[count]
        rng.shuffle(group)
        ordered.extend(group)
    return ordered


def create_appointment_for_any_specialist(
    *,
    salon: Salon,
    service: Service,
    customer: Customer,
    start_datetime: dt.datetime,
    now: dt.datetime,
    rng: random.Random | None = None,
) -> Appointment:
    """
    docs/DECISIONS.md § Stage 14 scope revision + tie-break clarification.
    Resolves who's free at `start_datetime` via
    compute_multi_specialist_availability, orders them with
    select_specialist_for_any, then attempts create_appointment once per
    candidate in that order — the same ordering is the retry sequence, not
    a fresh roll per attempt. A candidate's SlotUnavailableError (raised
    inside create_appointment, including its own ExclusionViolation
    translation) moves on to the next candidate; SlotUnavailableError is
    only re-raised once every candidate has been tried and failed.

    create_appointment itself is untouched by this — the specific-specialist
    path it already serves keeps its exact existing shape.
    """
    local_date = start_datetime.astimezone(ZoneInfo(salon.timezone)).date()
    availability = compute_multi_specialist_availability(
        service=service, salon=salon, date_from=local_date, date_to=local_date, now=now
    )
    candidates = availability.get(start_datetime, [])
    if not candidates:
        logger.warning(
            "create_appointment_for_any_specialist: slot not offered (start_datetime=%s)",
            start_datetime.isoformat(),
        )
        raise SlotNotOfferedError()

    ordered = select_specialist_for_any(candidates, salon, local_date, rng=rng)

    last_error = SlotUnavailableError()
    for specialist in ordered:
        try:
            return create_appointment(
                salon=salon,
                specialist=specialist,
                service=service,
                customer=customer,
                start_datetime=start_datetime,
                now=now,
            )
        except SlotUnavailableError as exc:
            last_error = exc
    raise last_error


def create_guest_appointment(
    *,
    salon: Salon,
    specialist: Specialist | Literal["any"],
    service: Service,
    start_datetime: dt.datetime,
    now: dt.datetime,
    customer_name: str,
    customer_email: str,
    customer_phone: str,
    rng: random.Random | None = None,
) -> tuple[Appointment, str]:
    """
    docs/DECISIONS.md § Stage 7.C-bis decisions. Guest path only. The outer
    transaction.atomic() here is the entire source of the
    "no orphaned Customer / no appointment without a token" guarantee —
    ATOMIC_REQUESTS is not set, so there is no request-level atomicity to
    lean on. create_appointment's own internal atomic() nests as a
    savepoint inside this one, same as create_appointment_for_any_specialist's
    own nested per-candidate atomic() blocks.

    `specialist` is either an already-resolved Specialist (existing path,
    unchanged) or the literal string "any" (docs/DECISIONS.md § Stage 14
    scope revision), dispatched to create_appointment_for_any_specialist.
    `rng` only matters on the "any" path — passed through for test
    determinism, ignored otherwise.
    """
    with transaction.atomic():
        customer = get_or_create_guest_customer(
            salon=salon, name=customer_name, email=customer_email, phone=customer_phone
        )
        if specialist == "any":
            appointment = create_appointment_for_any_specialist(
                salon=salon,
                service=service,
                customer=customer,
                start_datetime=start_datetime,
                now=now,
                rng=rng,
            )
        else:
            appointment = create_appointment(
                salon=salon,
                specialist=specialist,
                service=service,
                customer=customer,
                start_datetime=start_datetime,
                now=now,
            )
        raw_token, _token_row = issue_guest_token(appointment)
        record_and_dispatch_notification(
            salon=salon,
            trigger_type=NotificationTrigger.BOOKING_CREATED,
            appointment=appointment,
            dedup_key=f"booking_created:appointment:{appointment.pk}",
        )
        return appointment, raw_token


# Customer-initiated cancellations (the client themselves — GUEST via a
# guest-token link, CUSTOMER via a future Account-authenticated cancel
# path, § Stage 15 planning item 4) are the only ones the ≥24h cutoff
# applies to. STAFF/SYSTEM are not the client, so they fall through to the
# "always eligible" branch below — the same "salon-initiated" case
# docs/DECISIONS.md § Business rules describes (specialist illness,
# TimeOff, working-hours changes), even though the enum has no literal
# "salon" member (docs/DECISIONS.md § "Refund-eligibility gap (found
# 19.09.2026, closing Stage 8)").
_CUSTOMER_INITIATED_CANCELLATIONS = frozenset({CancelledBy.CUSTOMER, CancelledBy.GUEST})
REFUND_ELIGIBILITY_CUTOFF = dt.timedelta(hours=24)


def _is_refund_eligible(
    *, cancelled_by: str, now: dt.datetime, start_datetime: dt.datetime
) -> bool:
    if cancelled_by not in _CUSTOMER_INITIATED_CANCELLATIONS:
        return True
    return start_datetime - now >= REFUND_ELIGIBILITY_CUTOFF


def cancel_appointment(
    *,
    appointment_id: int,
    salon: Salon,
    cancelled_by: str,
    now: dt.datetime,
    provider: PaymentProvider,
    reason: str = "",
) -> Appointment:
    """
    docs/DECISIONS.md § Stage 7.E decisions. Role-agnostic — knows nothing
    about guest tokens; callers (e.g. the guest cancel view) handle their
    own credential-specific side effects after this returns.

    The select_for_update() fetch and the ACTIVE_APPOINTMENT_STATUSES
    recheck both happen inside the same atomic() block, in that order, so
    the row is locked before its status is read — closing the lost-update
    window a future concurrent sweep (Stage 7.F) could otherwise race
    through between an unlocked read and the write.

    Refund eligibility (docs/DECISIONS.md § "Refund-eligibility gap (found
    19.09.2026, closing Stage 8)") is decided here, after the cancellation
    transaction above has committed — not inside it. `initiate_refund`
    (payments/services.py) manages its own atomic()/select_for_update()
    plus an out-of-transaction provider network call; nesting that inside
    this function's own atomic() block would hold this row's lock for the
    duration of that network call, the same mistake
    payments/views.py's PaymentWebhookView avoids for the same reason. A
    Payment that doesn't exist, or exists but never reached SUCCEEDED (no
    money collected), is left untouched — cancellation always succeeds
    regardless of Payment/refund outcome, since it's a separate concern.

    A `PaymentProviderError` from `initiate_refund` (the provider's
    `refund()` call itself failing) is caught here and swallowed, not
    re-raised: by the time it can be raised, `initiate_refund` has already
    committed `Payment.status = REFUND_PENDING` in its own transaction, so
    the failure is already recorded as an async-recoverable state for the
    Stage 8.G stuck-refund sweep to flag — a synchronous error back to the
    caller would only contradict that design (the cancellation itself, a
    separate already-committed transaction, isn't and shouldn't be made to
    look like it failed) without adding any information the sweep doesn't
    already have.
    """
    with transaction.atomic():
        appointment = Appointment.objects.select_for_update().get(salon=salon, pk=appointment_id)
        if appointment.status not in ACTIVE_APPOINTMENT_STATUSES:
            raise InvalidStateTransitionError(details={"current_status": appointment.status})

        appointment.status = AppointmentStatus.CANCELLED
        appointment.cancelled_at = now
        appointment.cancelled_by = cancelled_by
        appointment.cancellation_reason = reason
        appointment.save(
            update_fields=["status", "cancelled_at", "cancelled_by", "cancellation_reason"]
        )
        record_and_dispatch_notification(
            salon=salon,
            trigger_type=NotificationTrigger.BOOKING_CANCELLED,
            appointment=appointment,
            dedup_key=(
                f"booking_cancelled:appointment:{appointment.pk}:"
                f"{appointment.cancelled_at.isoformat()}"
            ),
        )

    is_eligible = _is_refund_eligible(
        cancelled_by=cancelled_by, now=now, start_datetime=appointment.start_datetime
    )
    if is_eligible:
        payment = Payment.objects.filter(
            appointment=appointment, status=PaymentStatus.SUCCEEDED
        ).first()
        if payment is not None:
            try:
                initiate_refund(payment_id=payment.id, salon=salon, provider=provider, now=now)
            except PaymentProviderError:
                # initiate_refund already committed Payment.status =
                # REFUND_PENDING before calling provider.refund() (its own
                # docstring, "Writing first makes the failure mode
                # reversible") — a failed provider call is already an
                # async-recoverable state, with the Stage 8.G stuck-refund
                # sweep as its recovery path (docs/DECISIONS.md § Stage
                # 8.G decisions). Re-raising here would only turn an
                # already-committed, successful cancellation into a
                # caller-visible error for no gain: nothing further needs
                # doing synchronously, and the sweep doesn't need this
                # exception to do its job.
                pass

    return appointment


def expire_overdue_appointments(*, salon: Salon, now: dt.datetime) -> int:
    """
    docs/DECISIONS.md § Stage 7.F decisions. Requires tenant context to
    already be bound (core.tenancy.tenant_context) — this function does not
    bind it itself, same convention as create_appointment/cancel_appointment.
    Called once per salon by booking.tasks.expire_pending_payment_appointments,
    which owns the cross-salon loop and the tenant-context binding.

    Candidate ids are found with an unlocked query, then each row is locked
    and rechecked independently under its own transaction.atomic() — not one
    lock over the whole batch — so a single row can't hold up or roll back
    every other row in the same sweep. A row whose recheck finds it's no
    longer PENDING_PAYMENT (already CONFIRMED or already EXPIRED) is
    silently skipped, not raised: unlike cancel_appointment, a sweep is
    unattended background cleanup with no user to hand an error to, and the
    skip is also what makes a redelivered/overlapping run idempotent (rule 4
    of the sweep-vs-webhook policy, § Stage 7 decisions).

    Also dispatches a BOOKING_EXPIRED notification to the customer, right
    after the status write to EXPIRED (docs/DECISIONS.md § Stage 14 planning
    decisions).
    """
    candidate_ids = Appointment.objects.filter(
        salon=salon, status=AppointmentStatus.PENDING_PAYMENT, hold_expires_at__lte=now
    ).values_list("id", flat=True)

    expired_count = 0
    for appointment_id in candidate_ids:
        with transaction.atomic():
            appointment = Appointment.objects.select_for_update().get(
                salon=salon, pk=appointment_id
            )
            if appointment.status != AppointmentStatus.PENDING_PAYMENT:
                continue
            appointment.status = AppointmentStatus.EXPIRED
            appointment.save(update_fields=["status"])
            record_and_dispatch_notification(
                salon=salon,
                trigger_type=NotificationTrigger.BOOKING_EXPIRED,
                appointment=appointment,
                dedup_key=f"booking_expired:appointment:{appointment.pk}",
            )
            expired_count += 1
    return expired_count


def complete_overdue_appointments(*, salon: Salon, now: dt.datetime) -> int:
    """
    docs/DECISIONS.md § Stage 11. Requires tenant context to already be
    bound (core.tenancy.tenant_context) — this function does not bind it
    itself, same convention as expire_overdue_appointments. Called once per
    salon by booking.tasks.complete_finished_appointments, which owns the
    cross-salon loop and the tenant-context binding.

    Candidate ids are found with an unlocked query, then each row is locked
    and rechecked independently under its own transaction.atomic() — not one
    lock over the whole batch — so a single row can't hold up or roll back
    every other row in the same sweep. A row whose recheck finds it's no
    longer CONFIRMED (already CANCELLED, or already COMPLETED by an
    overlapping run) is silently skipped, not raised: same unattended-
    background-cleanup reasoning as expire_overdue_appointments, and the
    skip is what makes a redelivered/overlapping run idempotent.

    The COMPLETED status is itself the natural dedup — a row only leaves
    CONFIRMED once — so this sweep needs no explicit flag or pre-check,
    unlike flag_stuck_refunds (flagged_for_review) or
    send_due_appointment_reminders (an .exists() check on the notification
    journal).
    """
    candidate_ids = Appointment.objects.filter(
        salon=salon, status=AppointmentStatus.CONFIRMED, end_datetime__lte=now
    ).values_list("id", flat=True)

    completed_count = 0
    for appointment_id in candidate_ids:
        with transaction.atomic():
            appointment = Appointment.objects.select_for_update().get(
                salon=salon, pk=appointment_id
            )
            if appointment.status != AppointmentStatus.CONFIRMED:
                continue
            appointment.status = AppointmentStatus.COMPLETED
            appointment.save(update_fields=["status"])
            completed_count += 1
    return completed_count
