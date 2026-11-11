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

from accounts.models import Customer
from booking.guest_tokens import derive_guest_token, issue_guest_token
from booking.models import AppointmentStatus
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
from tests.conftest import make_appointment as _conftest_make_appointment

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 9, 3, 12, 0, tzinfo=dt.UTC)
EARLIER = dt.datetime(2026, 9, 1, 8, 0, tzinfo=dt.UTC)
APPOINTMENT_START = dt.datetime(2026, 9, 26, 11, 0, tzinfo=dt.UTC)

# Every booking email is excluded: none is a static _MESSAGES lookup — each
# builds a subject/body from a real appointment (salon name, local start
# time, a link, money), so each has its own dedicated tests instead of
# running through this generic check. REVIEW_REQUEST is the only static
# entry left.
_IMPLEMENTED_TRIGGERS = [
    NotificationTrigger.REVIEW_REQUEST,
]


def make_appointment(**kwargs):
    """conftest's make_appointment, reloaded from the database: the booking
    emails read the money snapshots, which the fixture service holds as an
    unsaved string price until the row is read back."""
    appointment = _conftest_make_appointment(**kwargs)
    with tenant_context(appointment.salon_id):
        appointment.refresh_from_db()
    return appointment


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
    booking_link_mode=None,
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
            booking_link_mode=booking_link_mode,
        )


def _reload(salon, notification_id):
    with tenant_context(salon.id):
        return Notification.objects.get(pk=notification_id)


@pytest.fixture(autouse=True)
def _subdomain_frontend_url(settings):
    # Guest links are subdomain-based: FRONTEND_URL carries a `{slug}`
    # placeholder filled per salon via `.format(slug=...)` — see
    # docs/DECISIONS.md § "Frontend routing: subdomain-based". Mirrors the
    # production `.env` pattern (`FRONTEND_URL=http://{slug}.localhost:3000`).
    settings.FRONTEND_URL = "http://{slug}.testserver"


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
    subject, body = _build_message(Notification(trigger_type=trigger), now=NOW)
    assert subject
    assert body


@pytest.mark.parametrize(
    "trigger",
    [NotificationTrigger.EMAIL_VERIFICATION, NotificationTrigger.PAYMENT_SUCCEEDED],
    ids=["email_verification", "payment_succeeded"],
)
def test_build_message_raises_for_an_unimplemented_trigger(trigger):
    """PAYMENT_SUCCEEDED is no longer sent (docs/DECISIONS.md "Notifications")
    and has no text: its enum value stays only for existing rows."""
    with pytest.raises(ValueError, match="trigger_type"):
        _build_message(Notification(trigger_type=trigger), now=NOW)


def _guest_appointment(salon, customer, specialist, service):
    """An appointment with the guest token row a guest booking has, so the
    BOOKING_CONFIRMED / APPOINTMENT_REMINDER link is the manage page
    (docs/DECISIONS.md "Notifications": without a row the link is /client)."""
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=APPOINTMENT_START,
    )
    with tenant_context(salon.id):
        issue_guest_token(appointment)
    return appointment


def _manage_link(salon, appointment) -> str:
    return (
        f"{settings.FRONTEND_URL.format(slug=salon.slug)}/appointments/{appointment.id}"
        f"/manage#token={derive_guest_token(appointment.id)}"
    )


def test_build_message_for_booking_confirmed_builds_subject_body_and_link(
    salon, customer, specialist, service
):
    appointment = _guest_appointment(salon, customer, specialist, service)
    notification = _make_notification(salon, customer=customer, appointment=appointment)

    with tenant_context(salon.id):
        subject, body = _build_message(notification, now=NOW)

    assert subject == "Your booking at Bella Demo Salon is confirmed"
    assert "Bella Demo Salon" in body
    # 2026-09-26 11:00 UTC is Saturday 14:00 in Kyiv (EEST).
    assert "When: Saturday, 26 September 2026, 14:00" in body.splitlines()
    assert _manage_link(salon, appointment) in body.splitlines()
    assert "View or cancel your booking:" in body.splitlines()


def test_build_message_for_booking_confirmed_link_carries_the_re_derived_token(
    salon, customer, specialist, service
):
    appointment = _guest_appointment(salon, customer, specialist, service)
    notification = _make_notification(salon, customer=customer, appointment=appointment)

    with tenant_context(salon.id):
        _subject, body = _build_message(notification, now=NOW)

    expected_token = derive_guest_token(appointment.id)
    expected_link = (
        f"{settings.FRONTEND_URL.format(slug=salon.slug)}/appointments/{appointment.id}"
        f"/manage#token={expected_token}"
    )
    assert expected_link in body.splitlines()


def test_build_message_raises_for_booking_confirmed_without_an_appointment(salon, customer):
    notification = _make_notification(salon, customer=customer, appointment=None)

    with tenant_context(salon.id), pytest.raises(ValueError, match="appointment"):
        _build_message(notification, now=NOW)


# --- _build_message for APPOINTMENT_REMINDER (dynamic, mirrors BOOKING_CONFIRMED)


def test_build_message_for_appointment_reminder_builds_subject_body_and_link(
    salon, customer, specialist, service
):
    appointment = _guest_appointment(salon, customer, specialist, service)
    notification = _make_notification(
        salon,
        customer=customer,
        appointment=appointment,
        trigger=NotificationTrigger.APPOINTMENT_REMINDER,
    )

    with tenant_context(salon.id):
        subject, body = _build_message(notification, now=NOW)

    assert subject == "Reminder: tomorrow at 14:00 at Bella Demo Salon"
    # The exact body (docs/DECISIONS.md "Notifications", texts of
    # APPOINTMENT_REMINDER). `salon.name` resolves to its English value for a
    # customer with no preferred_language. No Payment here, so no "Pay at the
    # visit" line; the fixture salon has no phone or address, so no contact
    # line and the short last line.
    expected_body = "\n".join(
        [
            "Hello, Alice!",
            "This is a reminder of your booking tomorrow:",
            "Service: Manicure",
            "Specialist: Jane",
            "When: Saturday, 26 September 2026, 14:00",
            "If your plans have changed, you can cancel your booking here:",
            _manage_link(salon, appointment),
            "Bella Demo Salon",
            "If you did not book at Bella Demo Salon, please let the salon know.",
        ]
    )
    assert body == expected_body


def test_build_message_for_appointment_reminder_link_carries_the_re_derived_token(
    salon, customer, specialist, service
):
    appointment = _guest_appointment(salon, customer, specialist, service)
    notification = _make_notification(
        salon,
        customer=customer,
        appointment=appointment,
        trigger=NotificationTrigger.APPOINTMENT_REMINDER,
    )

    with tenant_context(salon.id):
        _subject, body = _build_message(notification, now=NOW)

    expected_token = derive_guest_token(appointment.id)
    expected_link = (
        f"{settings.FRONTEND_URL.format(slug=salon.slug)}/appointments/{appointment.id}"
        f"/manage#token={expected_token}"
    )
    assert expected_link in body.splitlines()


def test_build_message_raises_for_appointment_reminder_without_an_appointment(salon, customer):
    notification = _make_notification(
        salon,
        customer=customer,
        appointment=None,
        trigger=NotificationTrigger.APPOINTMENT_REMINDER,
    )

    with tenant_context(salon.id), pytest.raises(ValueError, match="appointment"):
        _build_message(notification, now=NOW)


# --- _build_message for BOOKING_CREATED (dynamic, mirrors BOOKING_CONFIRMED) --
#
# docs/DECISIONS.md "Booking > Guest token": the payment link uses a URL
# fragment, never a query parameter or a path segment —
# build_salon_frontend_url(slug, "/booking/pay") +
# f"#appointment_id={id}&token={token}" — the same fragment form as
# BOOKING_CONFIRMED/APPOINTMENT_REMINDER's manage link
# (`/appointments/<id>/manage#token=<token>`).


def test_build_message_for_booking_created_builds_subject_body_and_payment_link(
    salon, customer, specialist, service
):
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=APPOINTMENT_START,
    )
    notification = _make_notification(
        salon,
        customer=customer,
        appointment=appointment,
        trigger=NotificationTrigger.BOOKING_CREATED,
        booking_link_mode="guest",
    )

    with tenant_context(salon.id):
        subject, body = _build_message(notification, now=NOW)

    assert subject == "Your booking at Bella Demo Salon is created, the deposit is still to be paid"
    lines = body.splitlines()
    assert "Bella Demo Salon" in lines
    assert "When: Saturday, 26 September 2026, 14:00" in lines
    payment_link = (
        f"{settings.FRONTEND_URL.format(slug=salon.slug)}/booking/pay"
        f"#appointment_id={appointment.id}&token={derive_guest_token(appointment.id)}"
    )
    assert lines[lines.index("Pay the deposit:") + 1] == payment_link


def test_build_message_for_booking_created_link_carries_the_re_derived_token(
    salon, customer, specialist, service
):
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=APPOINTMENT_START,
    )
    notification = _make_notification(
        salon,
        customer=customer,
        appointment=appointment,
        trigger=NotificationTrigger.BOOKING_CREATED,
        booking_link_mode="guest",
    )

    with tenant_context(salon.id):
        _subject, body = _build_message(notification, now=NOW)

    expected_token = derive_guest_token(appointment.id)
    expected_link = (
        f"{settings.FRONTEND_URL.format(slug=salon.slug)}/booking/pay"
        f"#appointment_id={appointment.id}&token={expected_token}"
    )
    assert expected_link in body


def test_build_message_raises_for_booking_created_without_an_appointment(salon, customer):
    notification = _make_notification(
        salon,
        customer=customer,
        appointment=None,
        trigger=NotificationTrigger.BOOKING_CREATED,
    )

    with tenant_context(salon.id), pytest.raises(ValueError, match="appointment"):
        _build_message(notification, now=NOW)


# --- _build_message language resolution (docs/DECISIONS.md § "Notification
# message builder: language resolution") ------------------------------------


def _uk_customer(salon):
    with tenant_context(salon.id):
        return Customer.objects.create(
            salon=salon,
            name="Оля",
            email="olya@example.com",
            phone="+380000000000",
            preferred_language="uk",
        )


def test_build_message_for_booking_cancelled_renders_in_customer_preferred_language(
    salon, specialist, service
):
    customer = _uk_customer(salon)
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=APPOINTMENT_START,
        status=AppointmentStatus.CANCELLED,
    )
    notification = _make_notification(
        salon,
        customer=customer,
        appointment=appointment,
        trigger=NotificationTrigger.BOOKING_CANCELLED,
    )

    with tenant_context(salon.id):
        subject, body = _build_message(notification, now=NOW)

    assert subject == "Ваш запис у Bella Demo Salon скасовано"
    lines = body.splitlines()
    assert lines[:2] == ["Вітаємо, Оля!", "Ваш запис скасовано:"]
    assert "Записатися знову:" in lines


def test_build_message_dynamic_branch_renders_in_customer_preferred_language(
    salon, specialist, service
):
    customer = _uk_customer(salon)
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=APPOINTMENT_START,
    )
    notification = _make_notification(
        salon,
        customer=customer,
        appointment=appointment,
        trigger=NotificationTrigger.BOOKING_CONFIRMED,
    )

    with tenant_context(salon.id):
        subject, body = _build_message(notification, now=NOW)

    # The fixture salon has only an English name, so it resolves to that.
    assert subject == "Ваш запис у Bella Demo Salon підтверджено"
    assert "підтверджено" in body
    assert "Переглянути або скасувати запис:" in body
    # The interpolated datetime is rendered with the Ukrainian full tables too.
    assert "Коли: субота, 26 вересня 2026, 14:00" in body.splitlines()


def _expired_appointment(salon, customer, specialist, service):
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=APPOINTMENT_START,
        status=AppointmentStatus.EXPIRED,
    )


def test_build_message_for_booking_expired_renders_in_customer_preferred_language(
    salon, specialist, service
):
    customer = _uk_customer(salon)
    appointment = _expired_appointment(salon, customer, specialist, service)
    notification = _make_notification(
        salon,
        customer=customer,
        appointment=appointment,
        trigger=NotificationTrigger.BOOKING_EXPIRED,
    )

    with tenant_context(salon.id):
        subject, body = _build_message(notification, now=NOW)

    assert subject == "Запис у Bella Demo Salon не підтверджено"
    assert body.splitlines()[:2] == [
        "Вітаємо, Оля!",
        "Час на оплату минув, тому запис не підтверджено і цей час звільнено:",
    ]


def test_build_message_for_booking_expired_renders_in_english_by_default(
    salon, customer, specialist, service
):
    """A notification with no recipient Customer has no language preference,
    so it falls back to English."""
    appointment = _expired_appointment(salon, customer, specialist, service)
    notification = _make_notification(
        salon, customer=None, appointment=appointment, trigger=NotificationTrigger.BOOKING_EXPIRED
    )

    with tenant_context(salon.id):
        subject, body = _build_message(notification, now=NOW)

    assert subject == "Your booking at Bella Demo Salon was not confirmed"
    assert body.splitlines()[:2] == [
        "Hello, Alice!",
        "The time to pay has run out, so the booking was not confirmed and this time has "
        "been released:",
    ]


def test_build_message_for_booking_created_renders_in_customer_preferred_language(
    salon, specialist, service
):
    customer = _uk_customer(salon)
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=APPOINTMENT_START,
    )
    notification = _make_notification(
        salon,
        customer=customer,
        appointment=appointment,
        trigger=NotificationTrigger.BOOKING_CREATED,
        booking_link_mode="guest",
    )

    with tenant_context(salon.id):
        subject, body = _build_message(notification, now=NOW)

    # The fixture salon has only an English name, so it resolves to that.
    assert subject == "Ваш запис у Bella Demo Salon створено, залишилось оплатити"
    lines = body.splitlines()
    assert "Ваш запис створено:" in lines
    assert "Оплатити передоплату:" in lines
    # The interpolated datetime is rendered with the Ukrainian full tables too.
    assert "Коли: субота, 26 вересня 2026, 14:00" in lines


def test_build_message_customer_less_notification_builds_in_english(salon):
    notification = _make_notification(
        salon, customer=None, trigger=NotificationTrigger.REVIEW_REQUEST
    )

    with tenant_context(salon.id):
        subject, body = _build_message(notification, now=NOW)

    assert subject == "How was your visit?"
    assert body == "We would love your feedback on your recent visit."


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
    subject, body = _build_message(notification, now=NOW)
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
    # REVIEW_REQUEST, not BOOKING_CONFIRMED: this test is about channel
    # failure handling, not message building, and REVIEW_REQUEST stays a
    # static _MESSAGES lookup that needs no appointment.
    notification = _make_notification(
        salon, customer=customer, trigger=NotificationTrigger.REVIEW_REQUEST
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
