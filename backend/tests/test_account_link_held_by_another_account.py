"""
docs/DECISIONS.md § "Account linked to a Customer held by another Account"
(decided 20.10.2026): account B's email matches a Customer C that is already
linked to account A in the same salon.

    POST /api/v1/salons/<slug>/appointments/
    POST /api/v1/salons/<slug>/auth/verify-email/
    GET  /api/v1/salons/<slug>/appointments/mine/
    POST /api/v1/salons/<slug>/appointments/<id>/cancel/

Setup for each test: one salon; Customer C (`shared@example.com`) linked to
Account A (`a@example.com`); Account B (`shared@example.com`) in the same
salon, not linked. The mismatched A <-> C link is made through the ORM, as an
admin edit would make it -- no API path produces it (same entry, point 1).

Asserts only through HTTP responses and the database; the new error class is
not imported, so before it exists every test fails on an assertion, not on
import.

Fixtures mirror test_booking_account_create.py (`_verified_account`,
`_working_hours`, `_freeze_now`, the payload shape) and
test_account_verification.py (`_mint`, the verify URL).
"""

import datetime as dt

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import tokens
from accounts.models import Account, AccountRole, Customer
from booking.models import Appointment, AppointmentStatus
from core.tenancy import tenant_context
from tests.conftest import make_appointment, make_working_hours

pytestmark = pytest.mark.django_db

UTC = dt.UTC

PASSWORD = "a-strong-passw0rd!"

SHARED_EMAIL = "shared@example.com"
C_NAME = "Alice"
C_PHONE = "+10000000001"

LINK_UNAVAILABLE_MESSAGE = "Не вдалося оформити запис з облікового запису. Зверніться до салону."

# Same numbers as test_booking_account_create.py: a Monday with 09:00-18:00
# local (Europe/Kyiv) working hours == 06:00-15:00 UTC.
MONDAY = dt.date(2026, 8, 17)
FIRST_CANDIDATE = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)
A_APPOINTMENT_START = dt.datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
SAFE_NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=UTC)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _account_bookings_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/appointments/"


def _verify_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/auth/verify-email/"


def _mine_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/appointments/mine/"


def _cancel_url(salon, appointment) -> str:
    return f"/api/v1/salons/{salon.slug}/appointments/{appointment.id}/cancel/"


def _working_hours(salon, specialist) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )


def _freeze_now(monkeypatch: pytest.MonkeyPatch, value: dt.datetime = SAFE_NOW) -> None:
    monkeypatch.setattr(timezone, "now", lambda: value)


def _payload(specialist, service) -> dict:
    return {
        "specialist": specialist.id,
        "service": service.id,
        "start_datetime": FIRST_CANDIDATE.isoformat(),
        "customer_name": "Bob",
        "customer_phone": "+19990000000",
    }


def _account(salon, *, email: str, customer: Customer | None = None, verified: bool) -> Account:
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon,
            email=email,
            password=PASSWORD,
            role=AccountRole.CLIENT,
            customer=customer,
        )
        if verified:
            account.email_verified_at = timezone.now()
            account.save(update_fields=["email_verified_at"])
        return account


def _mint(account: Account, salon) -> str:
    with tenant_context(salon.id):
        return tokens.generate_account_verification_token(account)


@pytest.fixture
def customer_c(salon) -> Customer:
    with tenant_context(salon.id):
        return Customer.objects.create(salon=salon, name=C_NAME, email=SHARED_EMAIL, phone=C_PHONE)


@pytest.fixture
def account_a(salon, customer_c) -> Account:
    return _account(salon, email="a@example.com", customer=customer_c, verified=True)


def _assert_links_unchanged(account_a: Account, account_b: Account, customer_c: Customer) -> None:
    assert Account.unscoped_objects.get(pk=account_b.pk).customer_id is None
    assert Account.unscoped_objects.get(pk=account_a.pk).customer_id == customer_c.pk


# --- 1. Booking by verified B ----------------------------------------------


def test_booking_by_b_returns_409_account_link_unavailable_and_changes_nothing(
    client, monkeypatch, salon, specialist, service, customer_c, account_a
):
    _working_hours(salon, specialist)
    _freeze_now(monkeypatch)
    account_b = _account(salon, email=SHARED_EMAIL, verified=True)
    client.force_authenticate(user=account_b)

    response = client.post(
        _account_bookings_url(salon), _payload(specialist, service), format="json"
    )

    assert response.status_code == 409
    assert response.data["error"]["code"] == "account_link_unavailable"
    assert response.data["error"]["message"] == LINK_UNAVAILABLE_MESSAGE
    assert not Appointment.unscoped_objects.exists()
    c_row = Customer.unscoped_objects.get(pk=customer_c.pk)
    assert (c_row.name, c_row.phone) == (C_NAME, C_PHONE)
    _assert_links_unchanged(account_a, account_b, customer_c)


# --- 2. Verification by unverified B ---------------------------------------


def test_verification_by_b_succeeds_without_linking(client, salon, customer_c, account_a):
    account_b = _account(salon, email=SHARED_EMAIL, verified=False)
    token = _mint(account_b, salon)

    response = client.post(_verify_url(salon), {"token": token}, format="json")

    assert response.status_code == 204
    assert Account.unscoped_objects.get(pk=account_b.pk).email_verified_at is not None
    _assert_links_unchanged(account_a, account_b, customer_c)


# --- 3. B never reaches A's appointments -----------------------------------


def test_b_cannot_see_or_cancel_as_appointment_after_verifying_and_booking(
    monkeypatch, salon, specialist, service, customer_c, account_a
):
    """B goes through verification and a booking attempt first, so a link to C
    made by either path would show up here. A's own list and cancel are the
    control: they prove the URLs and the appointment id are right, so B's
    empty list and 404 are not a wrong-URL artifact."""
    _working_hours(salon, specialist)
    _freeze_now(monkeypatch)
    a_appointment = make_appointment(
        salon=salon,
        customer=customer_c,
        specialist=specialist,
        service=service,
        start=A_APPOINTMENT_START,
        status=AppointmentStatus.CONFIRMED,
    )
    account_b = _account(salon, email=SHARED_EMAIL, verified=False)
    b_client = APIClient()
    b_client.post(_verify_url(salon), {"token": _mint(account_b, salon)}, format="json")
    account_b = Account.unscoped_objects.get(pk=account_b.pk)
    b_client.force_authenticate(user=account_b)
    b_client.post(_account_bookings_url(salon), _payload(specialist, service), format="json")
    account_b = Account.unscoped_objects.get(pk=account_b.pk)
    b_client.force_authenticate(user=account_b)

    b_mine = b_client.get(_mine_url(salon))
    b_cancel = b_client.post(_cancel_url(salon, a_appointment))

    assert b_mine.status_code == 200
    assert a_appointment.id not in [row["id"] for row in b_mine.data["results"]]
    assert b_cancel.status_code == 404
    with tenant_context(salon.id):
        assert Appointment.objects.get(pk=a_appointment.id).status == AppointmentStatus.CONFIRMED

    a_client = APIClient()
    a_client.force_authenticate(user=account_a)
    a_mine = a_client.get(_mine_url(salon))
    assert a_appointment.id in [row["id"] for row in a_mine.data["results"]]
    assert a_client.post(_cancel_url(salon, a_appointment)).status_code == 200


# --- 4. Positive control: a fresh email still books and links ---------------


def test_verified_account_with_a_fresh_email_books_and_gets_linked(
    client, monkeypatch, salon, specialist, service, customer_c, account_a
):
    """Guards against an implementation that refuses every unlinked booking."""
    _working_hours(salon, specialist)
    _freeze_now(monkeypatch)
    fresh = _account(salon, email="fresh@example.com", verified=True)
    client.force_authenticate(user=fresh)

    response = client.post(
        _account_bookings_url(salon), _payload(specialist, service), format="json"
    )

    assert response.status_code == 201
    with tenant_context(salon.id):
        new_customer = Customer.objects.get(email="fresh@example.com")
        appointment = Appointment.objects.get(pk=response.data["appointment"]["id"])
    assert Account.unscoped_objects.get(pk=fresh.pk).customer_id == new_customer.pk
    assert appointment.customer_id == new_customer.pk
    assert Account.unscoped_objects.get(pk=account_a.pk).customer_id == customer_c.pk
