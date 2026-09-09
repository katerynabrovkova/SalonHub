"""
Stage 11 Part 2 — Review write endpoint (docs/DECISIONS.md § Stage 11 Part 2).
Tests only — written before the permission class, serializer, view, and
urls.py entry exist.

    POST /api/v1/salons/<slug>/appointments/<appointment_id>/review/

RED shape: no route is registered yet, so every POST below resolves to a
plain Django 404 (``HttpResponseNotFound``, no DRF renderer). Each test
therefore fails either on the status line (``assert 404 == 201`` etc.) or,
for the cases whose *correct* answer is 404, on the immediately-following
body assertion (``response.data`` raises ``AttributeError`` on a routing
404). That is the "endpoint doesn't exist yet" signal — deliberately not a
500 and not a fixture error. The body assertion on the 404 cases is what
stops them from passing for the wrong reason at this stage.

Fixtures mirror test_guest_appointment_access.py / test_guest_appointment_pay.py
for the guest-token path (``issue_guest_token`` + ``HTTP_X_GUEST_TOKEN``) and
test_specialist_api.py / test_core_permissions.py for the Account path
(``Account.objects.create_account(customer=...)`` +
``client.force_authenticate``).

Two contract details the implementation must honour for these assertions to
pass (flagged in the RED-phase report, not assumed silently):

* Duplicate → 409. The DB OneToOne on ``Review.appointment`` alone does NOT
  produce 409: ``core.exceptions.exception_handler`` maps a ``UniqueViolation``
  to **400 unique_violation**. A 409 requires an explicit check in the
  serializer's ``validate()`` raising a ``DomainError`` subclass with
  ``status_code = 409``.
* rating out of bounds / text too long → 400. The model-level
  ``CheckConstraint``s surface as a ``CheckViolation`` (not ``UniqueViolation``),
  which the exception handler does NOT translate — it would be a 500. 400
  requires the serializer field itself to bound rating (min/max 1-5) and
  text length (max 2000).
"""

import datetime as dt

import pytest
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from booking.guest_tokens import issue_guest_token
from booking.models import AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from reviews.models import Review
from specialists.models import Specialist
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)
PAYLOAD = {"rating": 5, "text": "Lovely, thorough work — would book again."}


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _review_url(salon, appointment_id) -> str:
    return f"/api/v1/salons/{salon.slug}/appointments/{appointment_id}/review/"


@pytest.fixture
def completed_appointment(salon, customer, specialist, service):
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.COMPLETED,
    )


@pytest.fixture
def guest_token(salon, completed_appointment):
    with tenant_context(salon.id):
        raw_token, _row = issue_guest_token(completed_appointment)
    return raw_token


@pytest.fixture
def customer_account(salon, customer):
    """An Account whose linked Customer (Account.customer) owns
    ``completed_appointment`` — the Account identity path."""
    with tenant_context(salon.id):
        return Account.objects.create_account(
            salon=salon,
            email="alice-account@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
            customer=customer,
        )


@pytest.fixture
def other_salon_completed_appointment(other_salon):
    """A COMPLETED appointment living entirely under ``other_salon`` — used
    for the cross-tenant isolation case."""
    with tenant_context(other_salon.id):
        category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Hair"})
        svc = Service.objects.create(
            salon=other_salon,
            category=category,
            name={"en": "Cut"},
            duration_minutes=30,
            price="200.00",
            buffer_minutes=0,
        )
        spec = Specialist.objects.create(salon=other_salon, name={"en": "Sam"})
        cust = Customer.objects.create(
            salon=other_salon, name="Otto", email="otto@example.com", phone="+10000000009"
        )
    return make_appointment(
        salon=other_salon,
        customer=cust,
        specialist=spec,
        service=svc,
        start=START,
        status=AppointmentStatus.COMPLETED,
    )


# --- 1. Account, happy path ------------------------------------------------


def test_account_owner_of_completed_appointment_can_create_review_201(
    client, salon, customer, specialist, completed_appointment, customer_account
):
    client.force_authenticate(user=customer_account)

    response = client.post(_review_url(salon, completed_appointment.id), PAYLOAD, format="json")

    assert response.status_code == 201
    body = response.data
    assert body["rating"] == 5
    assert body["text"] == PAYLOAD["text"]
    assert body["specialist"] == specialist.id
    assert "id" in body
    assert "created_at" in body

    with tenant_context(salon.id):
        row = Review.objects.get(appointment_id=completed_appointment.id)
    assert row.rating == 5
    assert row.text == PAYLOAD["text"]
    assert row.specialist_id == specialist.id
    assert row.customer_id == customer.id
    assert row.salon_id == salon.id


# --- 2. Guest token, happy path -----------------------------------------


def test_guest_with_valid_token_for_own_completed_appointment_can_create_review_201(
    client, salon, customer, specialist, completed_appointment, guest_token
):
    response = client.post(
        _review_url(salon, completed_appointment.id),
        PAYLOAD,
        format="json",
        HTTP_X_GUEST_TOKEN=guest_token,
    )

    assert response.status_code == 201
    body = response.data
    assert body["rating"] == 5
    assert body["text"] == PAYLOAD["text"]
    assert body["specialist"] == specialist.id

    with tenant_context(salon.id):
        row = Review.objects.get(appointment_id=completed_appointment.id)
    assert row.customer_id == customer.id
    assert row.specialist_id == specialist.id


# --- 3. Account wins, stray guest token is ignored ----------------------


def test_account_path_wins_and_a_mismatched_guest_token_header_is_ignored(
    client, salon, customer, specialist, service, completed_appointment, customer_account
):
    # A guest token that belongs to a *different* appointment. If the endpoint
    # ever read it, token validation would 4xx; it must not be read at all
    # when a valid Account JWT is present.
    other = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(days=1),
        status=AppointmentStatus.COMPLETED,
    )
    with tenant_context(salon.id):
        stray_token, _row = issue_guest_token(other)

    client.force_authenticate(user=customer_account)
    response = client.post(
        _review_url(salon, completed_appointment.id),
        PAYLOAD,
        format="json",
        HTTP_X_GUEST_TOKEN=stray_token,
    )

    assert response.status_code == 201
    assert response.data["specialist"] == specialist.id


# --- 4. Account does not own the appointment -> 404 --------------------


def test_account_that_does_not_own_the_appointment_gets_404(
    client, salon, specialist, service, completed_appointment
):
    with tenant_context(salon.id):
        stranger = Customer.objects.create(
            salon=salon, name="Mallory", email="mallory@example.com", phone="+10000000008"
        )
        stranger_account = Account.objects.create_account(
            salon=salon,
            email="mallory-account@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
            customer=stranger,
        )
    client.force_authenticate(user=stranger_account)

    response = client.post(_review_url(salon, completed_appointment.id), PAYLOAD, format="json")

    assert response.status_code == 404
    # RED: routing 404 has no DRF body — this raises AttributeError until the
    # endpoint exists, so the test cannot pass on the status line alone.
    assert "error" in response.data or "detail" in response.data
    with tenant_context(salon.id):
        assert not Review.objects.filter(appointment_id=completed_appointment.id).exists()


# --- 5. Guest token does not match the URL appointment -> 404 ---------


def test_guest_token_for_a_different_appointment_than_the_url_gets_404(
    client, salon, customer, specialist, service, completed_appointment, guest_token
):
    # guest_token is bound to completed_appointment; aim it at another one.
    other = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(days=2),
        status=AppointmentStatus.COMPLETED,
    )

    response = client.post(
        _review_url(salon, other.id),
        PAYLOAD,
        format="json",
        HTTP_X_GUEST_TOKEN=guest_token,
    )

    # Contract diverges deliberately from the booking pay/cancel endpoints
    # (which return 400 invalid_or_expired_token): here a mismatch is an
    # ownership boundary and must not reveal existence -> 404.
    assert response.status_code == 404
    assert "error" in response.data or "detail" in response.data
    with tenant_context(salon.id):
        assert not Review.objects.filter(appointment_id=other.id).exists()


# --- 6. No identity at all ---------------------------------------------


def test_no_jwt_and_no_guest_token_is_rejected(client, salon, completed_appointment):
    response = client.post(_review_url(salon, completed_appointment.id), PAYLOAD, format="json")

    # Precedent: JWT-only endpoints (DEFAULT_PERMISSION_CLASSES =
    # IsAuthenticated) answer an unauthenticated request with 401; the
    # guest-token endpoints answer a missing token with 400
    # invalid_or_expired_token. "No identity provided" on this dual-auth
    # endpoint is expected to be 401 (nothing authenticated) — accept 403 too
    # rather than over-constraining the unbuilt permission class.
    assert response.status_code in (401, 403)
    with tenant_context(salon.id):
        assert not Review.objects.filter(appointment_id=completed_appointment.id).exists()


# --- 7. Appointment not COMPLETED -> 403 ------------------------------


def test_appointment_owned_but_not_completed_gets_403_mentioning_completion(
    client, salon, customer, specialist, service, customer_account
):
    confirmed = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    client.force_authenticate(user=customer_account)

    response = client.post(_review_url(salon, confirmed.id), PAYLOAD, format="json")

    assert response.status_code == 403
    message = response.data["error"]["message"]
    assert "complet" in message.lower()
    with tenant_context(salon.id):
        assert not Review.objects.filter(appointment_id=confirmed.id).exists()


# --- 8. Review already exists -> 409 ---------------------------------


def test_second_review_for_the_same_appointment_gets_409(
    client, salon, customer, specialist, completed_appointment, customer_account
):
    with tenant_context(salon.id):
        Review.objects.create(
            salon=salon,
            appointment=completed_appointment,
            customer=customer,
            specialist=specialist,
            rating=4,
            text="first",
        )
    client.force_authenticate(user=customer_account)

    response = client.post(_review_url(salon, completed_appointment.id), PAYLOAD, format="json")

    assert response.status_code == 409
    with tenant_context(salon.id):
        assert Review.objects.filter(appointment_id=completed_appointment.id).count() == 1


# --- 9. rating out of bounds -> 400 --------------------------------


@pytest.mark.parametrize("bad_rating", [0, 6])
def test_rating_out_of_bounds_gets_400(
    client, salon, completed_appointment, customer_account, bad_rating
):
    client.force_authenticate(user=customer_account)

    response = client.post(
        _review_url(salon, completed_appointment.id),
        {"rating": bad_rating, "text": ""},
        format="json",
    )

    assert response.status_code == 400
    with tenant_context(salon.id):
        assert not Review.objects.filter(appointment_id=completed_appointment.id).exists()


# --- 10. text too long -> 400 -------------------------------------


def test_text_longer_than_2000_chars_gets_400(
    client, salon, completed_appointment, customer_account
):
    client.force_authenticate(user=customer_account)

    response = client.post(
        _review_url(salon, completed_appointment.id),
        {"rating": 5, "text": "x" * 2001},
        format="json",
    )

    assert response.status_code == 400
    with tenant_context(salon.id):
        assert not Review.objects.filter(appointment_id=completed_appointment.id).exists()


# --- 11. cross-tenant: appointment under a different salon -> 404 ----


def test_appointment_from_a_different_salon_gets_404(
    client, salon, customer_account, other_salon_completed_appointment
):
    client.force_authenticate(user=customer_account)

    # URL slug is `salon`, appointment id belongs to `other_salon`.
    response = client.post(
        _review_url(salon, other_salon_completed_appointment.id), PAYLOAD, format="json"
    )

    assert response.status_code == 404
    assert "error" in response.data or "detail" in response.data
    with tenant_context(other_salon_completed_appointment.salon_id):
        assert not Review.objects.filter(
            appointment_id=other_salon_completed_appointment.id
        ).exists()
