"""
Stage 15 planning, item 10 (docs/DECISIONS.md § "Item 10 design details
(edit name and phone)"): the authenticated Account edits its linked
Customer's name/phone.

    PATCH /api/v1/salons/<slug>/auth/me/customer/

Fixtures mirror test_booking_account_appointment_list.py: an Account linked
to conftest.py's `customer` plus `client.force_authenticate`, except for the
cross-tenant case, which needs a real cookie login (force_authenticate would
bypass the tenant-scoped Account lookup under test). Every test reads the
Customer rows back from the database rather than trusting the response.
"""

import pytest
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

PASSWORD = "a-strong-passw0rd!"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _me_customer_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/auth/me/customer/"


def _login_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/auth/login/"


@pytest.fixture
def customer_account(salon, customer):
    with tenant_context(salon.id):
        return Account.objects.create_account(
            salon=salon,
            email="alice-account@example.com",
            password=PASSWORD,
            role=AccountRole.CLIENT,
            customer=customer,
        )


def _reload(customer) -> Customer:
    return Customer.unscoped_objects.get(pk=customer.pk)


# --- 1. Successful updates ---------------------------------------------------


def test_patch_name_only_updates_the_name(client, salon, customer, customer_account):
    client.force_authenticate(user=customer_account)
    updated_at_before = _reload(customer).updated_at

    response = client.patch(_me_customer_url(salon), {"name": "Alice Smith"}, format="json")

    assert response.status_code == 200
    assert response.data == {"name": "Alice Smith", "phone": "+10000000000"}
    row = _reload(customer)
    assert row.name == "Alice Smith"
    assert row.phone == "+10000000000"
    assert row.updated_at > updated_at_before


def test_patch_phone_only_updates_the_phone(client, salon, customer, customer_account):
    client.force_authenticate(user=customer_account)

    response = client.patch(_me_customer_url(salon), {"phone": "+19999999999"}, format="json")

    assert response.status_code == 200
    assert response.data == {"name": "Alice", "phone": "+19999999999"}
    row = _reload(customer)
    assert row.name == "Alice"
    assert row.phone == "+19999999999"


def test_patch_name_and_phone_updates_both(client, salon, customer, customer_account):
    client.force_authenticate(user=customer_account)

    response = client.patch(
        _me_customer_url(salon), {"name": "Alice Smith", "phone": "+19999999999"}, format="json"
    )

    assert response.status_code == 200
    assert response.data == {"name": "Alice Smith", "phone": "+19999999999"}
    row = _reload(customer)
    assert row.name == "Alice Smith"
    assert row.phone == "+19999999999"


# --- 2. Invalid bodies: 400, nothing changes ---------------------------------


def test_patch_with_neither_field_is_400(client, salon, customer, customer_account):
    client.force_authenticate(user=customer_account)

    response = client.patch(_me_customer_url(salon), {}, format="json")

    assert response.status_code == 400
    row = _reload(customer)
    assert row.name == "Alice"
    assert row.phone == "+10000000000"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", "   "),
        ("phone", ""),
        ("phone", "   "),
        ("name", "x" * 256),
        ("phone", "1" * 33),
    ],
    ids=[
        "blank-name",
        "whitespace-name",
        "blank-phone",
        "whitespace-phone",
        "long-name",
        "long-phone",
    ],
)
def test_patch_with_blank_or_too_long_value_is_400(
    client, salon, customer, customer_account, field, value
):
    client.force_authenticate(user=customer_account)

    response = client.patch(_me_customer_url(salon), {field: value}, format="json")

    assert response.status_code == 400
    row = _reload(customer)
    assert row.name == "Alice"
    assert row.phone == "+10000000000"


# --- 3. No linked Customer: 404, same body as the cancel/pay precedent -------


def test_patch_without_a_linked_customer_is_404_with_the_cancel_body(client, salon, customer):
    with tenant_context(salon.id):
        unlinked_account = Account.objects.create_account(
            salon=salon,
            email="alice@example.com",
            password=PASSWORD,
            role=AccountRole.CLIENT,
        )
    client.force_authenticate(user=unlinked_account)

    response = client.patch(_me_customer_url(salon), {"name": "Alice Smith"}, format="json")
    cancel_response = client.post(f"/api/v1/salons/{salon.slug}/appointments/999999/cancel/")

    assert response.status_code == 404
    assert cancel_response.status_code == 404
    assert response.content == cancel_response.content
    # The same-email Customer is not linked to this Account, so it must not
    # be touched.
    row = _reload(customer)
    assert row.name == "Alice"
    assert row.phone == "+10000000000"


# --- 4. Authentication and tenant isolation ----------------------------------


def test_unauthenticated_patch_is_401(client, salon, customer, customer_account):
    response = client.patch(_me_customer_url(salon), {"name": "Alice Smith"}, format="json")

    assert response.status_code == 401
    row = _reload(customer)
    assert row.name == "Alice"
    assert row.phone == "+10000000000"


def test_account_logged_in_to_another_salon_gets_401_and_nothing_changes(
    client, salon, other_salon, customer, customer_account
):
    with tenant_context(other_salon.id):
        other_customer = Customer.objects.create(
            salon=other_salon, name="Bob", email="bob@example.com", phone="+10000000009"
        )
        other_account = Account.objects.create_account(
            salon=other_salon,
            email="bob@example.com",
            password=PASSWORD,
            role=AccountRole.CLIENT,
            customer=other_customer,
        )
    login = client.post(
        _login_url(other_salon),
        {"email": other_account.email, "password": PASSWORD},
        format="json",
    )
    assert login.status_code == 200

    response = client.patch(
        _me_customer_url(salon), {"name": "Mallory", "phone": "+19999999999"}, format="json"
    )

    assert response.status_code == 401
    row = _reload(customer)
    assert row.name == "Alice"
    assert row.phone == "+10000000000"
    other_row = _reload(other_customer)
    assert other_row.name == "Bob"
    assert other_row.phone == "+10000000009"


# --- 5. Mass assignment: only name/phone are writable ------------------------


def test_patch_ignores_fields_other_than_name_and_phone(
    client, salon, other_salon, customer, customer_account
):
    client.force_authenticate(user=customer_account)

    response = client.patch(
        _me_customer_url(salon),
        {
            "name": "Alice Smith",
            "email": "attacker@example.com",
            "salon": other_salon.id,
            "preferred_language": "uk",
        },
        format="json",
    )

    assert response.status_code == 200
    row = _reload(customer)
    assert row.name == "Alice Smith"
    assert row.phone == "+10000000000"
    assert row.email == "alice@example.com"
    assert row.salon_id == salon.id
    assert row.preferred_language == ""
