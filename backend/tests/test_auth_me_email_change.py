"""
Stage 15 planning, item 8, cycle 1 (docs/DECISIONS.md § "Item 8 decisions
(change email)"): the authenticated Account requests an email change.

    POST /api/v1/salons/<slug>/auth/me/email-change/

Fixtures mirror test_auth_me_customer.py: an Account linked to conftest.py's
`customer` plus `client.force_authenticate`, except for the cross-tenant
case, which needs a real cookie login (force_authenticate would bypass the
tenant-scoped Account lookup under test). Mail lands in
`django.core.mail.outbox` (conftest.py's eager Celery + pytest-django's
locmem backend).

RED shape: the route does not exist yet, so every POST below gets a plain
Django 404 and each test fails on its first status assertion. Nothing here
imports a name that does not exist yet.
"""

import re

import pytest
from django.core import mail, signing
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from accounts.tokens import read_account_verification_token
from core.i18n import resolve_translation
from core.tenancy import tenant_context
from core.urls import build_salon_frontend_url

pytestmark = pytest.mark.django_db

PASSWORD = "a-strong-passw0rd!"
NEW_EMAIL = "new@example.com"
TAKEN_EMAIL = "taken@example.com"

EMAIL_CHANGE_SALT = "accounts.email-change"
EMAIL_CHANGE_MAX_AGE = 24 * 60 * 60

SUBJECT_CONFIRM = "Підтвердіть нову адресу пошти"
SUBJECT_TAKEN = "Зміна адреси пошти"
SUBJECT_NOTICE = "Запит на зміну адреси пошти"
TAKEN_TEXT = "Цю адресу не можна використати."


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _email_change_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/auth/me/email-change/"


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


def _post(client, salon, new_email: str, password: str = PASSWORD):
    return client.post(
        _email_change_url(salon),
        {"new_email": new_email, "password": password},
        format="json",
    )


def _salon_name(salon) -> str:
    return resolve_translation(salon.name, "uk")


def _confirm_link_prefix(salon) -> str:
    return build_salon_frontend_url(salon.slug, "/confirm-email-change") + "#token="


def _only_letter_to(address: str):
    letters = [m for m in mail.outbox if m.to == [address]]
    assert len(letters) == 1, f"expected one letter to {address}, got {len(letters)}"
    return letters[0]


def _token_from(letter, salon) -> str:
    match = re.search(re.escape(_confirm_link_prefix(salon)) + r"(\S+)", letter.body)
    assert match is not None, "no confirmation link in the letter"
    return match.group(1)


def _assert_link_letter(letter, salon) -> None:
    assert letter.subject == SUBJECT_CONFIRM
    assert _salon_name(salon) in letter.body
    assert _confirm_link_prefix(salon) in letter.body


def _assert_taken_letter(letter) -> None:
    assert letter.subject == SUBJECT_TAKEN
    assert TAKEN_TEXT in letter.body
    assert "#token=" not in letter.body
    assert "/confirm-email-change" not in letter.body


def _assert_notice_letter(letter, salon, new_email: str) -> None:
    assert letter.subject == SUBJECT_NOTICE
    assert new_email in letter.body
    assert _salon_name(salon) in letter.body


def _reload_account(account) -> Account:
    return Account.unscoped_objects.get(pk=account.pk)


def _reload_customer(customer) -> Customer:
    return Customer.unscoped_objects.get(pk=customer.pk)


# --- 1. Free address ---------------------------------------------------------


def test_free_address_sends_the_link_and_the_notice_and_changes_nothing(
    client, salon, customer, customer_account
):
    client.force_authenticate(user=customer_account)

    response = _post(client, salon, NEW_EMAIL)

    assert response.status_code == 202
    assert response.content == b""
    assert len(mail.outbox) == 2
    _assert_link_letter(_only_letter_to(NEW_EMAIL), salon)
    _assert_notice_letter(_only_letter_to(customer_account.email), salon, NEW_EMAIL)
    assert _reload_account(customer_account).email == "alice-account@example.com"
    assert _reload_customer(customer).email == "alice@example.com"


# --- 2-4. Taken address: same 202, no-link letter, notice still sent ---------


def test_address_of_another_account_in_the_salon_gets_the_no_link_letter(
    client, salon, customer_account
):
    with tenant_context(salon.id):
        Account.objects.create_account(salon=salon, email=TAKEN_EMAIL, password=PASSWORD)
    client.force_authenticate(user=customer_account)

    response = _post(client, salon, TAKEN_EMAIL)

    assert response.status_code == 202
    _assert_taken_letter(_only_letter_to(TAKEN_EMAIL))
    _assert_notice_letter(_only_letter_to(customer_account.email), salon, TAKEN_EMAIL)


def test_address_of_an_unlinked_guest_customer_in_the_salon_gets_the_no_link_letter(
    client, salon, customer_account
):
    with tenant_context(salon.id):
        Customer.objects.create(salon=salon, name="Guest", email=TAKEN_EMAIL, phone="+10000000001")
    client.force_authenticate(user=customer_account)

    response = _post(client, salon, TAKEN_EMAIL)

    assert response.status_code == 202
    _assert_taken_letter(_only_letter_to(TAKEN_EMAIL))
    _assert_notice_letter(_only_letter_to(customer_account.email), salon, TAKEN_EMAIL)


def test_taken_check_ignores_letter_case_of_a_stored_guest_customer(
    client, salon, customer_account
):
    with tenant_context(salon.id):
        Customer.objects.create(
            salon=salon, name="Guest", email="Taken@Example.com", phone="+10000000001"
        )
    client.force_authenticate(user=customer_account)

    response = _post(client, salon, TAKEN_EMAIL)

    assert response.status_code == 202
    _assert_taken_letter(_only_letter_to(TAKEN_EMAIL))
    _assert_notice_letter(_only_letter_to(customer_account.email), salon, TAKEN_EMAIL)


# --- 5-6. Addresses that do not count as taken --------------------------------


def test_email_of_the_accounts_own_linked_customer_counts_as_free(
    client, salon, customer, customer_account
):
    client.force_authenticate(user=customer_account)

    response = _post(client, salon, customer.email)

    assert response.status_code == 202
    _assert_link_letter(_only_letter_to(customer.email), salon)


def test_address_taken_only_in_another_salon_counts_as_free(
    client, salon, other_salon, customer_account
):
    with tenant_context(other_salon.id):
        Account.objects.create_account(salon=other_salon, email=TAKEN_EMAIL, password=PASSWORD)
        Customer.objects.create(
            salon=other_salon, name="Other", email=TAKEN_EMAIL, phone="+10000000002"
        )
    client.force_authenticate(user=customer_account)

    response = _post(client, salon, TAKEN_EMAIL)

    assert response.status_code == 202
    _assert_link_letter(_only_letter_to(TAKEN_EMAIL), salon)


# --- 7. No enumeration --------------------------------------------------------


def test_free_and_taken_responses_are_byte_identical(client, salon, customer_account):
    with tenant_context(salon.id):
        Account.objects.create_account(salon=salon, email=TAKEN_EMAIL, password=PASSWORD)
    client.force_authenticate(user=customer_account)

    free = _post(client, salon, NEW_EMAIL)
    taken = _post(client, salon, TAKEN_EMAIL)

    assert free.status_code == 202
    assert taken.status_code == free.status_code
    assert taken.content == free.content


# --- 8-9. Normalization and the token -----------------------------------------


def test_new_email_is_stripped_and_lowercased(client, salon, customer_account):
    client.force_authenticate(user=customer_account)

    response = _post(client, salon, " New@Example.COM ")

    assert response.status_code == 202
    letter = _only_letter_to(NEW_EMAIL)
    _assert_link_letter(letter, salon)
    payload = signing.TimestampSigner(salt=EMAIL_CHANGE_SALT).unsign_object(
        _token_from(letter, salon), max_age=EMAIL_CHANGE_MAX_AGE
    )
    assert payload["new_email"] == NEW_EMAIL


def test_token_carries_account_and_both_emails_and_is_not_a_verification_token(
    client, salon, customer_account
):
    client.force_authenticate(user=customer_account)

    response = _post(client, salon, NEW_EMAIL)

    assert response.status_code == 202
    token = _token_from(_only_letter_to(NEW_EMAIL), salon)
    payload = signing.TimestampSigner(salt=EMAIL_CHANGE_SALT).unsign_object(
        token, max_age=EMAIL_CHANGE_MAX_AGE
    )
    assert payload == {
        "account_id": customer_account.id,
        "old_email": "alice-account@example.com",
        "new_email": NEW_EMAIL,
    }
    with tenant_context(salon.id), pytest.raises(signing.BadSignature):
        read_account_verification_token(token)


# --- 10-12. Rejected requests: 400, no mail ----------------------------------


def test_wrong_password_is_400_invalid_password_and_sends_nothing(client, salon, customer_account):
    client.force_authenticate(user=customer_account)

    response = _post(client, salon, NEW_EMAIL, password="wrong-password")

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_password"
    assert mail.outbox == []


def test_current_email_up_to_case_and_spaces_is_400_same_email(client, salon, customer_account):
    client.force_authenticate(user=customer_account)

    response = _post(client, salon, "  Alice-Account@Example.COM ")

    assert response.status_code == 400
    assert response.data["error"]["code"] == "same_email"
    assert mail.outbox == []


def test_invalid_email_format_is_a_400_field_error(client, salon, customer_account):
    client.force_authenticate(user=customer_account)

    response = _post(client, salon, "not-an-email")

    assert response.status_code == 400
    assert "new_email" in response.data["error"]["details"]
    assert mail.outbox == []


# --- 13-14. Authentication and tenant isolation ------------------------------


def test_unauthenticated_request_is_401(client, salon, customer_account):
    response = _post(client, salon, NEW_EMAIL)

    assert response.status_code == 401
    assert mail.outbox == []


def test_account_logged_in_to_another_salon_gets_401(client, salon, other_salon, customer_account):
    with tenant_context(other_salon.id):
        other_account = Account.objects.create_account(
            salon=other_salon,
            email="bob@example.com",
            password=PASSWORD,
            role=AccountRole.CLIENT,
        )
    login = client.post(
        _login_url(other_salon),
        {"email": other_account.email, "password": PASSWORD},
        format="json",
    )
    assert login.status_code == 200

    response = _post(client, salon, NEW_EMAIL)

    assert response.status_code == 401
    assert mail.outbox == []


# --- 15. Throttle --------------------------------------------------------------


def test_fourth_request_in_an_hour_is_429_and_wrong_passwords_count(
    client, salon, customer_account
):
    client.force_authenticate(user=customer_account)

    first = _post(client, salon, NEW_EMAIL, password="wrong-password")
    second = _post(client, salon, NEW_EMAIL, password="wrong-password")
    third = _post(client, salon, NEW_EMAIL)
    fourth = _post(client, salon, NEW_EMAIL)

    assert [first.status_code, second.status_code, third.status_code] == [400, 400, 202]
    assert fourth.status_code == 429
