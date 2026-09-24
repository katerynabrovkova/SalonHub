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
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from payments.models import Payment, PaymentStatus
from specialists.models import Specialist
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


@pytest.fixture
def other_salon_appointment(other_salon):
    """A CONFIRMED appointment that exists, but in another salon."""
    with tenant_context(other_salon.id):
        category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Brows"})
        other_service = Service.objects.create(
            salon=other_salon,
            category=category,
            name={"en": "Brow shaping"},
            duration_minutes=30,
            price="400.00",
            buffer_minutes=0,
        )
        other_specialist = Specialist.objects.create(salon=other_salon, name={"en": "Olga"})
        other_customer = Customer.objects.create(
            salon=other_salon, name="Bob", email="bob@example.com", phone="+10000000009"
        )
    return make_appointment(
        salon=other_salon,
        customer=other_customer,
        specialist=other_specialist,
        service=other_service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )


def test_account_cannot_cancel_another_salons_appointment(
    client, salon, other_salon, other_salon_appointment, customer_account
):
    """An appointment that exists, but in another salon, addressed by its id
    under this salon's URL: 404, and the other salon's row is untouched
    (docs/DECISIONS.md § Stage 15 planning, item 14 design details)."""
    client.force_authenticate(user=customer_account)
    response = client.post(_cancel_url(salon, other_salon_appointment))

    assert response.status_code == 404
    with tenant_context(other_salon.id):
        row = Appointment.objects.get(pk=other_salon_appointment.id)
    assert row.status == AppointmentStatus.CONFIRMED


def test_cancel_404_bodies_are_byte_identical_for_stranger_other_salon_and_nonexistent(
    client, salon, customer, specialist, service, other_salon_appointment, customer_account
):
    """Anti-enumeration: another Customer's appointment, another salon's
    appointment, and an id that exists nowhere must be indistinguishable
    to the caller, down to the response body."""
    with tenant_context(salon.id):
        stranger = Customer.objects.create(
            salon=salon, name="Mallory", email="mallory@example.com", phone="+10000000008"
        )
    stranger_appt = make_appointment(
        salon=salon,
        customer=stranger,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    nonexistent_id = max(stranger_appt.id, other_salon_appointment.id) + 10_000

    client.force_authenticate(user=customer_account)
    stranger_resp = client.post(_cancel_url(salon, stranger_appt))
    cross_salon_resp = client.post(_cancel_url(salon, other_salon_appointment))
    nonexistent_resp = client.post(
        f"/api/v1/salons/{salon.slug}/appointments/{nonexistent_id}/cancel/"
    )

    assert (
        stranger_resp.status_code
        == cross_salon_resp.status_code
        == nonexistent_resp.status_code
        == 404
    )
    assert stranger_resp.content == cross_salon_resp.content == nonexistent_resp.content


def test_account_linked_to_another_salons_customer_cannot_cancel_that_salons_appointment(
    client, salon, other_salon, other_salon_appointment
):
    """The tenant filter as the ONLY guard. An Account in `salon` linked to
    a Customer of `other_salon` (a link the Django admin Account form
    currently allows, docs/DECISIONS.md § Stage 15 planning, item 14 design
    details, known issues) calls `salon`'s URL with that other salon's
    appointment id. The ownership comparison would match here, so only the
    tenant-scoped `Appointment.objects` lookup stands between the request
    and a cross-salon cancellation."""
    with tenant_context(other_salon.id):
        foreign_customer = Customer.objects.get(pk=other_salon_appointment.customer_id)
    with tenant_context(salon.id):
        cross_linked_account = Account.objects.create_account(
            salon=salon,
            email="cross-linked@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
            customer=foreign_customer,
        )

    client.force_authenticate(user=cross_linked_account)
    response = client.post(_cancel_url(salon, other_salon_appointment))

    assert response.status_code == 404
    with tenant_context(other_salon.id):
        row = Appointment.objects.get(pk=other_salon_appointment.id)
    assert row.status == AppointmentStatus.CONFIRMED
    assert row.cancelled_at is None


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
