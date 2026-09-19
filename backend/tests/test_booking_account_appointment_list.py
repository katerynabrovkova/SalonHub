"""
Stage 15 planning, item 1 — GET appointments/mine/ (docs/DECISIONS.md § Stage
15 planning): appointments belonging to the authenticated Account's linked
Customer in the current salon. Also covers item 4 Part A's price/deposit
snapshot fields on AppointmentAccountSerializer.

    GET /api/v1/salons/<slug>/appointments/mine/

Fixtures mirror test_review_create_endpoint.py's Account path
(``Account.objects.create_account(customer=...)`` + ``client.force_authenticate``)
and conftest.py's ``salon``/``other_salon`` pair for the cross-tenant case.
"""

import datetime as dt
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from booking.models import AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from specialists.models import Specialist
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _mine_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/appointments/mine/"


def _login_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/auth/login/"


@pytest.fixture
def customer_account(salon, customer):
    """An Account whose linked Customer owns some appointments at `salon`."""
    with tenant_context(salon.id):
        return Account.objects.create_account(
            salon=salon,
            email="alice-account@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
            customer=customer,
        )


# --- 1. An Account only sees its own Customer's appointments ------------


def test_account_only_sees_its_own_customers_appointments(
    client, salon, customer, specialist, service, customer_account
):
    own = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )

    with tenant_context(salon.id):
        stranger = Customer.objects.create(
            salon=salon, name="Mallory", email="mallory@example.com", phone="+10000000008"
        )
    make_appointment(
        salon=salon,
        customer=stranger,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(days=1),
        status=AppointmentStatus.CONFIRMED,
    )

    client.force_authenticate(user=customer_account)
    response = client.get(_mine_url(salon))

    assert response.status_code == 200
    results = response.data["results"]
    assert [row["id"] for row in results] == [own.id]


def test_account_with_no_linked_customer_sees_an_empty_list(client, salon):
    with tenant_context(salon.id):
        unlinked_account = Account.objects.create_account(
            salon=salon,
            email="unverified@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
        )
    client.force_authenticate(user=unlinked_account)

    response = client.get(_mine_url(salon))

    assert response.status_code == 200
    assert response.data["results"] == []


def test_price_and_deposit_fields_appear_with_correct_values(
    client, salon, customer, specialist, service, customer_account
):
    """Stage 15 planning, item 4, Part A: the price/deposit snapshot fields
    the dashboard needs to show an amount per appointment."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )

    client.force_authenticate(user=customer_account)
    response = client.get(_mine_url(salon))

    assert response.status_code == 200
    (row,) = response.data["results"]
    assert row["id"] == appt.id
    # service.price/salon.deposit_percentage are in-memory values (str/int as
    # the fixtures assigned them), not the Decimal a DB read returns -- the
    # same coercion trap test_booking_cancel_appointment.py documents.
    assert Decimal(str(row["service_price_at_booking"])) == Decimal(str(service.price))
    assert Decimal(str(row["deposit_percentage_at_booking"])) == Decimal(
        str(salon.deposit_percentage)
    )


def test_results_are_ordered_newest_appointment_first(
    client, salon, customer, specialist, service, customer_account
):
    earlier = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    later = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(days=3),
        status=AppointmentStatus.CONFIRMED,
    )

    client.force_authenticate(user=customer_account)
    response = client.get(_mine_url(salon))

    assert response.status_code == 200
    assert [row["id"] for row in response.data["results"]] == [later.id, earlier.id]


# --- 2. Cross-tenant isolation, including a same-email Account -----------


def test_account_at_salon_a_cannot_see_appointments_from_salon_b_even_with_same_email(
    client, salon, other_salon, customer, specialist, service, customer_account
):
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )

    # A distinct Account, in a different salon, that happens to share the
    # exact same email as customer_account -- and has its own appointment
    # over there. Two independent Accounts per the per-salon
    # unique(salon, email) rule (docs/DECISIONS.md § Identity), not the
    # same login.
    with tenant_context(other_salon.id):
        other_customer = Customer.objects.create(
            salon=other_salon,
            name="Alice",
            email="alice-account@example.com",
            phone="+20000000000",
        )
        other_account = Account.objects.create_account(
            salon=other_salon,
            email="alice-account@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
            customer=other_customer,
        )
        other_category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Hair"})
        other_service = Service.objects.create(
            salon=other_salon,
            category=other_category,
            name={"en": "Cut"},
            duration_minutes=30,
            price="200.00",
            buffer_minutes=0,
        )
        other_specialist = Specialist.objects.create(salon=other_salon, name={"en": "Sam"})
    other_appointment = make_appointment(
        salon=other_salon,
        customer=other_customer,
        specialist=other_specialist,
        service=other_service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )

    assert customer_account.email == other_account.email
    assert customer_account.salon_id != other_account.salon_id

    client.force_authenticate(user=other_account)
    response = client.get(_mine_url(other_salon))

    assert response.status_code == 200
    result_ids = [row["id"] for row in response.data["results"]]
    assert result_ids == [other_appointment.id]


def test_salon_a_appointment_never_appears_when_querying_salon_b(
    client, salon, other_salon, customer, specialist, service, customer_account
):
    salon_a_appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )

    with tenant_context(other_salon.id):
        other_customer = Customer.objects.create(
            salon=other_salon,
            name="Alice",
            email="alice-account@example.com",
            phone="+20000000000",
        )
        other_account = Account.objects.create_account(
            salon=other_salon,
            email="alice-account@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
            customer=other_customer,
        )

    client.force_authenticate(user=other_account)
    response = client.get(_mine_url(other_salon))

    assert response.status_code == 200
    result_ids = [row["id"] for row in response.data["results"]]
    assert salon_a_appointment.id not in result_ids
    assert result_ids == []


# --- 3. Unauthenticated request is rejected ------------------------------


def test_unauthenticated_request_is_rejected(client, salon):
    response = client.get(_mine_url(salon))

    assert response.status_code == 401


# --- 4. Session for one salon, hitting another salon's URL --------------


def test_account_authenticated_for_its_own_salon_gets_rejected_when_querying_another_salons_url(
    client, salon, other_salon, customer
):
    """
    A real logged-in session for `salon` (cookie-based JWT, not
    force_authenticate — force_authenticate sets request.user directly and
    would bypass the exact mechanism under test), pointed at
    `other_salon`'s `appointments/mine/` URL.

    Established project convention for this precise scenario
    (tests/test_auth_me.py's test_me_wrong_salon_cookie_returns_401_or_404):
    the access cookie is still sent, but AccountJWTCookieAuthentication ->
    AccountJWTAuthentication.get_user() resolves it through the
    tenant-scoped Account.objects manager bound to whatever salon the URL
    names. Under other_salon's tenant context, that manager cannot see a
    row belonging to salon at all, so the lookup raises
    Account.DoesNotExist -> AuthenticationFailed -> 401 — never a 200 with
    this Account's own appointments leaking through under the wrong
    salon's URL, and never a 404 (the URL itself resolves fine; it's the
    credential that's rejected).
    """
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon,
            email="alice-account@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
            customer=customer,
        )
    client.post(
        _login_url(salon),
        {"email": account.email, "password": "a-strong-passw0rd!"},
        format="json",
    )

    response = client.get(_mine_url(other_salon))

    assert response.status_code == 401
