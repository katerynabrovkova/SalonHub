"""
Which emails the payment webhook records (docs/DECISIONS.md "Notifications",
decided 11.11.2026; register "In progress: booking emails", sub-step (a)):

- `PAYMENT_SUCCEEDED` is not sent: a confirming payment records only
  `BOOKING_CONFIRMED`.
- `PAYMENT_FAILED` is sent only while the appointment is `PENDING_PAYMENT`;
  an expired or cancelled appointment's customer already has that email.

Real-DB integration tests over PaymentWebhookView with its default
MockPaymentProvider (accepts any signature, no network).
CELERY_TASK_ALWAYS_EAGER is True in tests, so a dispatched send runs inline
once the captured on_commit callbacks execute.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.core import mail
from rest_framework.test import APIClient

from booking.models import Appointment, AppointmentStatus
from core.tenancy import tenant_context
from notifications.models import Notification, NotificationTrigger
from payments.models import Payment, PaymentStatus
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 8, 22, 10, 0, tzinfo=dt.UTC)


def _booking(salon, specialist, service, customer, *, status, ref: str) -> Appointment:
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=status,
    )
    with tenant_context(salon.id):
        Payment.objects.create(
            salon=salon,
            appointment=appointment,
            amount=Decimal("100.00"),
            currency=salon.currency,
            status=PaymentStatus.PENDING,
            provider_reference_id=ref,
        )
    return appointment


def _post(event_id: str, event_type: str, ref: str):
    return APIClient().post(
        "/api/v1/webhooks/payments/",
        {"event_id": event_id, "event_type": event_type, "provider_reference_id": ref},
        format="json",
        HTTP_X_SIGNATURE="sig",
    )


def _trigger_types(salon) -> list[str]:
    with tenant_context(salon.id):
        return sorted(Notification.objects.values_list("trigger_type", flat=True))


def test_confirming_payment_records_only_booking_confirmed(
    salon, specialist, service, customer, django_capture_on_commit_callbacks
):
    _booking(
        salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT, ref="r1"
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = _post("e1", "payment_succeeded", "r1")

    assert response.status_code == 200
    assert _trigger_types(salon) == [NotificationTrigger.BOOKING_CONFIRMED]
    assert [m.to for m in mail.outbox] == [[customer.email]]


@pytest.mark.parametrize(
    "status",
    [AppointmentStatus.EXPIRED, AppointmentStatus.CANCELLED],
    ids=["expired", "cancelled"],
)
def test_payment_failed_is_not_recorded_once_the_appointment_is_no_longer_pending_payment(
    salon, specialist, service, customer, django_capture_on_commit_callbacks, status
):
    appointment = _booking(salon, specialist, service, customer, status=status, ref="r2")

    with django_capture_on_commit_callbacks(execute=True):
        response = _post("e2", "payment_failed", "r2")

    assert response.status_code == 200
    assert _trigger_types(salon) == []
    assert mail.outbox == []
    # The Payment transition itself is unchanged.
    with tenant_context(salon.id):
        assert Payment.objects.get(appointment=appointment).status == PaymentStatus.FAILED
        assert Appointment.objects.get(pk=appointment.pk).status == status


def test_payment_failed_is_still_recorded_while_pending_payment(
    salon, specialist, service, customer, django_capture_on_commit_callbacks
):
    """Control for the test above: the PENDING_PAYMENT case keeps its email."""
    _booking(
        salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT, ref="r3"
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = _post("e3", "payment_failed", "r3")

    assert response.status_code == 200
    assert _trigger_types(salon) == [NotificationTrigger.PAYMENT_FAILED]
    assert [m.to for m in mail.outbox] == [[customer.email]]
