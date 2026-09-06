"""
Stage 9 step (b.4.1) — notifications.services (docs/ARCHITECTURE.md § 9;
docs/DECISIONS.md § Stage 9 decisions, step (b)). Real-DB: these functions
lock and transition a Notification row.

Written before notifications/services.py exists — expected to fail on
collection (ModuleNotFoundError: No module named 'notifications.services')
until it is added, the same two-step red shape as the other Stage 9 tests.

The service requires tenant context to already be bound (same convention
as flag_stuck_refunds); each test binds it explicitly. The Celery task
that owns the cross-salon loop and the binding is step b.4.2.
MockNotificationChannel stands in for a real channel.
"""

import datetime as dt

import pytest
from django.conf import settings

from booking.guest_tokens import derive_guest_token
from core.formatting import format_datetime_for_salon
from core.tenancy import tenant_context
from notifications.channels.base import NotificationChannel
from notifications.channels.mock import MockNotificationChannel
from notifications.models import Notification, NotificationStatus, NotificationTrigger
from notifications.services import (
    _build_message,
    _resolve_recipient,
    mark_notification_failed,
    send_notification,
)
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 9, 3, 12, 0, tzinfo=dt.UTC)
EARLIER = dt.datetime(2026, 9, 1, 8, 0, tzinfo=dt.UTC)
APPOINTMENT_START = dt.datetime(2026, 9, 26, 11, 0, tzinfo=dt.UTC)

# BOOKING_CONFIRMED excluded: it is no longer a static _MESSAGES lookup (it
# now builds a subject/body from a real appointment), so it has its own
# dedicated tests below instead of running through this generic check.
_IMPLEMENTED_TRIGGERS = [
    NotificationTrigger.BOOKING_CANCELLED,
    NotificationTrigger.APPOINTMENT_REMINDER,
    NotificationTrigger.PAYMENT_SUCCEEDED,
    NotificationTrigger.PAYMENT_FAILED,
    NotificationTrigger.REVIEW_REQUEST,
]


class _RaisingChannel(NotificationChannel):
    def send(self, *, recipient: str, subject: str, body: str) -> None:
        raise RuntimeError("smtp down")


def _make_notification(
    salon,
    *,
    trigger=NotificationTrigger.BOOKING_CONFIRMED,
    customer=None,
    appointment=None,
    status=NotificationStatus.PENDING,
    sent_at=None,
    dedup_key="k",
):
    with tenant_context(salon.id):
        return Notification.objects.create(
            salon=salon,
            customer=customer,
            appointment=appointment,
            trigger_type=trigger,
            channel="email",  # NotificationChannel.EMAIL value; the channel enum isn't under test
            dedup_key=dedup_key,
            status=status,
            sent_at=sent_at,
        )


def _reload(salon, notification_id):
    with tenant_context(salon.id):
        return Notification.objects.get(pk=notification_id)


# --- _resolve_recipient --------------------------------------------------


def test_resolve_recipient_returns_customer_email_when_customer_is_set(salon, customer):
    notification = _make_notification(salon, customer=customer)
    with tenant_context(salon.id):
        assert _resolve_recipient(notification) == customer.email


def test_resolve_recipient_returns_salon_contact_email_when_customer_is_null(salon):
    notification = _make_notification(salon, customer=None)
    with tenant_context(salon.id):
        assert _resolve_recipient(notification) == salon.contact_email


# --- _build_message (the localization seam) -----------------------------


@pytest.mark.parametrize("trigger", _IMPLEMENTED_TRIGGERS)
def test_build_message_returns_nonempty_subject_and_body(trigger):
    subject, body = _build_message(Notification(trigger_type=trigger))
    assert subject
    assert body


def test_build_message_raises_for_an_unimplemented_trigger():
    with pytest.raises(ValueError, match="trigger_type"):
        _build_message(Notification(trigger_type=NotificationTrigger.EMAIL_VERIFICATION))


def test_build_message_for_booking_confirmed_builds_subject_body_and_link(
    salon, customer, specialist, service
):
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=APPOINTMENT_START,
    )
    notification = _make_notification(salon, customer=customer, appointment=appointment)

    with tenant_context(salon.id):
        subject, body = _build_message(notification)

    assert subject == "Your booking is confirmed"
    assert salon.name in body
    assert format_datetime_for_salon(appointment.start_datetime, salon.timezone) in body
    manage_link = (
        f"{settings.FRONTEND_URL}/salons/{salon.slug}/appointments/{appointment.id}"
        f"/manage/{derive_guest_token(appointment.id)}/"
    )
    assert manage_link in body
    assert "View or cancel" in body


def test_build_message_for_booking_confirmed_link_carries_the_re_derived_token(
    salon, customer, specialist, service
):
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=APPOINTMENT_START,
    )
    notification = _make_notification(salon, customer=customer, appointment=appointment)

    with tenant_context(salon.id):
        _subject, body = _build_message(notification)

    expected_token = derive_guest_token(appointment.id)
    expected_link = (
        f"{settings.FRONTEND_URL}/salons/{salon.slug}/appointments/{appointment.id}"
        f"/manage/{expected_token}/"
    )
    assert expected_link in body


def test_build_message_raises_for_booking_confirmed_without_an_appointment(salon, customer):
    notification = _make_notification(salon, customer=customer, appointment=None)

    with tenant_context(salon.id), pytest.raises(ValueError, match="appointment"):
        _build_message(notification)


# --- send_notification -------------------------------------------------


def test_send_notification_transitions_pending_to_sent_and_delivers_to_the_client(
    salon, customer, specialist, service
):
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=APPOINTMENT_START,
    )
    notification = _make_notification(salon, customer=customer, appointment=appointment)
    channel = MockNotificationChannel()

    with tenant_context(salon.id):
        send_notification(notification_id=notification.id, salon=salon, channel=channel, now=NOW)

    reloaded = _reload(salon, notification.id)
    assert reloaded.status == NotificationStatus.SENT
    assert reloaded.sent_at == NOW
    subject, body = _build_message(notification)
    assert channel.sent == [(customer.email, subject, body)]


def test_send_notification_delivers_to_salon_contact_email_for_a_salon_recipient(
    salon, customer, specialist, service
):
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=APPOINTMENT_START,
    )
    notification = _make_notification(salon, customer=None, appointment=appointment)
    channel = MockNotificationChannel()

    with tenant_context(salon.id):
        send_notification(notification_id=notification.id, salon=salon, channel=channel, now=NOW)

    assert len(channel.sent) == 1
    assert channel.sent[0][0] == salon.contact_email


def test_send_notification_is_a_noop_when_the_row_is_already_sent(salon, customer):
    notification = _make_notification(
        salon, customer=customer, status=NotificationStatus.SENT, sent_at=EARLIER
    )
    channel = MockNotificationChannel()

    with tenant_context(salon.id):
        send_notification(notification_id=notification.id, salon=salon, channel=channel, now=NOW)

    assert channel.sent == []
    reloaded = _reload(salon, notification.id)
    assert reloaded.status == NotificationStatus.SENT
    assert reloaded.sent_at == EARLIER


def test_send_notification_is_a_noop_when_the_row_is_already_failed(salon, customer):
    notification = _make_notification(salon, customer=customer, status=NotificationStatus.FAILED)
    channel = MockNotificationChannel()

    with tenant_context(salon.id):
        send_notification(notification_id=notification.id, salon=salon, channel=channel, now=NOW)

    assert channel.sent == []
    assert _reload(salon, notification.id).status == NotificationStatus.FAILED


def test_send_notification_propagates_a_channel_failure_and_leaves_the_row_pending(salon, customer):
    # PAYMENT_SUCCEEDED, not BOOKING_CONFIRMED: this test is about channel
    # failure handling, not message building, and PAYMENT_SUCCEEDED stays a
    # static _MESSAGES lookup that needs no appointment.
    notification = _make_notification(
        salon, customer=customer, trigger=NotificationTrigger.PAYMENT_SUCCEEDED
    )

    with tenant_context(salon.id), pytest.raises(RuntimeError, match="smtp down"):
        send_notification(
            notification_id=notification.id, salon=salon, channel=_RaisingChannel(), now=NOW
        )

    reloaded = _reload(salon, notification.id)
    assert reloaded.status == NotificationStatus.PENDING
    assert reloaded.sent_at is None


def test_send_notification_other_salon_row_raises_does_not_exist(salon, other_salon, customer):
    notification = _make_notification(salon, customer=customer)
    channel = MockNotificationChannel()

    with tenant_context(other_salon.id), pytest.raises(Notification.DoesNotExist):
        send_notification(
            notification_id=notification.id, salon=other_salon, channel=channel, now=NOW
        )

    assert channel.sent == []


# --- mark_notification_failed ----------------------------------------


def test_mark_notification_failed_transitions_pending_to_failed(salon, customer):
    notification = _make_notification(salon, customer=customer)

    with tenant_context(salon.id):
        mark_notification_failed(notification_id=notification.id, salon=salon)

    assert _reload(salon, notification.id).status == NotificationStatus.FAILED


def test_mark_notification_failed_is_a_noop_when_the_row_is_already_sent(salon, customer):
    notification = _make_notification(
        salon, customer=customer, status=NotificationStatus.SENT, sent_at=EARLIER
    )

    with tenant_context(salon.id):
        mark_notification_failed(notification_id=notification.id, salon=salon)

    reloaded = _reload(salon, notification.id)
    assert reloaded.status == NotificationStatus.SENT
    assert reloaded.sent_at == EARLIER
