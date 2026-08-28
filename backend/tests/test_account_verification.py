"""
Stage 3-R.D.4 — email-verification endpoint
(``POST /api/v1/salons/<slug>/auth/verify-email/``; docs/DECISIONS.md
§ Stage 3-R.D.4).

RED phase: written against the agreed contract before ``VerifyEmailView`` or
its URL tail exist. Every test reaches the behaviour only through the HTTP
endpoint and asserts on rows via ``unscoped_objects``; nothing here is
imported from the unwritten view, so collection succeeds and every
pre-implementation failure is a clean 404 — the ``salons/<slug>/`` prefix
resolves, the ``verify-email/`` tail does not yet
(``test_get_is_405_method_not_allowed`` is a 404 too until the route lands).

The contract under test (docs/DECISIONS.md § Stage 3-R.D.4):

* success is ``204 No Content``, empty body;
* verification is an idempotent filtered update — a second verify with the
  same token is still ``204`` and leaves the original ``email_verified_at``
  timestamp untouched;
* after stamping, a same-salon guest ``Customer`` whose email matches the
  account's is linked via ``Account.customer`` — only when the account is
  not already linked, matched by email only, never by phone;
* every token failure (expired, tampered, minted for another salon, naming
  an account that changed its email since) collapses to one neutral
  ``400`` with ``error.code == "invalid_or_expired_token"`` and leaves
  ``email_verified_at`` NULL.
"""

import pytest
from rest_framework.test import APIClient

from accounts import tokens
from accounts.models import Account, Customer
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "a-strong-passw0rd!"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _verify_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/verify-email/"


def _make_account(salon, email: str = "verify-me@example.com") -> Account:
    with tenant_context(salon.id):
        return Account.objects.create_account(salon=salon, email=email, password=STRONG_PASSWORD)


def _mint(account: Account, salon) -> str:
    with tenant_context(salon.id):
        return tokens.generate_account_verification_token(account)


def _make_guest_customer(
    salon, *, email: str, phone: str = "+15550000000", name: str = "Guest Row"
) -> Customer:
    with tenant_context(salon.id):
        return Customer.objects.create(salon=salon, name=name, email=email, phone=phone)


# --- 1. happy path -----------------------------------------------------------


def test_valid_token_verifies_account_and_returns_204(client, salon) -> None:
    account = _make_account(salon)
    token = _mint(account, salon)

    response = client.post(_verify_url(salon.slug), {"token": token}, format="json")

    assert response.status_code == 204
    assert response.data is None

    row = Account.unscoped_objects.get(pk=account.pk)
    assert row.email_verified_at is not None


# --- 2. idempotent filtered update ----------------------------------------


def test_verifying_twice_is_idempotent_204_and_keeps_original_timestamp(client, salon) -> None:
    account = _make_account(salon)
    token = _mint(account, salon)

    first = client.post(_verify_url(salon.slug), {"token": token}, format="json")
    assert first.status_code == 204
    stamped_at = Account.unscoped_objects.get(pk=account.pk).email_verified_at
    assert stamped_at is not None

    second = client.post(_verify_url(salon.slug), {"token": token}, format="json")
    assert second.status_code == 204
    assert Account.unscoped_objects.get(pk=account.pk).email_verified_at == stamped_at


# --- 3. same-salon guest link -------------------------------------------


def test_verification_links_matching_same_salon_guest_customer(client, salon) -> None:
    account = _make_account(salon)
    guest = _make_guest_customer(salon, email=account.email)
    token = _mint(account, salon)

    response = client.post(_verify_url(salon.slug), {"token": token}, format="json")

    assert response.status_code == 204
    row = Account.unscoped_objects.get(pk=account.pk)
    assert row.customer_id == guest.pk


# --- 4. no matching guest, still succeeds --------------------------------


def test_verification_with_no_matching_customer_still_204_and_customer_stays_none(
    client, salon
) -> None:
    account = _make_account(salon)
    token = _mint(account, salon)

    response = client.post(_verify_url(salon.slug), {"token": token}, format="json")

    assert response.status_code == 204
    row = Account.unscoped_objects.get(pk=account.pk)
    assert row.customer_id is None


# --- 5. an existing link is never overwritten --------------------------


def test_verification_does_not_overwrite_an_already_linked_customer(client, salon) -> None:
    account = _make_account(salon)
    # Already linked to a customer that the email match would NOT pick.
    linked = _make_guest_customer(salon, email="already-linked@example.com", name="Linked")
    with tenant_context(salon.id):
        account.customer = linked
        account.save(update_fields=["customer"])
    # A guest row that DOES match the account's email — the link step, which
    # runs only when account.customer is None, must leave this one alone.
    email_match = _make_guest_customer(salon, email=account.email, name="Email Match")
    token = _mint(account, salon)

    response = client.post(_verify_url(salon.slug), {"token": token}, format="json")

    assert response.status_code == 204
    row = Account.unscoped_objects.get(pk=account.pk)
    assert row.customer_id == linked.pk
    assert not Account.unscoped_objects.filter(customer_id=email_match.pk).exists()


# --- 6/7/8/11. every token failure -> one neutral 400, no verification ---


def test_expired_token_returns_400_invalid_or_expired_token(client, salon, monkeypatch) -> None:
    account = _make_account(salon)
    token = _mint(account, salon)
    monkeypatch.setattr(tokens, "ACCOUNT_VERIFICATION_TOKEN_MAX_AGE", -1)

    response = client.post(_verify_url(salon.slug), {"token": token}, format="json")

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"
    assert Account.unscoped_objects.get(pk=account.pk).email_verified_at is None


def test_tampered_token_returns_400_invalid_or_expired_token(client, salon) -> None:
    account = _make_account(salon)
    token = _mint(account, salon)
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")

    response = client.post(_verify_url(salon.slug), {"token": tampered}, format="json")

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"
    assert Account.unscoped_objects.get(pk=account.pk).email_verified_at is None


def test_token_for_another_salon_returns_400_and_does_not_verify(
    client, salon, other_salon
) -> None:
    account = _make_account(salon, email="cross@example.com")
    token = _mint(account, salon)

    response = client.post(_verify_url(other_salon.slug), {"token": token}, format="json")

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"
    assert Account.unscoped_objects.get(pk=account.pk).email_verified_at is None


def test_token_stops_working_after_the_account_email_changes(client, salon) -> None:
    account = _make_account(salon)
    token = _mint(account, salon)
    with tenant_context(salon.id):
        Account.objects.filter(pk=account.pk).update(email="changed@example.com")

    response = client.post(_verify_url(salon.slug), {"token": token}, format="json")

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"
    assert Account.unscoped_objects.get(pk=account.pk).email_verified_at is None


# --- 9. only POST is allowed -------------------------------------------


def test_get_is_405_method_not_allowed(client, salon) -> None:
    response = client.get(_verify_url(salon.slug))

    assert response.status_code == 405


# --- 10. the link key is the email, not the phone ---------------------


def test_link_matches_by_email_only_not_phone(client, salon) -> None:
    account = _make_account(salon)
    # Same contact phone, DIFFERENT email: the match key is the verified
    # email, so this guest row must not be linked to the account.
    _make_guest_customer(salon, email="different-address@example.com", phone="+15550000000")
    token = _mint(account, salon)

    response = client.post(_verify_url(salon.slug), {"token": token}, format="json")

    assert response.status_code == 204
    row = Account.unscoped_objects.get(pk=account.pk)
    assert row.customer_id is None
