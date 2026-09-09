"""
Stage 7.F — `expire_overdue_appointments` service function and the
`expire_pending_payment_appointments` periodic Celery task
(docs/ARCHITECTURE.md § 5, § 12, the State machines section;
docs/DECISIONS.md § Stage 7 decisions' sweep-vs-webhook policy, § Stage 7.F
decisions). Tests only — written before either the service or
`booking/tasks.py` exist. Expected to fail on collection (ImportError:
cannot import name 'expire_overdue_appointments' from 'booking.services',
or ModuleNotFoundError: No module named 'booking.tasks') until they're
added — same two-step red shape `test_booking_create_appointment.py` and
`test_booking_cancel_appointment.py` established for their own services.

Signatures under test:

    expire_overdue_appointments(*, salon, now) -> int
    expire_pending_payment_appointments() -> None   # @shared_task

Real-DB integration tests throughout (the service does select_for_update()
plus a write) — no monkeypatching, except test 8's deliberate one-salon
failure double. All `now`/`hold_expires_at` values in the SERVICE-level
tests (1-6) are tz-aware UTC literals, never `timezone.now()` — same
discipline as § Stage 6.F/6.I and § Stage 7.E decisions. The TASK-level
tests (7-8) are the one exception: the task reads its own `now =
timezone.now()` internally (§ Stage 7.F decisions), so there is no literal
to inject from the test side — `hold_expires_at` there is built from real
`timezone.now()` instead. Every appointment/customer/specialist/service
created here passes `salon=` explicitly (Stage 6 shell-seeding trap, per
CLAUDE.md).
"""

import datetime as dt
import logging

import pytest
from django.utils import timezone

import booking.tasks as booking_tasks
from accounts.models import Customer
from booking.models import Appointment, AppointmentStatus
from booking.services import expire_overdue_appointments
from booking.tasks import expire_pending_payment_appointments
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from specialists.models import Specialist
from tenants.models import Salon
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 8, 20, 10, 0, tzinfo=dt.UTC)
NOW = dt.datetime(2026, 8, 18, 9, 0, tzinfo=dt.UTC)
PAST = NOW - dt.timedelta(minutes=1)
FUTURE = NOW + dt.timedelta(minutes=10)


def _unrelated_fields(appt: Appointment) -> dict:
    """Fields expire_overdue_appointments must never touch — everything
    except `status`, which is the only field this transition writes."""
    return {
        "start_datetime": appt.start_datetime,
        "end_datetime": appt.end_datetime,
        "blocked_until": appt.blocked_until,
        "specialist_id": appt.specialist_id,
        "service_id": appt.service_id,
        "customer_id": appt.customer_id,
        "service_price_at_booking": appt.service_price_at_booking,
        "deposit_percentage_at_booking": appt.deposit_percentage_at_booking,
        "hold_expires_at": appt.hold_expires_at,
        "cancelled_at": appt.cancelled_at,
        "cancelled_by": appt.cancelled_by,
        "cancellation_reason": appt.cancellation_reason,
    }


def _make_overdue_appointment(
    salon: Salon, *, hold_expires_at: dt.datetime, start: dt.datetime
) -> Appointment:
    """Creates a fresh specialist/service/customer for `salon` and one
    PENDING_PAYMENT appointment. Used only by the task-level tests (7-8),
    which need a second, fully independent salon rather than the shared
    `salon` fixture's own service/specialist/customer."""
    with tenant_context(salon.id):
        category = ServiceCategory.objects.create(salon=salon, name={"en": "Nails"})
        service = Service.objects.create(
            salon=salon,
            category=category,
            name={"en": "Manicure"},
            duration_minutes=60,
            price="500.00",
            buffer_minutes=15,
        )
        specialist = Specialist.objects.create(salon=salon, name={"en": "Specialist"})
        customer = Customer.objects.create(
            salon=salon,
            name="Customer",
            email=f"customer-{salon.id}@example.com",
            phone="+10000000002",
        )
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.PENDING_PAYMENT,
        hold_expires_at=hold_expires_at,
    )


# --- 1. Happy path: overdue PENDING_PAYMENT is expired ----------------------


def test_expire_overdue_appointments_expires_an_overdue_pending_payment_appointment(
    salon, specialist, service, customer
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
        hold_expires_at=PAST,
    )
    # Refresh before snapshotting: the in-memory object from .create() still
    # carries service_price_at_booking/deposit_percentage_at_booking as
    # whatever Python type the service/salon fixtures assigned, not the
    # Decimal a DB read returns — same coercion trap
    # test_booking_cancel_appointment.py's snapshot tests document.
    with tenant_context(salon.id):
        appt.refresh_from_db()
    before = _unrelated_fields(appt)

    with tenant_context(salon.id):
        count = expire_overdue_appointments(salon=salon, now=NOW)

    assert count == 1

    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.EXPIRED
    assert _unrelated_fields(row) == before


# --- 2. hold_expires_at in the future is not touched -------------------------


def test_expire_overdue_appointments_does_not_touch_a_not_yet_overdue_hold(
    salon, specialist, service, customer
):
    """Proves the filter is hold_expires_at <= now, not "all
    PENDING_PAYMENT"."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
        hold_expires_at=FUTURE,
    )

    with tenant_context(salon.id):
        count = expire_overdue_appointments(salon=salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.PENDING_PAYMENT


# --- 3. CONFIRMED with a past hold_expires_at is not touched -----------------


def test_expire_overdue_appointments_does_not_touch_a_confirmed_appointment(
    salon, specialist, service, customer
):
    """The recheck guard: CONFIRMED is not PENDING_PAYMENT, even with a
    stale past hold_expires_at still sitting on the row from booking time."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
        hold_expires_at=PAST,
    )

    with tenant_context(salon.id):
        count = expire_overdue_appointments(salon=salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.CONFIRMED


# --- 4. Already-EXPIRED is not re-processed (idempotency) --------------------


def test_expire_overdue_appointments_does_not_reprocess_an_already_expired_appointment(
    salon, specialist, service, customer
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.EXPIRED,
        hold_expires_at=PAST,
    )

    with tenant_context(salon.id):
        count = expire_overdue_appointments(salon=salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.EXPIRED


# --- 5. Multiple overdue rows in one salon; a non-overdue row is left alone --


def test_expire_overdue_appointments_expires_every_overdue_row_in_the_salon(
    salon, specialist, service, customer
):
    # Distinct start times so the exclusion constraint (same specialist,
    # overlapping active-status intervals) doesn't reject these as
    # double-bookings — each is 60 min + 15 min buffer, so 3h apart is safe.
    overdue_1 = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
        hold_expires_at=PAST,
    )
    overdue_2 = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(hours=3),
        status=AppointmentStatus.PENDING_PAYMENT,
        hold_expires_at=PAST,
    )
    not_overdue = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(hours=6),
        status=AppointmentStatus.PENDING_PAYMENT,
        hold_expires_at=FUTURE,
    )

    with tenant_context(salon.id):
        count = expire_overdue_appointments(salon=salon, now=NOW)

    assert count == 2
    with tenant_context(salon.id):
        row_1 = Appointment.objects.get(pk=overdue_1.id)
        row_2 = Appointment.objects.get(pk=overdue_2.id)
        row_3 = Appointment.objects.get(pk=not_overdue.id)
    assert row_1.status == AppointmentStatus.EXPIRED
    assert row_2.status == AppointmentStatus.EXPIRED
    assert row_3.status == AppointmentStatus.PENDING_PAYMENT


# --- 6. Tenant isolation ------------------------------------------------------


def test_expire_overdue_appointments_does_not_touch_another_salons_appointment(
    salon, other_salon, specialist, service, customer
):
    """specialist/service/customer/appointment all belong to `salon`; the
    call passes salon=other_salon. Proves `salon` is actually applied in
    the overdue query, not silently ignored — same reasoning as
    test_booking_cancel_appointment.py's own tenant-isolation test."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
        hold_expires_at=PAST,
    )

    with tenant_context(salon.id):
        count = expire_overdue_appointments(salon=other_salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.PENDING_PAYMENT


# --- 7. Task: cross-tenant loop visits every salon ---------------------------


def test_expire_pending_payment_appointments_task_expires_overdue_rows_in_every_salon(
    salon, specialist, service, customer, other_salon
):
    """Called directly and synchronously (not via .delay()/a worker). An
    overdue row in each of two salons, one task call. If tenant_context
    didn't bind per salon, or bled from one iteration into the next, at
    least one of these two rows would end up missed or mis-attributed."""
    overdue = timezone.now() - dt.timedelta(minutes=1)
    appt_a = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
        hold_expires_at=overdue,
    )
    appt_b = _make_overdue_appointment(other_salon, hold_expires_at=overdue, start=START)

    expire_pending_payment_appointments()

    with tenant_context(salon.id):
        row_a = Appointment.objects.get(pk=appt_a.id)
    with tenant_context(other_salon.id):
        row_b = Appointment.objects.get(pk=appt_b.id)
    assert row_a.status == AppointmentStatus.EXPIRED
    assert row_b.status == AppointmentStatus.EXPIRED


# --- 8. Task: one failing salon does not abort the run -----------------------


def test_expire_pending_payment_appointments_task_continues_past_a_failing_salon(
    monkeypatch, caplog, salon, specialist, service, customer, other_salon
):
    """
    One salon's processing raises; the task must log it and continue to the
    next salon rather than aborting the whole run (§ Stage 7.F decisions,
    cross-tenant-loop safety requirement (b)).

    Patch target: `booking.tasks.expire_overdue_appointments`, not
    `booking.services.expire_overdue_appointments`. The task is expected to
    do `from booking.services import expire_overdue_appointments` and call
    it as a bare module-global name — the same "from module import name,
    not a namespaced reference" convention `test_booking_create_appointment.py`
    already pins for `compute_candidate_start_times` /
    `_has_overlapping_active_appointment` in `booking/services.py` — so the
    name to intercept lives in `booking.tasks`'s own namespace, not
    `booking.services`'s. If a future refactor imports the module instead
    of the name, this patch target silently stops intercepting and this
    test would start exercising the real service instead of the fake — a
    signal to update the patch target, not a false pass.
    """
    caplog.set_level(logging.ERROR, logger="booking.tasks")
    overdue = timezone.now() - dt.timedelta(minutes=1)
    appt_a = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
        hold_expires_at=overdue,
    )
    appt_b = _make_overdue_appointment(other_salon, hold_expires_at=overdue, start=START)

    failing_salon_id = salon.id

    def _fake_expire_overdue_appointments(*, salon, now):
        if salon.id == failing_salon_id:
            raise RuntimeError("boom")
        # A minimal stand-in for the real transition, scoped exactly like
        # the real service would be, so salon B's row is observably
        # expired without depending on the (not-yet-written) real service.
        return Appointment.objects.filter(
            salon=salon,
            status=AppointmentStatus.PENDING_PAYMENT,
            hold_expires_at__lte=now,
        ).update(status=AppointmentStatus.EXPIRED)

    monkeypatch.setattr(
        booking_tasks, "expire_overdue_appointments", _fake_expire_overdue_appointments
    )

    expire_pending_payment_appointments()  # must not raise/propagate

    with tenant_context(salon.id):
        row_a = Appointment.objects.get(pk=appt_a.id)
    with tenant_context(other_salon.id):
        row_b = Appointment.objects.get(pk=appt_b.id)
    assert row_a.status == AppointmentStatus.PENDING_PAYMENT
    assert row_b.status == AppointmentStatus.EXPIRED
    assert any(str(failing_salon_id) in record.getMessage() for record in caplog.records)
