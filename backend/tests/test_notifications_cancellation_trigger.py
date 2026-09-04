"""
Stage 9 step (c) commit 2b — the BOOKING_CANCELLED notification trigger
(docs/ARCHITECTURE.md § 9; docs/DECISIONS.md § Stage 9 step (c) `dedup_key`
formats). Real-DB integration tests over booking.services.cancel_appointment
directly — service level, not the guest cancel view, the same discipline
test_booking_cancel_appointment.py already established for the function
itself.

Written before cancel_appointment records or dispatches any Notification —
expected red: the assertions on Notification rows / dispatch fail until the
trigger is wired.

Tests run under plain @pytest.mark.django_db, so transaction.on_commit never
fires on its own — the service's atomic() is a savepoint under the test's
own outer, rolled-back transaction. django_capture_on_commit_callbacks
drives it explicitly, the same pattern test_notifications_webhook_triggers.py
established.
"""

import datetime as dt

import pytest
from django.core import mail

from booking.models import AppointmentStatus, CancelledBy
from booking.services import cancel_appointment
from core.exceptions import InvalidStateTransitionError
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


def _notifications(salon, **filters):
    with tenant_context(salon.id):
        return list(Notification.objects.filter(**filters))


def test_cancel_appointment_records_and_delivers_booking_cancelled_notification(
    salon, specialist, service, customer, django_capture_on_commit_callbacks
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with tenant_context(salon.id):
            result = cancel_appointment(
                appointment_id=appt.id,
                salon=salon,
                cancelled_by=CancelledBy.CUSTOMER,
                now=NOW,
            )

    assert len(callbacks) == 1

    rows = _notifications(salon, trigger_type=NotificationTrigger.BOOKING_CANCELLED)
    assert len(rows) == 1
    row = rows[0]
    assert row.channel == NotificationChannel.EMAIL
    assert row.customer_id == customer.id
    assert row.appointment_id == appt.id
    assert row.dedup_key == (
        f"booking_cancelled:appointment:{appt.pk}:{result.cancelled_at.isoformat()}"
    )
    assert row.status == NotificationStatus.SENT  # dispatched + delivered
    assert [m.to for m in mail.outbox] == [[customer.email]]


def test_cancel_appointment_notification_dispatch_is_deferred_until_after_commit(
    salon, specialist, service, customer, django_capture_on_commit_callbacks
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with tenant_context(salon.id):
            cancel_appointment(
                appointment_id=appt.id,
                salon=salon,
                cancelled_by=CancelledBy.CUSTOMER,
                now=NOW,
            )
        # Rows are written, but nothing has been sent yet — the send is
        # queued behind commit, not run inline.
        rows = _notifications(salon, trigger_type=NotificationTrigger.BOOKING_CANCELLED)
        assert len(rows) == 1
        assert rows[0].status == NotificationStatus.PENDING
        assert mail.outbox == []

    assert len(callbacks) == 1
    for callback in callbacks:
        callback()

    rows = _notifications(salon, trigger_type=NotificationTrigger.BOOKING_CANCELLED)
    assert rows[0].status == NotificationStatus.SENT
    assert len(mail.outbox) == 1


def test_cancel_appointment_rejected_transition_records_no_notification(
    salon, specialist, service, customer, django_capture_on_commit_callbacks
):
    """The InvalidStateTransitionError guard (an already-CANCELLED row) fires
    before the CANCELLED save, so the trigger code is never reached — no
    Notification row, no dispatch."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CANCELLED,
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with tenant_context(salon.id), pytest.raises(InvalidStateTransitionError):
            cancel_appointment(
                appointment_id=appt.id,
                salon=salon,
                cancelled_by=CancelledBy.CUSTOMER,
                now=NOW,
            )

    assert callbacks == []
    assert _notifications(salon, trigger_type=NotificationTrigger.BOOKING_CANCELLED) == []
