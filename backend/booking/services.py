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
from zoneinfo import ZoneInfo

import psycopg
from django.db import IntegrityError, transaction

from accounts.models import Customer
from accounts.services import get_or_create_guest_customer
from booking.constants import SLOT_HOLD_DURATION
from booking.guest_tokens import issue_guest_token
from booking.models import ACTIVE_APPOINTMENT_STATUSES, Appointment, AppointmentStatus
from catalog.models import Service
from core.exceptions import InvalidStateTransitionError, SlotNotOfferedError, SlotUnavailableError
from notifications.models import NotificationTrigger
from notifications.services import record_and_dispatch_notification
from scheduling.services import compute_candidate_start_times
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


def create_guest_appointment(
    *,
    salon: Salon,
    specialist: Specialist,
    service: Service,
    start_datetime: dt.datetime,
    now: dt.datetime,
    customer_name: str,
    customer_email: str,
    customer_phone: str,
) -> tuple[Appointment, str]:
    """
    docs/DECISIONS.md § Stage 7.C-bis decisions. Guest path only. The outer
    transaction.atomic() here is the entire source of the
    "no orphaned Customer / no appointment without a token" guarantee —
    ATOMIC_REQUESTS is not set, so there is no request-level atomicity to
    lean on. create_appointment's own internal atomic() nests as a
    savepoint inside this one.
    """
    with transaction.atomic():
        customer = get_or_create_guest_customer(
            salon=salon, name=customer_name, email=customer_email, phone=customer_phone
        )
        appointment = create_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=start_datetime,
            now=now,
        )
        raw_token, _token_row = issue_guest_token(appointment)
        return appointment, raw_token


def cancel_appointment(
    *,
    appointment_id: int,
    salon: Salon,
    cancelled_by: str,
    now: dt.datetime,
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
            expired_count += 1
    return expired_count
