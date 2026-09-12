"""
Stage 14 planning: booking flow, payment link, confirmation email
(docs/DECISIONS.md § Stage 14 planning decisions). The BOOKING_EXPIRED
notification trigger — dispatched from `expire_overdue_appointments`
(booking/services.py), inside the per-row `transaction.atomic()` block,
immediately after the status write to EXPIRED. Real-DB integration tests
over the service function directly, mirroring
test_notifications_cancellation_trigger.py's discipline for BOOKING_CANCELLED
and test_booking_expire_appointments.py's fixture/timing conventions.

Written before expire_overdue_appointments records or dispatches any
BOOKING_EXPIRED Notification — expected red: NotificationTrigger.BOOKING_EXPIRED
does not exist yet (AttributeError), and once that lands, the assertions on
Notification rows / dispatch will still fail until the trigger is wired.

Tests run under plain @pytest.mark.django_db, so transaction.on_commit never
fires on its own — expire_overdue_appointments's per-row atomic() is a
savepoint under the test's own outer, rolled-back transaction.
django_capture_on_commit_callbacks drives it explicitly.
"""

import datetime as dt

import pytest
from django.core import mail

from booking.models import AppointmentStatus
from booking.services import expire_overdue_appointments
from core.tenancy import tenant_context
from notifications.models import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationTrigger,
)
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 8, 20, 10, 0, tzinfo=dt.UTC)
NOW = dt.datetime(2026, 8, 18, 9, 0, tzinfo=dt.UTC)
PAST = NOW - dt.timedelta(minutes=1)
FUTURE = NOW + dt.timedelta(minutes=10)


def _notifications(salon, **filters):
    with tenant_context(salon.id):
        return list(Notification.objects.filter(**filters))


# --- 1. Happy path ---------------------------------------------------------


def test_expire_overdue_appointments_records_and_delivers_booking_expired_notification(
    salon, specialist, service, customer, django_capture_on_commit_callbacks
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

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with tenant_context(salon.id):
            count = expire_overdue_appointments(salon=salon, now=NOW)

    assert count == 1
    assert len(callbacks) == 1

    rows = _notifications(salon, trigger_type=NotificationTrigger.BOOKING_EXPIRED)
    assert len(rows) == 1
    row = rows[0]
    assert row.channel == NotificationChannel.EMAIL
    assert row.customer_id == customer.id
    assert row.appointment_id == appt.id
    assert row.dedup_key == f"booking_expired:appointment:{appt.pk}"
    assert row.status == NotificationStatus.SENT  # dispatched + delivered
    assert [m.to for m in mail.outbox] == [[customer.email]]


# --- 2. Dispatch deferred until after commit --------------------------------


def test_expire_overdue_appointments_booking_expired_dispatch_is_deferred_until_after_commit(
    salon, specialist, service, customer, django_capture_on_commit_callbacks
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

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with tenant_context(salon.id):
            expire_overdue_appointments(salon=salon, now=NOW)
        rows = _notifications(salon, trigger_type=NotificationTrigger.BOOKING_EXPIRED)
        assert len(rows) == 1
        assert rows[0].status == NotificationStatus.PENDING
        assert mail.outbox == []

    assert len(callbacks) == 1
    for callback in callbacks:
        callback()

    rows = _notifications(salon, trigger_type=NotificationTrigger.BOOKING_EXPIRED)
    assert rows[0].status == NotificationStatus.SENT
    assert len(mail.outbox) == 1
    assert appt.id == rows[0].appointment_id


# --- 3. A not-yet-overdue hold is untouched: no notification ---------------


def test_expire_overdue_appointments_does_not_notify_for_a_not_yet_overdue_hold(
    salon, specialist, service, customer, django_capture_on_commit_callbacks
):
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
        hold_expires_at=FUTURE,
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with tenant_context(salon.id):
            count = expire_overdue_appointments(salon=salon, now=NOW)

    assert count == 0
    assert callbacks == []
    assert _notifications(salon, trigger_type=NotificationTrigger.BOOKING_EXPIRED) == []


# --- 4. Idempotency: an already-EXPIRED row is not reprocessed -------------


def test_expire_overdue_appointments_does_not_renotify_an_already_expired_appointment(
    salon, specialist, service, customer, django_capture_on_commit_callbacks
):
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.EXPIRED,
        hold_expires_at=PAST,
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with tenant_context(salon.id):
            count = expire_overdue_appointments(salon=salon, now=NOW)

    assert count == 0
    assert callbacks == []
    assert _notifications(salon, trigger_type=NotificationTrigger.BOOKING_EXPIRED) == []
