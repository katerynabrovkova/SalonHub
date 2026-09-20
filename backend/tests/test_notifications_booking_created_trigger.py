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
from django.conf import settings
from django.core import mail
from django.utils import timezone

from accounts.models import Account, AccountRole
from booking.guest_tokens import derive_guest_token
from booking.models import AppointmentStatus
from booking.services import create_guest_appointment
from core.exceptions import DomainError
from core.tenancy import tenant_context
from notifications.channels.email import EmailChannel
from notifications.models import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationTrigger,
)
from notifications.services import record_and_dispatch_notification, send_notification
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


# --- 4. BOOKING_CREATED link mode is an explicit, required parameter -----
#
# docs/DECISIONS.md § Stage 15 planning, item 5, "Extended 19.09.2026": the
# BOOKING_CREATED link is chosen by an explicit parameter passed from the
# booking-creation call site, never inferred from whether the appointment's
# Customer happens to have a linked Account. Written before
# record_and_dispatch_notification accepts any such parameter -- expected
# red: TypeError (unexpected keyword argument), not an import/collection
# error, since record_and_dispatch_notification itself already exists today.


def test_booking_created_guest_mode_link_is_the_booking_pay_fragment_link(
    salon, customer, specialist, service, django_capture_on_commit_callbacks
):
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
    )

    with django_capture_on_commit_callbacks(execute=True):
        with tenant_context(salon.id):
            record_and_dispatch_notification(
                salon=salon,
                trigger_type=NotificationTrigger.BOOKING_CREATED,
                appointment=appointment,
                dedup_key=f"booking_created:appointment:{appointment.pk}",
                booking_link_mode="guest",
            )

    expected_link = (
        f"{settings.FRONTEND_URL.format(slug=salon.slug)}/booking/pay"
        f"#appointment_id={appointment.id}&token={derive_guest_token(appointment.id)}"
    )
    assert len(mail.outbox) == 1
    assert expected_link in mail.outbox[0].body


def test_booking_created_account_mode_link_is_the_client_page_with_no_token(
    salon, customer, specialist, service, django_capture_on_commit_callbacks
):
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
    )

    with django_capture_on_commit_callbacks(execute=True):
        with tenant_context(salon.id):
            record_and_dispatch_notification(
                salon=salon,
                trigger_type=NotificationTrigger.BOOKING_CREATED,
                appointment=appointment,
                dedup_key=f"booking_created:appointment:{appointment.pk}",
                booking_link_mode="account",
            )

    expected_link = f"{settings.FRONTEND_URL.format(slug=salon.slug)}/client"
    body = mail.outbox[0].body
    assert len(mail.outbox) == 1
    assert expected_link in body
    assert "token=" not in body
    assert derive_guest_token(appointment.id) not in body


def test_booking_created_guest_mode_is_not_inferred_from_a_linked_account(
    salon, customer, specialist, service, django_capture_on_commit_callbacks
):
    """The Customer for this appointment has a linked Account, but the
    caller still explicitly requested guest mode -- the link must stay the
    guest link. The mode is never inferred from Customer.account."""
    with tenant_context(salon.id):
        Account.objects.create_account(
            salon=salon,
            email="linked-but-still-guest@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
            customer=customer,
        )
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
    )

    with django_capture_on_commit_callbacks(execute=True):
        with tenant_context(salon.id):
            record_and_dispatch_notification(
                salon=salon,
                trigger_type=NotificationTrigger.BOOKING_CREATED,
                appointment=appointment,
                dedup_key=f"booking_created:appointment:{appointment.pk}",
                booking_link_mode="guest",
            )

    expected_link = (
        f"{settings.FRONTEND_URL.format(slug=salon.slug)}/booking/pay"
        f"#appointment_id={appointment.id}&token={derive_guest_token(appointment.id)}"
    )
    assert len(mail.outbox) == 1
    assert expected_link in mail.outbox[0].body


# --- 5. BOOKING_CREATED with no/unknown booking_link_mode must raise -----
#
# Design decision: booking_link_mode is keyword-only with default None on
# record_and_dispatch_notification, meaningful only for BOOKING_CREATED. For
# that trigger, None or any unrecognized value must raise ValueError -- never
# silently fall back to the guest link. Other triggers (BOOKING_CANCELLED,
# BOOKING_EXPIRED) don't pass it and are unaffected.


def test_booking_created_without_booking_link_mode_raises(
    salon, customer, specialist, service, django_capture_on_commit_callbacks
):
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
    )

    with tenant_context(salon.id), pytest.raises(ValueError, match="booking_link_mode"):
        record_and_dispatch_notification(
            salon=salon,
            trigger_type=NotificationTrigger.BOOKING_CREATED,
            appointment=appointment,
            dedup_key=f"booking_created:appointment:{appointment.pk}",
        )


def test_booking_created_with_unknown_booking_link_mode_raises(
    salon, customer, specialist, service, django_capture_on_commit_callbacks
):
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
    )

    with tenant_context(salon.id), pytest.raises(ValueError, match="booking_link_mode"):
        record_and_dispatch_notification(
            salon=salon,
            trigger_type=NotificationTrigger.BOOKING_CREATED,
            appointment=appointment,
            dedup_key=f"booking_created:appointment:{appointment.pk}",
            booking_link_mode="acount",
        )


# --- 6. booking_link_mode is persisted on the Notification row, and the
#        deferred send reads it from there, not from the original call -----
#
# The Celery boundary means record_and_dispatch_notification's caller-side
# parameter cannot itself reach _build_message at send time -- it has to be
# persisted on the row. Written before Notification has a booking_link_mode
# field at all -- expected red: TypeError/AttributeError, not an
# import/collection error.


def test_record_and_dispatch_notification_persists_booking_link_mode_on_the_row(
    salon, customer, specialist, service, django_capture_on_commit_callbacks
):
    account_appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
    )
    guest_appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE + dt.timedelta(hours=2),
    )
    cancelled_appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE + dt.timedelta(hours=4),
    )

    with django_capture_on_commit_callbacks(execute=False):
        with tenant_context(salon.id):
            record_and_dispatch_notification(
                salon=salon,
                trigger_type=NotificationTrigger.BOOKING_CREATED,
                appointment=account_appt,
                dedup_key=f"booking_created:appointment:{account_appt.pk}",
                booking_link_mode="account",
            )
            record_and_dispatch_notification(
                salon=salon,
                trigger_type=NotificationTrigger.BOOKING_CREATED,
                appointment=guest_appt,
                dedup_key=f"booking_created:appointment:{guest_appt.pk}",
                booking_link_mode="guest",
            )
            record_and_dispatch_notification(
                salon=salon,
                trigger_type=NotificationTrigger.BOOKING_CANCELLED,
                appointment=cancelled_appt,
                dedup_key=f"booking_cancelled:appointment:{cancelled_appt.pk}",
            )

    with tenant_context(salon.id):
        account_row = Notification.objects.get(
            appointment=account_appt, trigger_type=NotificationTrigger.BOOKING_CREATED
        )
        guest_row = Notification.objects.get(
            appointment=guest_appt, trigger_type=NotificationTrigger.BOOKING_CREATED
        )
        cancelled_row = Notification.objects.get(
            appointment=cancelled_appt, trigger_type=NotificationTrigger.BOOKING_CANCELLED
        )

    assert account_row.booking_link_mode == "account"
    assert guest_row.booking_link_mode == "guest"
    assert cancelled_row.booking_link_mode is None


def test_send_notification_builds_the_link_from_the_row_not_the_original_call(
    salon, customer, specialist, service
):
    """Simulates the Celery task's own call shape (send_notification with
    only notification_id/salon/channel/now -- no booking_link_mode
    argument at all) to prove the link is read back from the persisted row,
    not carried forward from whatever called record_and_dispatch_notification."""
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
    )
    with tenant_context(salon.id):
        notification = Notification.objects.create(
            salon=salon,
            trigger_type=NotificationTrigger.BOOKING_CREATED,
            channel=NotificationChannel.EMAIL,
            appointment=appointment,
            customer_id=appointment.customer_id,
            dedup_key=f"booking_created:appointment:{appointment.pk}",
            booking_link_mode="account",
        )

        send_notification(
            notification_id=notification.id,
            salon=salon,
            channel=EmailChannel(),
            now=timezone.now(),
        )

    expected_link = f"{settings.FRONTEND_URL.format(slug=salon.slug)}/client"
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert expected_link in body
    assert "token=" not in body
    assert derive_guest_token(appointment.id) not in body
