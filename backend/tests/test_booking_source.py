"""
Stage 16 sub-step 9 — `Appointment.source` (docs/DECISIONS.md § Stage 16
planning, "Sub-step 9 details").

Both booking endpoints accept an optional body field `source`. Missing means
`direct`; `"direct"` and `"assistant"` are stored as sent; an unknown value
is a 400 and creates nothing. The value reaches the row on both the specific-specialist
and the "any" path. It is not returned in booking responses.

Values are compared as plain strings rather than through the choices class,
so the tests fail on behaviour, not on an import. Slot setup and the frozen
`now` mirror test_booking_post_endpoint.py and test_booking_account_create.py.
"""

import datetime as dt

import pytest
from django.contrib import admin
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from booking.models import Appointment
from core.tenancy import tenant_context
from tests.conftest import make_appointment, make_working_hours

pytestmark = pytest.mark.django_db

UTC = dt.UTC
PASSWORD = "a-strong-passw0rd!"

# A Monday with 09:00-18:00 Europe/Kyiv working hours == 06:00-15:00 UTC, so
# 06:00 UTC is the first on-grid candidate for the `service` fixture.
MONDAY = dt.date(2026, 8, 17)
FIRST_CANDIDATE = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)
SAFE_NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=UTC)

UNKNOWN_SOURCES = ["web", "Assistant", "", None]


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: SAFE_NOW)


@pytest.fixture
def bookable(salon, specialist, service):
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    return specialist, service


def _guest_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/bookings/"


def _account_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/appointments/"


def _guest_payload(specialist_id, service, **extra) -> dict:
    return {
        "specialist": specialist_id,
        "service": service.id,
        "start_datetime": FIRST_CANDIDATE.isoformat(),
        "customer_name": "Alice",
        "customer_email": "alice@example.com",
        "customer_phone": "+10000000000",
        **extra,
    }


def _account_payload(specialist_id, service, **extra) -> dict:
    return {
        "specialist": specialist_id,
        "service": service.id,
        "start_datetime": FIRST_CANDIDATE.isoformat(),
        "customer_name": "Alice",
        "customer_phone": "+10000000000",
        **extra,
    }


def _verified_account(salon) -> Account:
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon, email="client@example.com", password=PASSWORD, role=AccountRole.CLIENT
        )
        account.email_verified_at = SAFE_NOW
        account.save(update_fields=["email_verified_at"])
    return account


def _only_appointment(salon) -> Appointment:
    with tenant_context(salon.id):
        return Appointment.objects.get()


# --- Model default -------------------------------------------------------------


def test_appointment_source_defaults_to_direct(salon, specialist, service, customer):
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
    )

    appointment.refresh_from_db()
    assert appointment.source == "direct"


# --- Guest booking -------------------------------------------------------------


@pytest.mark.parametrize("specialist_value", ["id", "any"])
def test_guest_booking_with_source_assistant_stores_source(
    client, salon, bookable, specialist_value
):
    specialist, service = bookable
    specialist_id = specialist.id if specialist_value == "id" else "any"

    response = client.post(
        _guest_url(salon),
        _guest_payload(specialist_id, service, source="assistant"),
        format="json",
    )

    assert response.status_code == 201
    assert _only_appointment(salon).source == "assistant"


def test_guest_booking_without_source_stores_direct(client, salon, bookable):
    specialist, service = bookable

    response = client.post(_guest_url(salon), _guest_payload(specialist.id, service), format="json")

    assert response.status_code == 201
    assert _only_appointment(salon).source == "direct"


def test_guest_booking_with_explicit_source_direct_stores_direct(client, salon, bookable):
    specialist, service = bookable

    response = client.post(
        _guest_url(salon), _guest_payload(specialist.id, service, source="direct"), format="json"
    )

    assert response.status_code == 201
    assert _only_appointment(salon).source == "direct"


@pytest.mark.parametrize("source", UNKNOWN_SOURCES)
def test_guest_booking_with_unknown_source_returns_400_and_creates_nothing(
    client, salon, bookable, source
):
    specialist, service = bookable

    response = client.post(
        _guest_url(salon), _guest_payload(specialist.id, service, source=source), format="json"
    )

    assert response.status_code == 400
    assert "source" in response.data["error"]["details"]
    with tenant_context(salon.id):
        assert not Appointment.objects.exists()
        assert not Customer.objects.exists()


def test_guest_booking_response_does_not_expose_source(client, salon, bookable):
    specialist, service = bookable

    response = client.post(
        _guest_url(salon),
        _guest_payload(specialist.id, service, source="assistant"),
        format="json",
    )

    assert response.status_code == 201
    assert set(response.data["appointment"]) == {"id", "status", "start_datetime", "end_datetime"}


# --- Account booking -----------------------------------------------------------


@pytest.mark.parametrize("specialist_value", ["id", "any"])
def test_account_booking_with_source_assistant_stores_source(
    client, salon, bookable, specialist_value
):
    specialist, service = bookable
    specialist_id = specialist.id if specialist_value == "id" else "any"
    client.force_authenticate(user=_verified_account(salon))

    response = client.post(
        _account_url(salon),
        _account_payload(specialist_id, service, source="assistant"),
        format="json",
    )

    assert response.status_code == 201
    assert _only_appointment(salon).source == "assistant"


def test_account_booking_without_source_stores_direct(client, salon, bookable):
    specialist, service = bookable
    client.force_authenticate(user=_verified_account(salon))

    response = client.post(
        _account_url(salon), _account_payload(specialist.id, service), format="json"
    )

    assert response.status_code == 201
    assert _only_appointment(salon).source == "direct"


def test_account_booking_with_explicit_source_direct_stores_direct(client, salon, bookable):
    specialist, service = bookable
    client.force_authenticate(user=_verified_account(salon))

    response = client.post(
        _account_url(salon),
        _account_payload(specialist.id, service, source="direct"),
        format="json",
    )

    assert response.status_code == 201
    assert _only_appointment(salon).source == "direct"


@pytest.mark.parametrize("source", UNKNOWN_SOURCES)
def test_account_booking_with_unknown_source_returns_400_and_creates_nothing(
    client, salon, bookable, source
):
    specialist, service = bookable
    account = _verified_account(salon)
    client.force_authenticate(user=account)

    response = client.post(
        _account_url(salon),
        _account_payload(specialist.id, service, source=source),
        format="json",
    )

    assert response.status_code == 400
    assert "source" in response.data["error"]["details"]
    account.refresh_from_db()
    assert account.customer_id is None
    with tenant_context(salon.id):
        assert not Appointment.objects.exists()
        assert not Customer.objects.exists()


def test_account_booking_response_does_not_expose_source(client, salon, bookable):
    specialist, service = bookable
    client.force_authenticate(user=_verified_account(salon))

    response = client.post(
        _account_url(salon),
        _account_payload(specialist.id, service, source="assistant"),
        format="json",
    )

    assert response.status_code == 201
    assert set(response.data["appointment"]) == {"id", "status", "start_datetime", "end_datetime"}


# --- Admin ---------------------------------------------------------------------


def test_appointment_admin_lists_and_filters_by_source():
    model_admin = admin.site._registry[Appointment]

    assert "source" in model_admin.list_display
    assert "source" in model_admin.list_filter
