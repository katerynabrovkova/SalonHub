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
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from booking.models import AppointmentStatus
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


def test_specialist_and_service_are_nested_id_and_name_objects(
    client, salon, customer, specialist, service, customer_account
):
    """specialist/service used to be bare FK ids on this serializer; the
    dashboard needs a display name per card, so both are now nested
    {id, name} (docs/DECISIONS.md § Stage 15 planning, item 4) -- name
    resolved the same way reviews.serializers.ReviewSpecialistSerializer/
    ReviewServiceSerializer do."""
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )

    client.force_authenticate(user=customer_account)
    response = client.get(_mine_url(salon))

    (row,) = response.data["results"]
    assert row["specialist"] == {"id": specialist.id, "name": "Jane"}
    assert row["service"] == {"id": service.id, "name": "Manicure"}


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


# --- 3a. payment_status/payment_amount/amount_due_at_visit -----------------
# (docs/DECISIONS.md § Stage 15 planning, item 4, Part 2 -- the 15 reachable
# (appointment_status, payment_status) combinations mapped in this session's
# earlier recon)


def _make_payment(salon, appt, *, status, amount="100.00", provider_reference_id="ref") -> Payment:
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon,
            appointment=appt,
            amount=Decimal(amount),
            currency=salon.currency,
            status=status,
            provider_reference_id=provider_reference_id,
        )


def test_payment_fields_are_null_when_no_payment_row_exists(
    client, salon, customer, specialist, service, customer_account
):
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
    )

    client.force_authenticate(user=customer_account)
    response = client.get(_mine_url(salon))

    (row,) = response.data["results"]
    assert row["payment_status"] is None
    assert row["payment_amount"] is None
    assert row["amount_due_at_visit"] is None


@pytest.mark.parametrize(
    "payment_status",
    [
        PaymentStatus.PENDING,
        PaymentStatus.FAILED,
        PaymentStatus.SUCCEEDED,
        PaymentStatus.REFUND_PENDING,
        PaymentStatus.REFUNDED,
    ],
)
def test_payment_status_and_amount_reflect_the_real_payment(
    client, salon, customer, specialist, service, customer_account, payment_status
):
    """Covers every reachable Payment status from this session's earlier
    recon (PROCESSING/EXPIRED/CANCELLED are never assigned by any
    production code path, so are not exercised here)."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CANCELLED,
    )
    payment = _make_payment(salon, appt, status=payment_status, amount="123.45")

    client.force_authenticate(user=customer_account)
    response = client.get(_mine_url(salon))

    (row,) = response.data["results"]
    assert row["payment_status"] == payment_status
    # str, not a bare JSON number -- same representation as
    # service_price_at_booking/deposit_percentage_at_booking's real
    # DecimalFields (docs/DECISIONS.md § Stage 15 planning, item 4).
    assert isinstance(row["payment_amount"], str)
    assert row["payment_amount"] == str(payment.amount)


def test_amount_due_at_visit_is_computed_for_confirmed_with_succeeded_payment(
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
    _make_payment(salon, appt, status=PaymentStatus.SUCCEEDED, amount="100.00")

    client.force_authenticate(user=customer_account)
    response = client.get(_mine_url(salon))

    (row,) = response.data["results"]
    expected = Decimal(str(service.price)) - Decimal("100.00")
    assert isinstance(row["amount_due_at_visit"], str)
    assert row["amount_due_at_visit"] == str(expected)


def test_amount_due_at_visit_is_null_for_pending_payment_with_no_payment_row(
    client, salon, customer, specialist, service, customer_account
):
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
    )

    client.force_authenticate(user=customer_account)
    response = client.get(_mine_url(salon))

    (row,) = response.data["results"]
    assert row["amount_due_at_visit"] is None


def test_amount_due_at_visit_is_null_for_cancelled_with_succeeded_payment(
    client, salon, customer, specialist, service, customer_account
):
    """CANCELLED+SUCCEEDED is reachable (refund-ineligible cancellation,
    <24h before start) -- amount_due_at_visit must still be null, since the
    booking is no longer honored."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CANCELLED,
    )
    _make_payment(salon, appt, status=PaymentStatus.SUCCEEDED, amount="100.00")

    client.force_authenticate(user=customer_account)
    response = client.get(_mine_url(salon))

    (row,) = response.data["results"]
    assert row["amount_due_at_visit"] is None


def test_amount_due_at_visit_is_null_for_cancelled_with_refunded_payment(
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
    _make_payment(salon, appt, status=PaymentStatus.REFUNDED, amount="100.00")

    client.force_authenticate(user=customer_account)
    response = client.get(_mine_url(salon))

    (row,) = response.data["results"]
    assert row["amount_due_at_visit"] is None


# --- 3b. select_related("payment", "specialist", "service") avoids N+1 -----


def test_payment_specialist_and_service_fields_add_no_n_plus_one_query_across_a_list(
    client, salon, customer, specialist, service, customer_account
):
    """Covers all three select_related() additions at once (payment,
    specialist, service): every row here reads `.payment.status`,
    `.specialist.name`, and `.service.name`, so a missing select_related on
    any of the three would show up as extra queries per row below, not just
    a fixed offset."""
    one_appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    _make_payment(salon, one_appt, status=PaymentStatus.SUCCEEDED, provider_reference_id="ref-0")

    client.force_authenticate(user=customer_account)
    with CaptureQueriesContext(connection) as one_row_ctx:
        response = client.get(_mine_url(salon))
    assert response.status_code == 200
    assert response.data["count"] == 1
    one_row_query_count = len(one_row_ctx.captured_queries)

    for i in range(1, 6):
        appt = make_appointment(
            salon=salon,
            customer=customer,
            specialist=specialist,
            service=service,
            start=START + dt.timedelta(days=i),
            status=AppointmentStatus.CONFIRMED,
        )
        _make_payment(salon, appt, status=PaymentStatus.SUCCEEDED, provider_reference_id=f"ref-{i}")

    with CaptureQueriesContext(connection) as six_rows_ctx:
        response = client.get(_mine_url(salon))
    assert response.status_code == 200
    assert response.data["count"] == 6

    # Fixed query count regardless of row volume -- select_related("payment")
    # joins the Payment row into the same SELECT, not one query per row.
    assert len(six_rows_ctx.captured_queries) == one_row_query_count


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
