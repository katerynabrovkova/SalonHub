"""
Customer email language set at booking time (docs/DECISIONS.md § Identity,
"`Customer.preferred_language` is the site language at booking time", and
§ Booking › Creation, "`language`"; register "In progress: customer email
language", sub-step (a)).

Both booking endpoints accept an optional `language`:

    POST /api/v1/salons/<slug>/bookings/       (guest)
    POST /api/v1/salons/<slug>/appointments/   (account)

Allowed values are SUPPORTED_LANGUAGES, anything else is 400; a missing one
leaves the stored value unchanged. An account booking always writes it to
the Account's Customer; a guest booking writes it only to a Customer with no
linked Account.

Fixtures and slot numbers mirror test_booking_post_endpoint.py and
test_booking_account_create.py.
"""

import datetime as dt

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from booking.models import Appointment
from core.tenancy import tenant_context
from tests.conftest import make_working_hours

pytestmark = pytest.mark.django_db

UTC = dt.UTC

PASSWORD = "a-strong-passw0rd!"

# A Monday with 09:00-18:00 local (Europe/Kyiv) working hours == 06:00-15:00
# UTC, so 06:00 UTC is the first on-grid candidate for the `service` fixture.
MONDAY = dt.date(2026, 8, 17)
FIRST_CANDIDATE = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)

# Within the default min_lead_time_hours / max_advance_days window.
SAFE_NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=UTC)

GUEST_EMAIL = "alice@example.com"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture
def bookable(monkeypatch, salon, specialist, service):
    """Working hours for `specialist` on MONDAY and a frozen now."""
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    monkeypatch.setattr(timezone, "now", lambda: SAFE_NOW)


def _guest_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/bookings/"


def _account_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/appointments/"


def _guest_payload(specialist, service, *, email: str = GUEST_EMAIL, language=None) -> dict:
    data = {
        "specialist": specialist.id,
        "service": service.id,
        "start_datetime": FIRST_CANDIDATE.isoformat(),
        "customer_name": "Alice",
        "customer_email": email,
        "customer_phone": "+10000000000",
    }
    if language is not None:
        data["language"] = language
    return data


def _account_payload(specialist, service, *, with_contact: bool, language=None) -> dict:
    data = {
        "specialist": specialist.id,
        "service": service.id,
        "start_datetime": FIRST_CANDIDATE.isoformat(),
    }
    if with_contact:
        data["customer_name"] = "Bob"
        data["customer_phone"] = "+10000000002"
    if language is not None:
        data["language"] = language
    return data


def _make_customer(salon, *, email: str, language: str = "", name: str = "Old Name") -> Customer:
    with tenant_context(salon.id):
        return Customer.objects.create(
            salon=salon,
            name=name,
            email=email,
            phone="+19999999999",
            preferred_language=language,
        )


def _verified_account(salon, *, email: str, customer: Customer | None = None) -> Account:
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon,
            email=email,
            password=PASSWORD,
            role=AccountRole.CLIENT,
            customer=customer,
        )
        account.email_verified_at = SAFE_NOW
        account.save(update_fields=["email_verified_at"])
        return account


def _language_of(customer: Customer) -> str:
    with tenant_context(customer.salon_id):
        customer.refresh_from_db()
    return customer.preferred_language


# --- Guest booking (POST bookings/) -------------------------------------------


def test_guest_booking_with_language_sets_it_on_a_new_customer(
    client, bookable, salon, specialist, service
):
    response = client.post(
        _guest_url(salon), _guest_payload(specialist, service, language="uk"), format="json"
    )

    assert response.status_code == 201
    with tenant_context(salon.id):
        customer = Customer.objects.get(salon=salon, email=GUEST_EMAIL)
    assert customer.preferred_language == "uk"


def test_guest_booking_with_language_overwrites_a_returning_unlinked_customer(
    client, bookable, salon, specialist, service
):
    # A second unlinked Customer in the same salon with the lower pk: only
    # the booked one (higher pk) may change.
    bystander = _make_customer(salon, email="bystander@example.com", language="en")
    returning = _make_customer(salon, email=GUEST_EMAIL, language="en")
    assert returning.pk > bystander.pk

    response = client.post(
        _guest_url(salon), _guest_payload(specialist, service, language="uk"), format="json"
    )

    assert response.status_code == 201
    assert _language_of(returning) == "uk"
    assert _language_of(bystander) == "en"


def test_guest_booking_with_language_keeps_a_linked_customers_language(
    client, bookable, salon, specialist, service
):
    linked = _make_customer(salon, email=GUEST_EMAIL, language="en")
    _verified_account(salon, email=GUEST_EMAIL, customer=linked)

    response = client.post(
        _guest_url(salon), _guest_payload(specialist, service, language="uk"), format="json"
    )

    assert response.status_code == 201
    with tenant_context(salon.id):
        appointment = Appointment.objects.get(pk=response.data["appointment"]["id"])
    assert appointment.customer_id == linked.pk
    assert _language_of(linked) == "en"


def test_guest_booking_without_language_keeps_the_stored_value(
    client, bookable, salon, specialist, service
):
    returning = _make_customer(salon, email=GUEST_EMAIL, language="uk")

    response = client.post(_guest_url(salon), _guest_payload(specialist, service), format="json")

    assert response.status_code == 201
    assert _language_of(returning) == "uk"


def test_guest_booking_with_unsupported_language_is_400_and_creates_nothing(
    client, bookable, salon, specialist, service
):
    response = client.post(
        _guest_url(salon), _guest_payload(specialist, service, language="fr"), format="json"
    )

    assert response.status_code == 400
    assert "language" in response.data["error"]["details"]
    with tenant_context(salon.id):
        assert not Appointment.objects.exists()
        assert not Customer.objects.exists()


# --- Account booking (POST appointments/) -------------------------------------


def test_account_booking_with_language_sets_it_on_a_newly_created_customer(
    client, bookable, salon, specialist, service
):
    account = _verified_account(salon, email="new@example.com")
    client.force_authenticate(user=account)

    response = client.post(
        _account_url(salon),
        _account_payload(specialist, service, with_contact=True, language="uk"),
        format="json",
    )

    assert response.status_code == 201
    with tenant_context(salon.id):
        account.refresh_from_db()
        customer = Customer.objects.get(pk=account.customer_id)
    assert customer.email == "new@example.com"
    assert customer.preferred_language == "uk"


def test_account_booking_with_language_overwrites_the_linked_customers_language(
    client, bookable, salon, specialist, service
):
    bystander = _make_customer(salon, email="bystander@example.com", language="en")
    linked = _make_customer(salon, email="linked@example.com", language="en")
    assert linked.pk > bystander.pk
    account = _verified_account(salon, email="linked@example.com", customer=linked)
    client.force_authenticate(user=account)

    response = client.post(
        _account_url(salon),
        _account_payload(specialist, service, with_contact=False, language="uk"),
        format="json",
    )

    assert response.status_code == 201
    assert _language_of(linked) == "uk"
    assert _language_of(bystander) == "en"


def test_account_booking_with_language_links_an_existing_unlinked_customer_and_sets_it(
    client, bookable, salon, specialist, service
):
    existing = _make_customer(salon, email="reused@example.com", language="en")
    account = _verified_account(salon, email="reused@example.com")
    client.force_authenticate(user=account)

    response = client.post(
        _account_url(salon),
        _account_payload(specialist, service, with_contact=True, language="uk"),
        format="json",
    )

    assert response.status_code == 201
    with tenant_context(salon.id):
        account.refresh_from_db()
    assert account.customer_id == existing.pk
    assert _language_of(existing) == "uk"


def test_account_booking_without_language_keeps_the_stored_value(
    client, bookable, salon, specialist, service
):
    linked = _make_customer(salon, email="linked@example.com", language="uk")
    account = _verified_account(salon, email="linked@example.com", customer=linked)
    client.force_authenticate(user=account)

    response = client.post(
        _account_url(salon),
        _account_payload(specialist, service, with_contact=False),
        format="json",
    )

    assert response.status_code == 201
    assert _language_of(linked) == "uk"


def test_account_booking_without_language_keeps_the_value_of_an_adopted_customer(
    client, bookable, salon, specialist, service
):
    existing = _make_customer(salon, email="reused@example.com", language="uk")
    account = _verified_account(salon, email="reused@example.com")
    client.force_authenticate(user=account)

    response = client.post(
        _account_url(salon),
        _account_payload(specialist, service, with_contact=True),
        format="json",
    )

    assert response.status_code == 201
    with tenant_context(salon.id):
        account.refresh_from_db()
    assert account.customer_id == existing.pk
    assert _language_of(existing) == "uk"


def test_account_booking_with_unsupported_language_is_400_and_changes_nothing(
    client, bookable, salon, specialist, service
):
    linked = _make_customer(salon, email="linked@example.com", language="en")
    account = _verified_account(salon, email="linked@example.com", customer=linked)
    client.force_authenticate(user=account)

    response = client.post(
        _account_url(salon),
        _account_payload(specialist, service, with_contact=False, language="fr"),
        format="json",
    )

    assert response.status_code == 400
    assert "language" in response.data["error"]["details"]
    with tenant_context(salon.id):
        assert not Appointment.objects.exists()
    assert _language_of(linked) == "en"


# --- End to end: the email follows the stored language ------------------------


def test_guest_booking_with_uk_gets_a_ukrainian_booking_created_email(
    client, bookable, salon, specialist, service, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            _guest_url(salon), _guest_payload(specialist, service, language="uk"), format="json"
        )

    assert response.status_code == 201
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [GUEST_EMAIL]
    # The fixture salon has only an English name, so it resolves to that.
    assert message.subject == "Ваш запис у Bella Demo Salon створено, залишилось оплатити"
    assert message.body.splitlines()[1] == "Ваш запис створено:"


# --- Two salons ----------------------------------------------------------------


def test_guest_booking_writes_only_the_booking_salons_customer(
    client, bookable, salon, other_salon, specialist, service
):
    in_other_salon = _make_customer(other_salon, email=GUEST_EMAIL, language="en")
    in_this_salon = _make_customer(salon, email=GUEST_EMAIL, language="en")

    response = client.post(
        _guest_url(salon), _guest_payload(specialist, service, language="uk"), format="json"
    )

    assert response.status_code == 201
    assert _language_of(in_this_salon) == "uk"
    assert _language_of(in_other_salon) == "en"
