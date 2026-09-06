"""
Stage 9 step (e) part 1 — the appointment-reminder sweep service function
(docs/DECISIONS.md § Step (e) decisions). Real-DB integration tests over
notifications.services.send_due_appointment_reminders directly — service
level, the same discipline as test_notifications_cancellation_trigger.py.

Written before send_due_appointment_reminders exists — expected red: the
import of the service function fails until it is implemented.

Tests run under plain @pytest.mark.django_db, so transaction.on_commit
never fires on its own; django_capture_on_commit_callbacks drives it
explicitly, the same pattern test_notifications_cancellation_trigger.py and
test_notifications_webhook_triggers.py established. `now` is a fixed literal
datetime, never timezone.now(), and every appointment start is computed as
an offset from it.
"""

import datetime as dt

import pytest
from django.core import mail

from booking.models import AppointmentStatus
from core.tenancy import tenant_context
from notifications.models import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationTrigger,
)
from notifications.services import send_due_appointment_reminders
from specialists.models import Specialist
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 9, 6, 12, 0, tzinfo=dt.UTC)

# Window is (NOW + 24h, NOW + 25h] — lower bound strict, upper bound inclusive.
AT_24H = NOW + dt.timedelta(hours=24)  # exactly the lower bound -> NOT reminded
JUST_INSIDE_LOWER = NOW + dt.timedelta(hours=24, minutes=1)  # -> reminded
AT_25H = NOW + dt.timedelta(hours=25)  # exactly the upper bound -> reminded
JUST_OUTSIDE_UPPER = NOW + dt.timedelta(hours=25, minutes=1)  # -> NOT reminded
MID_WINDOW = NOW + dt.timedelta(hours=24, minutes=30)  # comfortably inside


def _reminder_rows(salon):
    with tenant_context(salon.id):
        return list(
            Notification.objects.filter(trigger_type=NotificationTrigger.APPOINTMENT_REMINDER)
        )


def _new_specialist(salon, name):
    with tenant_context(salon.id):
        return Specialist.objects.create(salon=salon, name=name)


# --- window boundary tests ---------------------------------------------------


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (AT_24H, 0),
        (JUST_INSIDE_LOWER, 1),
        (MID_WINDOW, 1),
        (AT_25H, 1),
        (JUST_OUTSIDE_UPPER, 0),
    ],
    ids=["at-now+24h-excluded", "now+24h+1min", "mid-window", "at-now+25h-included", "now+25h+1min"],
)
def test_window_bounds_lower_strict_upper_inclusive(
    salon, specialist, service, customer, django_capture_on_commit_callbacks, start, expected
):
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.CONFIRMED,
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with tenant_context(salon.id):
            dispatched = send_due_appointment_reminders(salon=salon, now=NOW)

    assert dispatched == expected
    assert len(callbacks) == expected
    assert len(_reminder_rows(salon)) == expected
    assert len(mail.outbox) == expected


# --- status filter tests ---------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        AppointmentStatus.PENDING_PAYMENT,
        AppointmentStatus.CANCELLED,
        AppointmentStatus.EXPIRED,
        AppointmentStatus.COMPLETED,
        AppointmentStatus.NO_SHOW,
    ],
)
def test_non_confirmed_appointment_in_window_is_skipped(
    salon, specialist, service, customer, django_capture_on_commit_callbacks, status
):
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=MID_WINDOW,
        status=status,
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with tenant_context(salon.id):
            dispatched = send_due_appointment_reminders(salon=salon, now=NOW)

    assert dispatched == 0
    assert callbacks == []
    assert _reminder_rows(salon) == []
    assert mail.outbox == []


def test_confirmed_appointment_in_window_is_reminded(
    salon, specialist, service, customer, django_capture_on_commit_callbacks
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=MID_WINDOW,
        status=AppointmentStatus.CONFIRMED,
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with tenant_context(salon.id):
            dispatched = send_due_appointment_reminders(salon=salon, now=NOW)

    assert dispatched == 1
    assert len(callbacks) == 1

    rows = _reminder_rows(salon)
    assert len(rows) == 1
    row = rows[0]
    assert row.trigger_type == NotificationTrigger.APPOINTMENT_REMINDER
    assert row.channel == NotificationChannel.EMAIL
    assert row.appointment_id == appt.id
    assert row.customer_id == customer.id
    assert row.dedup_key == f"appointment_reminder:appointment:{appt.pk}"
    assert row.status == NotificationStatus.SENT  # dispatched + delivered
    assert [m.to for m in mail.outbox] == [[customer.email]]


# --- idempotency ---------------------------------------------------------


def test_running_the_sweep_twice_reminds_once(
    salon, specialist, service, customer, django_capture_on_commit_callbacks
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=MID_WINDOW,
        status=AppointmentStatus.CONFIRMED,
    )

    with django_capture_on_commit_callbacks(execute=True) as first_callbacks:
        with tenant_context(salon.id):
            first = send_due_appointment_reminders(salon=salon, now=NOW)

    assert first == 1
    assert len(first_callbacks) == 1

    with django_capture_on_commit_callbacks(execute=True) as second_callbacks:
        with tenant_context(salon.id):
            second = send_due_appointment_reminders(salon=salon, now=NOW)

    assert second == 0  # no-op
    assert second_callbacks == []

    rows = _reminder_rows(salon)
    assert len(rows) == 1
    assert rows[0].dedup_key == f"appointment_reminder:appointment:{appt.pk}"
    assert len(mail.outbox) == 1  # not re-sent


# --- return value / mixed batch ---------------------------------------------


def test_return_value_equals_number_of_reminders_dispatched(
    salon, specialist, service, customer, django_capture_on_commit_callbacks
):
    # Two due CONFIRMED appointments, each on its own specialist so the
    # double-booking exclusion constraint does not fire.
    due_a = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=JUST_INSIDE_LOWER,
        status=AppointmentStatus.CONFIRMED,
    )
    due_b = make_appointment(
        salon=salon,
        customer=customer,
        specialist=_new_specialist(salon, "Kate"),
        service=service,
        start=AT_25H,
        status=AppointmentStatus.CONFIRMED,
    )
    # Not due: outside the window.
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=_new_specialist(salon, "Lena"),
        service=service,
        start=JUST_OUTSIDE_UPPER,
        status=AppointmentStatus.CONFIRMED,
    )
    # Not due: in the window but CANCELLED.
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=_new_specialist(salon, "Mara"),
        service=service,
        start=MID_WINDOW,
        status=AppointmentStatus.CANCELLED,
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with tenant_context(salon.id):
            dispatched = send_due_appointment_reminders(salon=salon, now=NOW)

    assert dispatched == 2
    assert len(callbacks) == 2

    rows = _reminder_rows(salon)
    assert {r.appointment_id for r in rows} == {due_a.id, due_b.id}
    assert all(r.status == NotificationStatus.SENT for r in rows)
    assert len(mail.outbox) == 2
