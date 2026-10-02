"""
Stage 8.D — guest pay endpoint (docs/ARCHITECTURE.md § 8; docs/DECISIONS.md
§ Stage 8.D decisions). Tests only — written before GuestAppointmentPayView,
its URL, and HasValidGuestToken's "pay" action exist.

Expected to fail on collection: importing GuestAppointmentPayView from
booking.views below raises ImportError until the view is added. That's the
whole module failing to collect, not a fixture problem — the same
collection-time red shape test_payments_initiate_payment.py established for
initiate_payment.

Named test contract for provider substitution (§ Stage 8.D decisions):
`monkeypatch.setattr(GuestAppointmentPayView, "provider_class", <fake>)`,
with GuestAppointmentPayView imported from booking.views. A later rename of
that class or attribute silently breaks this without raising an error of its
own — same caution as booking/services.py's double-booking monkeypatch
contract (docs/DECISIONS.md § Stage 6 decisions).

`_FakeProvider.calls` is a CLASS-level list, not per-instance:
GuestAppointmentPayView builds a fresh `provider_class()` per request, so a
call-count assertion spanning two HTTP requests (the idempotency test) must
survive separate instantiations within one test. Reset by the
`_reset_fake_provider_calls` autouse fixture below.

Fixtures follow the same client/appointment/token pattern
test_guest_appointment_access.py already established — appointment here is
PENDING_PAYMENT (not that file's default CONFIRMED), since that's the status
this endpoint requires.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from booking.guest_tokens import issue_guest_token
from booking.models import AppointmentStatus
from booking.views import GuestAppointmentPayView
from core.tenancy import tenant_context
from payments.models import Payment, PaymentStatus
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)


def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same time-bomb fix as test_guest_appointment_access.py: the guest
    token expires 30 days after the fixed START appointment ends, checked
    against the real timezone.now(), so freeze now to START."""
    monkeypatch.setattr(timezone, "now", lambda: START)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


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
def token(salon, appointment):
    with tenant_context(salon.id):
        raw_token, _row = issue_guest_token(appointment)
    return raw_token


def _pay_url(salon, appointment) -> str:
    return f"/api/v1/salons/{salon.slug}/guest/appointments/{appointment.id}/pay/"


@dataclass(frozen=True)
class _FakeIntent:
    provider_reference_id: str
    provider_data: object = None


class _FakeProvider:
    calls: ClassVar[list[dict]] = []

    def start_payment(self, *, amount, currency, reference):
        type(self).calls.append({"amount": amount, "currency": currency, "reference": reference})
        return _FakeIntent(provider_reference_id=f"fake_ref_{len(type(self).calls)}")

    def refund(self, *, provider_reference_id, reference):
        raise NotImplementedError("not exercised by pay endpoint tests")


class _Boom(Exception):
    """Stand-in for a real provider adapter failure — the view must not leak
    it; PaymentProviderError -> 502 instead."""


class _RaisingProvider:
    def start_payment(self, *, amount, currency, reference):
        raise _Boom("network down")

    def refund(self, *, provider_reference_id, reference):
        raise NotImplementedError("not exercised by pay endpoint tests")


@pytest.fixture(autouse=True)
def _reset_fake_provider_calls():
    _FakeProvider.calls = []
    yield


def test_pay_happy_path_creates_a_pending_payment_and_returns_201(
    client, salon, appointment, token, monkeypatch
):
    _freeze_now(monkeypatch)
    monkeypatch.setattr(GuestAppointmentPayView, "provider_class", _FakeProvider)

    response = client.post(_pay_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)

    assert response.status_code == 201
    assert response.data["payment"]["status"] == PaymentStatus.PENDING
    assert response.data["provider_data"] is None
    assert Decimal(str(response.data["payment"]["amount"])) == Decimal("100.00")
    assert response.data["payment"]["currency"] == salon.currency
    with tenant_context(salon.id):
        row = Payment.objects.get(appointment_id=appointment.id)
    assert row.status == PaymentStatus.PENDING
    assert response.data["payment"]["id"] == row.id
    assert len(_FakeProvider.calls) == 1


def test_pay_second_call_with_pending_payment_returns_200_and_does_not_call_provider_again(
    client, salon, appointment, token, monkeypatch
):
    _freeze_now(monkeypatch)
    monkeypatch.setattr(GuestAppointmentPayView, "provider_class", _FakeProvider)

    first = client.post(_pay_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)
    assert first.status_code == 201

    second = client.post(_pay_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)

    assert second.status_code == 200
    assert second.data["payment"]["id"] == first.data["payment"]["id"]
    assert len(_FakeProvider.calls) == 1


def test_pay_retries_an_existing_failed_payment_in_place_and_returns_201(
    client, salon, appointment, token, monkeypatch
):
    _freeze_now(monkeypatch)
    monkeypatch.setattr(GuestAppointmentPayView, "provider_class", _FakeProvider)
    with tenant_context(salon.id):
        existing = Payment.objects.create(
            salon=salon,
            appointment=appointment,
            amount=Decimal("100.00"),
            currency=salon.currency,
            status=PaymentStatus.FAILED,
            provider_reference_id="old_ref",
        )

    response = client.post(_pay_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)

    assert response.status_code == 201
    assert response.data["payment"]["id"] == existing.pk
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=existing.pk)
    assert row.status == PaymentStatus.PENDING
    assert row.provider_reference_id != "old_ref"


def test_pay_rejects_an_appointment_not_in_pending_payment_with_409(
    monkeypatch, client, salon, customer, specialist, service
):
    _freeze_now(monkeypatch)
    confirmed = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    with tenant_context(salon.id):
        raw_token, _row = issue_guest_token(confirmed)

    response = client.post(_pay_url(salon, confirmed), HTTP_X_GUEST_TOKEN=raw_token)

    assert response.status_code == 409
    assert response.data["error"]["code"] == "invalid_state_transition"
    assert response.data["error"]["details"] == {"current_status": AppointmentStatus.CONFIRMED}


def test_pay_provider_failure_returns_502_and_creates_no_payment_row(
    client, salon, appointment, token, monkeypatch
):
    _freeze_now(monkeypatch)
    monkeypatch.setattr(GuestAppointmentPayView, "provider_class", _RaisingProvider)

    response = client.post(_pay_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)

    assert response.status_code == 502
    assert response.data["error"]["code"] == "payment_provider_error"
    with tenant_context(salon.id):
        assert not Payment.objects.filter(appointment_id=appointment.id).exists()


def test_pay_with_a_token_for_a_different_appointment_is_rejected_with_400(
    client, salon, customer, specialist, service, appointment, token, monkeypatch
):
    monkeypatch.setattr(GuestAppointmentPayView, "provider_class", _FakeProvider)
    other_appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(days=1),
        status=AppointmentStatus.PENDING_PAYMENT,
    )

    response = client.post(_pay_url(salon, other_appointment), HTTP_X_GUEST_TOKEN=token)

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"
    with tenant_context(salon.id):
        assert not Payment.objects.filter(appointment_id=other_appointment.id).exists()
    assert _FakeProvider.calls == []


def test_pay_with_no_token_header_is_rejected_with_400(client, salon, appointment, monkeypatch):
    monkeypatch.setattr(GuestAppointmentPayView, "provider_class", _FakeProvider)

    response = client.post(_pay_url(salon, appointment))

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"
    assert _FakeProvider.calls == []
