"""
Stage 11 — `complete_overdue_appointments` service function and the
`complete_finished_appointments` periodic Celery task: the time-based
CONFIRMED -> COMPLETED transition, fired once an appointment's
`end_datetime` has passed (docs/ARCHITECTURE.md § 12, the State machines
section; docs/DECISIONS.md § Stage 11). Mirrors Stage 7.F
(`expire_overdue_appointments` / `expire_pending_payment_appointments`)
line-for-line — see tests/test_booking_expire_appointments.py.

Tests only — written before either the service or the task exist. Expected
to fail on collection (ImportError: cannot import name
'complete_overdue_appointments' from 'booking.services' /
'complete_finished_appointments' from 'booking.tasks') until they're added.

Signatures under test:

    complete_overdue_appointments(*, salon, now) -> int
    complete_finished_appointments() -> None   # @shared_task

Real-DB integration tests throughout (the service does select_for_update()
plus a write). All `now`/`end_datetime` values in the SERVICE-level tests
(1-7) are tz-aware UTC literals, never `timezone.now()` — same discipline
as Stage 7.F. The TASK-level tests (8-9) are the exception: the task reads
its own `now = timezone.now()` internally, so there is no literal to inject
from the test side — the past `end_datetime` there is built from real
`timezone.now() - timedelta(minutes=1)` instead. Every
appointment/customer/specialist/service created here passes `salon=`
explicitly (Stage 6 shell-seeding trap, per CLAUDE.md).
"""

import datetime as dt
import logging

import pytest
from django.utils import timezone

import booking.tasks as booking_tasks
from accounts.models import Customer
from booking.models import Appointment, AppointmentStatus
from booking.services import complete_overdue_appointments
from booking.tasks import complete_finished_appointments
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from specialists.models import Specialist
from tenants.models import Salon
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

# Fixed clock for every service-level test.
NOW = dt.datetime(2026, 8, 18, 9, 0, tzinfo=dt.UTC)
# The service fixture's Service.duration_minutes is 60, so end_datetime is
# always start + 1h. Pick a start that puts end_datetime where the test
# wants it relative to NOW.
_DURATION = dt.timedelta(minutes=60)


def _unrelated_fields(appt: Appointment) -> dict:
    """Fields complete_overdue_appointments must never touch — everything
    except `status`, the only field this transition writes."""
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


def _appt_ending_at(
    salon, customer, specialist, service, *, end: dt.datetime, status=AppointmentStatus.CONFIRMED
) -> Appointment:
    """One appointment for `salon` whose end_datetime is exactly `end`."""
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=end - _DURATION,
        status=status,
    )


def _make_finished_appointment(
    salon: Salon, *, end: dt.datetime, status=AppointmentStatus.CONFIRMED
) -> Appointment:
    """Creates a fresh specialist/service/customer for `salon` and one
    appointment ending at `end`. Used by the task-level tests (8-9), which
    need a second, fully independent salon rather than the shared `salon`
    fixture's own service/specialist/customer."""
    with tenant_context(salon.id):
        category = ServiceCategory.objects.create(salon=salon, name="Nails")
        service = Service.objects.create(
            salon=salon,
            category=category,
            name="Manicure",
            duration_minutes=60,
            price="500.00",
            buffer_minutes=15,
        )
        specialist = Specialist.objects.create(salon=salon, name="Specialist")
        customer = Customer.objects.create(
            salon=salon,
            name="Customer",
            email=f"customer-{salon.id}@example.com",
            phone="+10000000002",
        )
    return _appt_ending_at(salon, customer, specialist, service, end=end, status=status)


# --- 1. Happy path + <= boundary (parametrized) -----------------------------


@pytest.mark.parametrize(
    ("end_offset", "should_sweep"),
    [
        pytest.param(dt.timedelta(minutes=-1), True, id="past-end-swept"),
        pytest.param(dt.timedelta(0), True, id="end-exactly-now-swept"),
        pytest.param(dt.timedelta(seconds=1), False, id="end-one-second-after-now-not-swept"),
    ],
)
def test_complete_overdue_appointments_boundary(
    salon, specialist, service, customer, end_offset, should_sweep
):
    """end_datetime <= now is swept (a visit ending exactly now counts as
    finished); end_datetime one second later is not. Together these two
    prove the contract is `end_datetime__lte=now`, not `__lt`."""
    appt = _appt_ending_at(
        salon,
        customer,
        specialist,
        service,
        end=NOW + end_offset,
        status=AppointmentStatus.CONFIRMED,
    )
    with tenant_context(salon.id):
        appt.refresh_from_db()
    before = _unrelated_fields(appt)

    with tenant_context(salon.id):
        count = complete_overdue_appointments(salon=salon, now=NOW)

    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)

    if should_sweep:
        assert count == 1
        assert row.status == AppointmentStatus.COMPLETED
    else:
        assert count == 0
        assert row.status == AppointmentStatus.CONFIRMED
    assert _unrelated_fields(row) == before


# --- 2. Future end_datetime is not touched ----------------------------------


def test_complete_overdue_appointments_does_not_touch_a_future_appointment(
    salon, specialist, service, customer
):
    """Proves the filter is end_datetime <= now, not "all CONFIRMED"."""
    appt = _appt_ending_at(
        salon,
        customer,
        specialist,
        service,
        end=NOW + dt.timedelta(hours=2),
        status=AppointmentStatus.CONFIRMED,
    )

    with tenant_context(salon.id):
        count = complete_overdue_appointments(salon=salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.CONFIRMED


# --- 3. Wrong status: PENDING_PAYMENT with past end is not touched ----------


def test_complete_overdue_appointments_does_not_touch_a_pending_payment_appointment(
    salon, specialist, service, customer
):
    """Only CONFIRMED is swept. A PENDING_PAYMENT hold whose end_datetime is
    already in the past is not a finished visit."""
    appt = _appt_ending_at(
        salon,
        customer,
        specialist,
        service,
        end=NOW - dt.timedelta(hours=1),
        status=AppointmentStatus.PENDING_PAYMENT,
    )

    with tenant_context(salon.id):
        count = complete_overdue_appointments(salon=salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.PENDING_PAYMENT


# --- 4. Idempotency: already-COMPLETED is not re-processed -----------------


def test_complete_overdue_appointments_does_not_reprocess_an_already_completed_appointment(
    salon, specialist, service, customer
):
    appt = _appt_ending_at(
        salon,
        customer,
        specialist,
        service,
        end=NOW - dt.timedelta(hours=1),
        status=AppointmentStatus.COMPLETED,
    )

    with tenant_context(salon.id):
        count = complete_overdue_appointments(salon=salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.COMPLETED


# --- 5. Other terminal statuses (CANCELLED, EXPIRED) are not touched -------


def test_complete_overdue_appointments_does_not_touch_other_terminal_statuses(
    salon, specialist, service, customer
):
    cancelled = _appt_ending_at(
        salon,
        customer,
        specialist,
        service,
        end=NOW - dt.timedelta(hours=1),
        status=AppointmentStatus.CANCELLED,
    )
    expired = _appt_ending_at(
        salon,
        customer,
        specialist,
        service,
        end=NOW - dt.timedelta(hours=5),
        status=AppointmentStatus.EXPIRED,
    )

    with tenant_context(salon.id):
        count = complete_overdue_appointments(salon=salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row_cancelled = Appointment.objects.get(pk=cancelled.id)
        row_expired = Appointment.objects.get(pk=expired.id)
    assert row_cancelled.status == AppointmentStatus.CANCELLED
    assert row_expired.status == AppointmentStatus.EXPIRED


# --- 6. Mixed batch: two past, one future decoy ---------------------------


def test_complete_overdue_appointments_completes_every_finished_row_in_the_salon(
    salon, specialist, service, customer
):
    # Distinct, non-overlapping intervals so the exclusion constraint (same
    # specialist, overlapping active-status intervals) doesn't reject these
    # — 60 min + 15 min buffer, so several hours apart is safe.
    past_1 = _appt_ending_at(
        salon,
        customer,
        specialist,
        service,
        end=NOW - dt.timedelta(hours=1),
        status=AppointmentStatus.CONFIRMED,
    )
    past_2 = _appt_ending_at(
        salon,
        customer,
        specialist,
        service,
        end=NOW - dt.timedelta(hours=6),
        status=AppointmentStatus.CONFIRMED,
    )
    decoy_future = _appt_ending_at(
        salon,
        customer,
        specialist,
        service,
        end=NOW + dt.timedelta(hours=6),
        status=AppointmentStatus.CONFIRMED,
    )

    with tenant_context(salon.id):
        count = complete_overdue_appointments(salon=salon, now=NOW)

    assert count == 2
    with tenant_context(salon.id):
        row_1 = Appointment.objects.get(pk=past_1.id)
        row_2 = Appointment.objects.get(pk=past_2.id)
        row_3 = Appointment.objects.get(pk=decoy_future.id)
    assert row_1.status == AppointmentStatus.COMPLETED
    assert row_2.status == AppointmentStatus.COMPLETED
    assert row_3.status == AppointmentStatus.CONFIRMED


# --- 7. Tenant isolation --------------------------------------------------


def test_complete_overdue_appointments_does_not_touch_another_salons_appointment(
    salon, other_salon, specialist, service, customer
):
    """specialist/service/customer/appointment all belong to `salon`; the
    call passes salon=other_salon. Proves `salon` is actually applied in
    the candidate query, not silently ignored."""
    appt = _appt_ending_at(
        salon,
        customer,
        specialist,
        service,
        end=NOW - dt.timedelta(hours=1),
        status=AppointmentStatus.CONFIRMED,
    )

    with tenant_context(salon.id):
        count = complete_overdue_appointments(salon=other_salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.CONFIRMED


# --- 8. Task: cross-tenant loop visits every salon -----------------------


def test_complete_finished_appointments_task_completes_finished_rows_in_every_salon(
    salon, specialist, service, customer, other_salon
):
    """Called directly and synchronously (not via .delay()/a worker). A
    finished CONFIRMED row in each of two salons, one task call. If
    tenant_context didn't bind per salon, or bled from one iteration into
    the next, at least one of these two rows would end up missed or
    mis-attributed."""
    past_end = timezone.now() - dt.timedelta(minutes=1)
    appt_a = _appt_ending_at(
        salon,
        customer,
        specialist,
        service,
        end=past_end,
        status=AppointmentStatus.CONFIRMED,
    )
    appt_b = _make_finished_appointment(other_salon, end=past_end)

    complete_finished_appointments()

    with tenant_context(salon.id):
        row_a = Appointment.objects.get(pk=appt_a.id)
    with tenant_context(other_salon.id):
        row_b = Appointment.objects.get(pk=appt_b.id)
    assert row_a.status == AppointmentStatus.COMPLETED
    assert row_b.status == AppointmentStatus.COMPLETED


# --- 9. Task: one failing salon does not abort the run ------------------


def test_complete_finished_appointments_task_continues_past_a_failing_salon(
    monkeypatch, caplog, salon, specialist, service, customer, other_salon
):
    """
    One salon's processing raises; the task must log it and continue to the
    next salon rather than aborting the whole run — same cross-tenant-loop
    safety requirement as Stage 7.F.

    Patch target: `booking.tasks.complete_overdue_appointments`, not
    `booking.services.complete_overdue_appointments`. The task is expected
    to do `from booking.services import complete_overdue_appointments` and
    call it as a bare module-global name, so the name to intercept lives in
    `booking.tasks`'s own namespace.
    """
    caplog.set_level(logging.ERROR, logger="booking.tasks")
    past_end = timezone.now() - dt.timedelta(minutes=1)
    appt_a = _appt_ending_at(
        salon,
        customer,
        specialist,
        service,
        end=past_end,
        status=AppointmentStatus.CONFIRMED,
    )
    appt_b = _make_finished_appointment(other_salon, end=past_end)

    failing_salon_id = salon.id

    def _fake_complete_overdue_appointments(*, salon, now):
        if salon.id == failing_salon_id:
            raise RuntimeError("boom")
        return Appointment.objects.filter(
            salon=salon,
            status=AppointmentStatus.CONFIRMED,
            end_datetime__lte=now,
        ).update(status=AppointmentStatus.COMPLETED)

    monkeypatch.setattr(
        booking_tasks, "complete_overdue_appointments", _fake_complete_overdue_appointments
    )

    complete_finished_appointments()  # must not raise/propagate

    with tenant_context(salon.id):
        row_a = Appointment.objects.get(pk=appt_a.id)
    with tenant_context(other_salon.id):
        row_b = Appointment.objects.get(pk=appt_b.id)
    assert row_a.status == AppointmentStatus.CONFIRMED
    assert row_b.status == AppointmentStatus.COMPLETED
    assert any(str(failing_salon_id) in record.getMessage() for record in caplog.records)
