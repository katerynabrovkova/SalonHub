"""
Stage 15 planning, item 4, Part B — POST appointments/<id>/cancel/
(docs/DECISIONS.md § Stage 15 planning): the authenticated Account cancels
its own Customer's appointment.

    POST /api/v1/salons/<slug>/appointments/<appointment_id>/cancel/

Fixtures mirror test_booking_account_appointment_list.py's Account path
(``Account.objects.create_account(customer=...)`` + ``client.force_authenticate``)
and test_guest_appointment_pay.py's provider-substitution contract
(``monkeypatch.setattr(AccountAppointmentCancelView, "provider_class", ...)``).

Every boundary of the ≥24h refund-eligibility rule itself is already covered
exhaustively at the service layer in test_booking_cancel_appointment.py; the
one test here proves this endpoint actually reuses cancel_appointment for
that decision (a SUCCEEDED payment ≥24h before start triggers a refund
attempt through this endpoint too), not a full re-run of that matrix.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from booking.models import Appointment, AppointmentStatus, CancelledBy
from booking.views import AccountAppointmentCancelView
from core.tenancy import tenant_context
from payments.models import Payment, PaymentStatus
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _cancel_url(salon, appointment) -> str:
    return f"/api/v1/salons/{salon.slug}/appointments/{appointment.id}/cancel/"


@pytest.fixture
def customer_account(salon, customer):
    with tenant_context(salon.id):
        return Account.objects.create_account(
            salon=salon,
            email="alice-account@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
            customer=customer,
        )


# --- 1. An Account can cancel its own Customer's appointment ---------------


def test_account_can_cancel_its_own_customers_appointment(
    client, salon, customer, specialist, service, customer_account
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )

    client.force_authenticate(user=customer_account)
    response = client.post(_cancel_url(salon, appt))

    assert response.status_code == 200
    assert response.data["id"] == appt.id
    assert response.data["status"] == AppointmentStatus.CANCELLED
    assert response.data["cancelled_by"] == CancelledBy.CUSTOMER

    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.CANCELLED
    assert row.cancelled_by == CancelledBy.CUSTOMER


# --- 2. Refund eligibility reuses cancel_appointment's own rule ------------


@dataclass(frozen=True)
class _FakeRefundIntent:
    provider_reference_id: str


class _FakeRefundProvider:
    calls: ClassVar[list[dict]] = []

    def start_payment(self, *, amount, currency, reference):
        raise NotImplementedError("not exercised by cancel endpoint tests")

    def refund(self, *, provider_reference_id, reference, amount, currency):
        type(self).calls.append(
            {
                "provider_reference_id": provider_reference_id,
                "reference": reference,
                "amount": amount,
                "currency": currency,
            }
        )
        return _FakeRefundIntent(provider_reference_id=f"fake_refund_{len(type(self).calls)}")


@pytest.fixture(autouse=True)
def _reset_fake_provider_calls():
    _FakeRefundProvider.calls = []
    yield
    _FakeRefundProvider.calls = []


def test_cancelling_at_least_24h_before_start_with_a_succeeded_payment_triggers_a_refund(
    client, salon, customer, specialist, service, customer_account, monkeypatch
):
    monkeypatch.setattr(AccountAppointmentCancelView, "provider_class", _FakeRefundProvider)
    # Real timezone.now() at request time -- comfortably >=24h eligible
    # regardless of test-run wall-clock drift.
    future_start = timezone.now() + dt.timedelta(days=3)
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=future_start,
        status=AppointmentStatus.CONFIRMED,
    )
    with tenant_context(salon.id):
        payment = Payment.objects.create(
            salon=salon,
            appointment=appt,
            amount=Decimal("100.00"),
            currency=salon.currency,
            status=PaymentStatus.SUCCEEDED,
            provider_reference_id="existing_ref",
        )

    client.force_authenticate(user=customer_account)
    response = client.post(_cancel_url(salon, appt))

    assert response.status_code == 200
    assert len(_FakeRefundProvider.calls) == 1
    with tenant_context(salon.id):
        assert Payment.objects.get(pk=payment.id).status == PaymentStatus.REFUND_PENDING


# --- 3. Cross-customer ownership: 404, not 403 ------------------------------


def test_account_cannot_cancel_another_customers_appointment(
    client, salon, customer, specialist, service, customer_account
):
    with tenant_context(salon.id):
        stranger = Customer.objects.create(
            salon=salon, name="Mallory", email="mallory@example.com", phone="+10000000008"
        )
    other_appt = make_appointment(
        salon=salon,
        customer=stranger,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )

    client.force_authenticate(user=customer_account)
    response = client.post(_cancel_url(salon, other_appt))

    assert response.status_code == 404
    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=other_appt.id)
    assert row.status == AppointmentStatus.CONFIRMED


def test_account_with_no_linked_customer_cannot_cancel_any_appointment(
    client, salon, customer, specialist, service
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    with tenant_context(salon.id):
        unlinked_account = Account.objects.create_account(
            salon=salon,
            email="unverified@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
        )

    client.force_authenticate(user=unlinked_account)
    response = client.post(_cancel_url(salon, appt))

    assert response.status_code == 404


# --- 4. Non-cancellable state: 409 ------------------------------------------


def test_account_cannot_cancel_an_already_cancelled_appointment(
    client, salon, customer, specialist, service, customer_account
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CANCELLED,
    )

    client.force_authenticate(user=customer_account)
    response = client.post(_cancel_url(salon, appt))

    assert response.status_code == 409
    assert response.data["error"]["details"] == {"current_status": AppointmentStatus.CANCELLED}


# --- 5. Unauthenticated request is rejected ---------------------------------


def test_unauthenticated_request_is_rejected(client, salon, customer, specialist, service):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )

    response = client.post(_cancel_url(salon, appt))

    assert response.status_code == 401
