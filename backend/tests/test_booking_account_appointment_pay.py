"""
Stage 15 planning, item 14 design details, P2 — POST appointments/<id>/pay/
(docs/DECISIONS.md § Stage 15 planning, item 14): the authenticated Account
starts payment for its own Customer's appointment.

    POST /api/v1/salons/<slug>/appointments/<appointment_id>/pay/

Mirrors two existing endpoints:

- Ownership and auth from AccountAppointmentCancelView
  (test_booking_account_appointment_cancel.py): project-wide cookie/Bearer
  JWT defaults (401 unauthenticated), tenant-scoped fetch by pk, then the
  owning Customer compared to the caller's linked Customer, every mismatch a
  plain 404 (never 403), identical to a nonexistent id.
- Response shape and the provider seam from GuestAppointmentPayView
  (test_guest_appointment_pay.py): ``{"payment": {id, status, amount,
  currency}, "provider_data": ...}``, 201 when a Payment is created and 200
  when an existing PENDING one is reused; 409 for a non-PENDING_PAYMENT
  appointment; 502 on provider failure; the provider substituted via
  ``monkeypatch.setattr(AccountAppointmentPayView, "provider_class", ...)``.

No CSRF test here: the cancel endpoint's own test file has none either.
CSRF for cookie-authenticated unsafe requests is enforced once, in
accounts.authentication.AccountJWTCookieAuthentication, and covered in
test_csrf_protection.py.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

import pytest
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from booking.models import Appointment, AppointmentStatus
from booking.views import AccountAppointmentPayView
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


def _pay_url(salon, appointment_id: int) -> str:
    return f"/api/v1/salons/{salon.slug}/appointments/{appointment_id}/pay/"


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


@pytest.fixture
def appointment(salon, customer, specialist, service):
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
    )


@pytest.fixture
def other_salon_appointment(other_salon):
    """A PENDING_PAYMENT appointment that exists, but in another salon."""
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
        status=AppointmentStatus.PENDING_PAYMENT,
    )


@pytest.fixture
def stranger_appointment(salon, specialist, service):
    """Same salon, another Customer's PENDING_PAYMENT appointment."""
    with tenant_context(salon.id):
        stranger = Customer.objects.create(
            salon=salon, name="Mallory", email="mallory@example.com", phone="+10000000008"
        )
    return make_appointment(
        salon=salon,
        customer=stranger,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(days=1),
        status=AppointmentStatus.PENDING_PAYMENT,
    )


@dataclass(frozen=True)
class _FakeIntent:
    provider_reference_id: str
    provider_data: object = None


class _FakeProvider:
    """Records every start_payment call on a CLASS-level list: the view
    builds a fresh provider_class() per request, so call counts spanning two
    requests must survive separate instances (same shape as
    test_guest_appointment_pay.py)."""

    calls: ClassVar[list[dict]] = []

    def start_payment(self, *, amount, currency, reference):
        type(self).calls.append({"amount": amount, "currency": currency, "reference": reference})
        return _FakeIntent(provider_reference_id=f"fake_ref_{len(type(self).calls)}")

    def refund(self, *, provider_reference_id, reference, amount, currency):
        raise NotImplementedError("not exercised by pay endpoint tests")


class _Boom(Exception):
    """Stand-in for a real provider adapter failure."""


class _RaisingProvider:
    def start_payment(self, *, amount, currency, reference):
        raise _Boom("network down")

    def refund(self, *, provider_reference_id, reference, amount, currency):
        raise NotImplementedError("not exercised by pay endpoint tests")


@pytest.fixture(autouse=True)
def _reset_fake_provider_calls():
    _FakeProvider.calls = []
    yield
    _FakeProvider.calls = []


def _payment_exists(salon, appointment_id: int) -> bool:
    with tenant_context(salon.id):
        return Payment.objects.filter(appointment_id=appointment_id).exists()


# --- 1. Owner, PENDING_PAYMENT: 201 ------------------------------------------


def test_owner_pays_pending_payment_appointment_returns_201_and_creates_pending_payment(
    client, salon, appointment, customer_account, monkeypatch
):
    monkeypatch.setattr(AccountAppointmentPayView, "provider_class", _FakeProvider)

    client.force_authenticate(user=customer_account)
    response = client.post(_pay_url(salon, appointment.id))

    assert response.status_code == 201
    assert set(response.data) == {"payment", "provider_data"}
    assert set(response.data["payment"]) == {"id", "status", "amount", "currency"}
    assert response.data["provider_data"] is None
    with tenant_context(salon.id):
        rows = list(Payment.objects.filter(appointment_id=appointment.id))
    assert len(rows) == 1
    row = rows[0]
    assert row.status == PaymentStatus.PENDING
    assert response.data["payment"]["id"] == row.id
    assert response.data["payment"]["status"] == PaymentStatus.PENDING
    assert Decimal(str(response.data["payment"]["amount"])) == row.amount
    assert response.data["payment"]["currency"] == salon.currency
    assert len(_FakeProvider.calls) == 1
    assert _FakeProvider.calls[0]["reference"] == str(appointment.id)


# --- 2. Second call: 200, same Payment, provider not called again -----------


def test_second_pay_call_returns_200_same_payment_and_does_not_call_provider_again(
    client, salon, appointment, customer_account, monkeypatch
):
    monkeypatch.setattr(AccountAppointmentPayView, "provider_class", _FakeProvider)
    client.force_authenticate(user=customer_account)

    first = client.post(_pay_url(salon, appointment.id))
    assert first.status_code == 201

    second = client.post(_pay_url(salon, appointment.id))

    assert second.status_code == 200
    assert second.data["payment"]["id"] == first.data["payment"]["id"]
    assert len(_FakeProvider.calls) == 1


# --- 3-6. Not the caller's appointment: 404 ---------------------------------


def test_another_customers_appointment_is_404(
    client, salon, stranger_appointment, customer_account, monkeypatch
):
    monkeypatch.setattr(AccountAppointmentPayView, "provider_class", _FakeProvider)

    client.force_authenticate(user=customer_account)
    response = client.post(_pay_url(salon, stranger_appointment.id))

    assert response.status_code == 404
    assert not _payment_exists(salon, stranger_appointment.id)
    assert _FakeProvider.calls == []


def test_account_with_no_linked_customer_is_404(client, salon, appointment, monkeypatch):
    monkeypatch.setattr(AccountAppointmentPayView, "provider_class", _FakeProvider)
    with tenant_context(salon.id):
        unlinked_account = Account.objects.create_account(
            salon=salon,
            email="unlinked@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
        )

    client.force_authenticate(user=unlinked_account)
    response = client.post(_pay_url(salon, appointment.id))

    assert response.status_code == 404
    assert not _payment_exists(salon, appointment.id)
    assert _FakeProvider.calls == []


def test_another_salons_appointment_id_under_this_salons_url_is_404(
    client, salon, other_salon, other_salon_appointment, customer_account, monkeypatch
):
    monkeypatch.setattr(AccountAppointmentPayView, "provider_class", _FakeProvider)

    client.force_authenticate(user=customer_account)
    response = client.post(_pay_url(salon, other_salon_appointment.id))

    assert response.status_code == 404
    assert not _payment_exists(other_salon, other_salon_appointment.id)
    with tenant_context(other_salon.id):
        row = Appointment.objects.get(pk=other_salon_appointment.id)
    assert row.status == AppointmentStatus.PENDING_PAYMENT
    assert _FakeProvider.calls == []


def test_account_linked_to_another_salons_customer_cannot_pay_that_salons_appointment(
    client, salon, other_salon, other_salon_appointment, monkeypatch
):
    """The tenant filter as the ONLY guard. An Account in `salon` linked to
    a Customer of `other_salon` (a link the Django admin Account form
    currently allows, docs/DECISIONS.md § Stage 15 planning, item 14 design
    details, known issues) calls `salon`'s URL with that other salon's
    appointment id. The ownership comparison would match here, so only the
    tenant-scoped `Appointment.objects` lookup stands between the request
    and a cross-salon payment."""
    monkeypatch.setattr(AccountAppointmentPayView, "provider_class", _FakeProvider)
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
    response = client.post(_pay_url(salon, other_salon_appointment.id))

    assert response.status_code == 404
    assert not _payment_exists(other_salon, other_salon_appointment.id)
    with tenant_context(other_salon.id):
        row = Appointment.objects.get(pk=other_salon_appointment.id)
    assert row.status == AppointmentStatus.PENDING_PAYMENT
    assert _FakeProvider.calls == []


def test_nonexistent_appointment_id_is_404(
    client, salon, appointment, customer_account, monkeypatch
):
    monkeypatch.setattr(AccountAppointmentPayView, "provider_class", _FakeProvider)

    client.force_authenticate(user=customer_account)
    response = client.post(_pay_url(salon, appointment.id + 10_000))

    assert response.status_code == 404
    assert _FakeProvider.calls == []


# --- 7. Anti-enumeration: identical 404 bodies ------------------------------


def test_404_bodies_are_byte_identical_for_stranger_other_salon_and_nonexistent(
    client,
    salon,
    appointment,
    stranger_appointment,
    other_salon_appointment,
    customer_account,
    monkeypatch,
):
    """Another Customer's appointment, another salon's appointment, and an
    id that exists nowhere must be indistinguishable to the caller."""
    monkeypatch.setattr(AccountAppointmentPayView, "provider_class", _FakeProvider)
    nonexistent_id = (
        max(appointment.id, stranger_appointment.id, other_salon_appointment.id) + 10_000
    )

    client.force_authenticate(user=customer_account)
    stranger = client.post(_pay_url(salon, stranger_appointment.id))
    cross_salon = client.post(_pay_url(salon, other_salon_appointment.id))
    nonexistent = client.post(_pay_url(salon, nonexistent_id))

    assert stranger.status_code == cross_salon.status_code == nonexistent.status_code == 404
    assert stranger.content == cross_salon.content == nonexistent.content
    assert _FakeProvider.calls == []


# --- 8. Unauthenticated: 401 ------------------------------------------------


def test_unauthenticated_request_is_401(client, salon, appointment, monkeypatch):
    monkeypatch.setattr(AccountAppointmentPayView, "provider_class", _FakeProvider)

    response = client.post(_pay_url(salon, appointment.id))

    assert response.status_code == 401
    assert not _payment_exists(salon, appointment.id)
    assert _FakeProvider.calls == []


# --- 9. Not PENDING_PAYMENT: 409 --------------------------------------------


@pytest.mark.parametrize(
    "appointment_status",
    [AppointmentStatus.CONFIRMED, AppointmentStatus.CANCELLED, AppointmentStatus.EXPIRED],
)
def test_owner_pay_on_non_pending_payment_appointment_is_409(
    client, salon, customer, specialist, service, customer_account, monkeypatch, appointment_status
):
    monkeypatch.setattr(AccountAppointmentPayView, "provider_class", _FakeProvider)
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=appointment_status,
    )

    client.force_authenticate(user=customer_account)
    response = client.post(_pay_url(salon, appt.id))

    assert response.status_code == 409
    assert response.data["error"]["code"] == "invalid_state_transition"
    assert response.data["error"]["details"] == {"current_status": appointment_status}
    assert not _payment_exists(salon, appt.id)
    assert _FakeProvider.calls == []


# --- 10. Provider failure: 502 ----------------------------------------------


def test_provider_failure_returns_502_and_creates_no_payment_row(
    client, salon, appointment, customer_account, monkeypatch
):
    monkeypatch.setattr(AccountAppointmentPayView, "provider_class", _RaisingProvider)

    client.force_authenticate(user=customer_account)
    response = client.post(_pay_url(salon, appointment.id))

    assert response.status_code == 502
    assert response.data["error"]["code"] == "payment_provider_error"
    assert not _payment_exists(salon, appointment.id)
