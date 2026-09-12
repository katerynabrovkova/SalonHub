"""
Stage 14 planning: booking flow, payment link, confirmation email
(docs/DECISIONS.md § Stage 14 planning decisions). The BOOKING_CREATED
notification trigger — dispatched from `create_guest_appointment`
(booking/services.py), inside the same outer `transaction.atomic()` block,
immediately after `issue_guest_token`. Real-DB integration tests over the
orchestrator directly, mirroring test_notifications_cancellation_trigger.py's
discipline for BOOKING_CANCELLED.

Written before create_guest_appointment records or dispatches any
BOOKING_CREATED Notification — expected red: NotificationTrigger.BOOKING_CREATED
does not exist yet (AttributeError), and once that lands, the assertions on
Notification rows / dispatch will still fail until the trigger is wired.

Tests run under plain @pytest.mark.django_db, so transaction.on_commit never
fires on its own — the service's atomic() is a savepoint under the test's own
outer, rolled-back transaction. django_capture_on_commit_callbacks drives it
explicitly, same pattern as test_notifications_webhook_triggers.py and
test_notifications_cancellation_trigger.py.
"""

import datetime as dt

import pytest
from django.core import mail

from booking.models import AppointmentStatus
from booking.services import create_guest_appointment
from core.exceptions import DomainError
from core.tenancy import tenant_context
from notifications.models import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationTrigger,
)
from tests.conftest import make_appointment, make_working_hours

pytestmark = pytest.mark.django_db

UTC = dt.UTC

# Same numbers as test_booking_create_guest_appointment.py: a Monday with
# 09:00-18:00 local (Europe/Kyiv, the `salon` fixture's default tz) working
# hours == 06:00-15:00 UTC, so 06:00 UTC is always the first on-grid
# candidate for the `service` fixture's duration/buffer.
MONDAY = dt.date(2026, 8, 17)
FIRST_CANDIDATE = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)

# Well within the default min_lead_time_hours=3 / max_advance_days=60 window
# for FIRST_CANDIDATE.
SAFE_NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=UTC)


def _working_hours(salon, specialist, *, day: dt.date = MONDAY) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=day.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )


def _notifications(salon, **filters):
    with tenant_context(salon.id):
        return list(Notification.objects.filter(**filters))


# --- 1. Happy path -------------------------------------------------------


def test_create_guest_appointment_records_and_delivers_booking_created_notification(
    salon, specialist, service, django_capture_on_commit_callbacks
):
    _working_hours(salon, specialist)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with tenant_context(salon.id):
            appt, _raw_token = create_guest_appointment(
                salon=salon,
                specialist=specialist,
                service=service,
                start_datetime=FIRST_CANDIDATE,
                now=SAFE_NOW,
                customer_name="Alice",
                customer_email="alice@example.com",
                customer_phone="+10000000000",
            )

    assert len(callbacks) == 1

    rows = _notifications(salon, trigger_type=NotificationTrigger.BOOKING_CREATED)
    assert len(rows) == 1
    row = rows[0]
    assert row.channel == NotificationChannel.EMAIL
    assert row.customer_id == appt.customer_id
    assert row.appointment_id == appt.id
    assert row.dedup_key == f"booking_created:appointment:{appt.pk}"
    assert row.status == NotificationStatus.SENT  # dispatched + delivered
    assert [m.to for m in mail.outbox] == [["alice@example.com"]]


# --- 2. Dispatch deferred until after commit ------------------------------


def test_create_guest_appointment_booking_created_dispatch_is_deferred_until_after_commit(
    salon, specialist, service, django_capture_on_commit_callbacks
):
    _working_hours(salon, specialist)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with tenant_context(salon.id):
            appt, _raw_token = create_guest_appointment(
                salon=salon,
                specialist=specialist,
                service=service,
                start_datetime=FIRST_CANDIDATE,
                now=SAFE_NOW,
                customer_name="Alice",
                customer_email="alice@example.com",
                customer_phone="+10000000000",
            )
        rows = _notifications(salon, trigger_type=NotificationTrigger.BOOKING_CREATED)
        assert len(rows) == 1
        assert rows[0].status == NotificationStatus.PENDING
        assert mail.outbox == []

    assert len(callbacks) == 1
    for callback in callbacks:
        callback()

    rows = _notifications(salon, trigger_type=NotificationTrigger.BOOKING_CREATED)
    assert rows[0].status == NotificationStatus.SENT
    assert len(mail.outbox) == 1


# --- 3. Failure atomicity: no notification on a failed creation ----------


def test_create_guest_appointment_failure_records_no_booking_created_notification(
    salon, specialist, service, customer, django_capture_on_commit_callbacks
):
    """A slot already occupied by another customer makes create_appointment
    raise before issue_guest_token is ever reached, so the BOOKING_CREATED
    dispatch call site is never executed."""
    _working_hours(salon, specialist)
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
        status=AppointmentStatus.CONFIRMED,
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with tenant_context(salon.id), pytest.raises(DomainError):
            create_guest_appointment(
                salon=salon,
                specialist=specialist,
                service=service,
                start_datetime=FIRST_CANDIDATE,
                now=SAFE_NOW,
                customer_name="Bob",
                customer_email="bob@example.com",
                customer_phone="+10000000001",
            )

    assert callbacks == []
    assert _notifications(salon, trigger_type=NotificationTrigger.BOOKING_CREATED) == []
