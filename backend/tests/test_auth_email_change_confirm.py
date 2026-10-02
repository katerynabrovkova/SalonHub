"""
Stage 15 planning, item 8, cycle 2 (docs/DECISIONS.md § "Item 8 decisions
(change email)", including "Cycle 2 additions"): confirm an email change
from the emailed token.

    POST /api/v1/salons/<slug>/auth/email-change/confirm/   body: {"token"}

Public endpoint (PublicEndpointMixin), so no authentication here. Tokens are
minted with accounts.tokens.generate_email_change_token unless a test says
otherwise. "Nothing changes" is checked with a snapshot of every Account's
email/email_verified_at and every Customer's email across both salons,
read through the unscoped managers.

RED shape: the route does not exist yet, so every POST gets a plain Django
404 and each test fails on its first status assertion.
"""

import time

import pytest
from django.core import signing
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from accounts.tokens import generate_account_verification_token, generate_email_change_token
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

PASSWORD = "a-strong-passw0rd!"
OLD_EMAIL = "alice-account@example.com"
NEW_EMAIL = "new@example.com"

EMAIL_CHANGE_SALT = "accounts.email-change"
TOKEN_MAX_AGE = 24 * 60 * 60


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _confirm_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/auth/email-change/confirm/"


def _login_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/auth/login/"


def _confirm(client, salon, token: str):
    return client.post(_confirm_url(salon), {"token": token}, format="json")


@pytest.fixture
def customer_account(salon, customer):
    with tenant_context(salon.id):
        return Account.objects.create_account(
            salon=salon,
            email=OLD_EMAIL,
            password=PASSWORD,
            role=AccountRole.CLIENT,
            customer=customer,
        )


@pytest.fixture
def unlinked_account(salon):
    with tenant_context(salon.id):
        return Account.objects.create_account(
            salon=salon, email=OLD_EMAIL, password=PASSWORD, role=AccountRole.CLIENT
        )


def _snapshot() -> tuple[list, list]:
    accounts = list(
        Account.unscoped_objects.order_by("pk").values_list("pk", "email", "email_verified_at")
    )
    customers = list(Customer.unscoped_objects.order_by("pk").values_list("pk", "email"))
    return accounts, customers


def _reload_account(account) -> Account:
    return Account.unscoped_objects.get(pk=account.pk)


def _reload_customer(customer) -> Customer:
    return Customer.unscoped_objects.get(pk=customer.pk)


def _assert_invalid_token(response) -> None:
    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"


def _assert_email_unavailable(response) -> None:
    assert response.status_code == 400
    assert response.data["error"]["code"] == "email_unavailable"


# --- 1-2. Success ---------------------------------------------------------------


def test_valid_token_changes_account_and_linked_customer_and_verifies(
    client, salon, customer, customer_account
):
    token = generate_email_change_token(customer_account, NEW_EMAIL)

    response = _confirm(client, salon, token)

    assert response.status_code == 204
    assert response.content == b""
    account = _reload_account(customer_account)
    assert account.email == NEW_EMAIL
    assert account.email_verified_at is not None
    assert _reload_customer(customer).email == NEW_EMAIL


def test_unverified_account_without_customer_changes_only_the_account(
    client, salon, customer, unlinked_account
):
    _accounts_before, customers_before = _snapshot()
    token = generate_email_change_token(unlinked_account, NEW_EMAIL)

    response = _confirm(client, salon, token)

    assert response.status_code == 204
    account = _reload_account(unlinked_account)
    assert account.email == NEW_EMAIL
    assert account.email_verified_at is not None
    _accounts_after, customers_after = _snapshot()
    assert customers_after == customers_before


# --- 3-8. Invalid tokens -----------------------------------------------------------


def test_token_cannot_be_used_twice(client, salon, customer, customer_account):
    token = generate_email_change_token(customer_account, NEW_EMAIL)

    first = _confirm(client, salon, token)
    assert first.status_code == 204
    after_first = _snapshot()

    second = _confirm(client, salon, token)

    _assert_invalid_token(second)
    assert _snapshot() == after_first


def test_token_older_than_24_hours_is_rejected(
    monkeypatch, client, salon, customer, customer_account
):
    stale = int(time.time()) - TOKEN_MAX_AGE - 60
    with monkeypatch.context() as m:
        m.setattr(signing.TimestampSigner, "timestamp", lambda self: signing.b62_encode(stale))
        token = generate_email_change_token(customer_account, NEW_EMAIL)
    before = _snapshot()

    response = _confirm(client, salon, token)

    _assert_invalid_token(response)
    assert _snapshot() == before


def test_garbage_token_is_rejected(client, salon, customer_account):
    response = _confirm(client, salon, "not-a-token")

    _assert_invalid_token(response)


def test_registration_verification_token_is_rejected(client, salon, customer, customer_account):
    token = generate_account_verification_token(customer_account)
    before = _snapshot()

    response = _confirm(client, salon, token)

    _assert_invalid_token(response)
    assert _snapshot() == before


def test_token_for_another_salons_account_looks_like_a_garbage_token(
    client, salon, other_salon, customer, customer_account
):
    with tenant_context(other_salon.id):
        other_account = Account.objects.create_account(
            salon=other_salon, email=OLD_EMAIL, password=PASSWORD, role=AccountRole.CLIENT
        )
    token = generate_email_change_token(other_account, NEW_EMAIL)
    before = _snapshot()

    response = _confirm(client, salon, token)
    garbage = _confirm(client, salon, "not-a-token")

    _assert_invalid_token(response)
    assert response.status_code == garbage.status_code
    assert response.content == garbage.content
    assert _snapshot() == before


def test_token_whose_salon_id_names_another_salon_is_rejected(
    client, salon, other_salon, customer, customer_account
):
    token = signing.TimestampSigner(salt=EMAIL_CHANGE_SALT).sign_object(
        {
            "account_id": customer_account.id,
            "salon_id": other_salon.id,
            "old_email": OLD_EMAIL,
            "new_email": NEW_EMAIL,
        }
    )
    before = _snapshot()

    response = _confirm(client, salon, token)

    _assert_invalid_token(response)
    assert _snapshot() == before


# --- 9-12. Address taken at confirmation ---------------------------------------


def test_address_taken_by_an_account_after_issue_is_400_email_unavailable(
    client, salon, customer, customer_account
):
    token = generate_email_change_token(customer_account, NEW_EMAIL)
    with tenant_context(salon.id):
        Account.objects.create_account(salon=salon, email=NEW_EMAIL, password=PASSWORD)
    before = _snapshot()

    response = _confirm(client, salon, token)

    _assert_email_unavailable(response)
    assert _snapshot() == before


def test_address_of_a_guest_customer_in_another_case_is_400_email_unavailable(
    client, salon, customer, customer_account
):
    with tenant_context(salon.id):
        Customer.objects.create(
            salon=salon, name="Guest", email="New@Example.com", phone="+10000000001"
        )
    token = generate_email_change_token(customer_account, NEW_EMAIL)
    before = _snapshot()

    response = _confirm(client, salon, token)

    _assert_email_unavailable(response)
    assert _snapshot() == before


def test_account_unique_violation_at_save_is_400_email_unavailable(
    monkeypatch, client, salon, customer, customer_account
):
    monkeypatch.setattr("accounts.views.is_email_taken", lambda **kwargs: False)
    with tenant_context(salon.id):
        Account.objects.create_account(salon=salon, email=NEW_EMAIL, password=PASSWORD)
    token = generate_email_change_token(customer_account, NEW_EMAIL)
    before = _snapshot()

    response = _confirm(client, salon, token)

    _assert_email_unavailable(response)
    assert _snapshot() == before


def test_customer_unique_violation_at_save_rolls_back_the_account_too(
    monkeypatch, client, salon, customer, customer_account
):
    monkeypatch.setattr("accounts.views.is_email_taken", lambda **kwargs: False)
    with tenant_context(salon.id):
        Customer.objects.create(salon=salon, name="Guest", email=NEW_EMAIL, phone="+10000000001")
    token = generate_email_change_token(customer_account, NEW_EMAIL)
    before = _snapshot()

    response = _confirm(client, salon, token)

    _assert_email_unavailable(response)
    assert _reload_account(customer_account).email == OLD_EMAIL
    assert _snapshot() == before


# --- 13-14. Public endpoint and login afterwards ------------------------------------


def test_stale_access_cookie_does_not_block_confirmation(client, salon, customer, customer_account):
    token = generate_email_change_token(customer_account, NEW_EMAIL)
    client.cookies["access_token"] = "not-a-valid-jwt"

    response = _confirm(client, salon, token)

    assert response.status_code == 204


def test_after_confirmation_login_works_with_new_email_and_not_the_old(
    client, salon, customer, customer_account
):
    token = generate_email_change_token(customer_account, NEW_EMAIL)
    confirm = _confirm(client, salon, token)
    assert confirm.status_code == 204

    new_login = client.post(
        _login_url(salon), {"email": NEW_EMAIL, "password": PASSWORD}, format="json"
    )
    old_login = APIClient().post(
        _login_url(salon), {"email": OLD_EMAIL, "password": PASSWORD}, format="json"
    )

    assert new_login.status_code == 200
    assert old_login.status_code == 401
