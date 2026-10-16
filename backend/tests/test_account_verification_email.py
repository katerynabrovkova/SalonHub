"""
The verification email in Ukrainian with the salon name (docs/DECISIONS.md
§ "Item 13 decisions (verification reminder)", point 1).

Both senders are covered: registration of a new account and
resend-verification for an unverified one. The silent no-op cases of resend
(unknown address, already verified account, another salon) stay pinned in
``test_account_resend_verification.py``.
"""

import pytest
from django.conf import settings
from django.core import mail
from rest_framework.test import APIClient

from accounts.models import Account
from core.i18n import resolve_translation
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "a-strong-passw0rd!"

# docs/DECISIONS.md § "Item 13 decisions (verification reminder)", point 1.
SUBJECT_VERIFY = "Підтвердження пошти"
GREETING = "Вітаємо!"
LINK_TEXT = "Щоб підтвердити пошту для акаунта в салоні {salon}, перейдіть за посиланням: {link}"
VALIDITY_TEXT = "Посилання діє 48 годин."
NOT_YOU_TEXT = "Якщо ви не реєструвалися, просто проігноруйте цей лист."


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture(autouse=True)
def _subdomain_frontend_url(settings):
    # Same subdomain-based FRONTEND_URL as the other credential-email tests
    # (docs/DECISIONS.md § "Frontend routing: subdomain-based").
    settings.FRONTEND_URL = "http://{slug}.testserver"


def _register_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/register/"


def _resend_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/resend-verification/"


def _verify_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/verify-email/"


def _make_account(salon, email: str = "resend-me@example.com") -> Account:
    with tenant_context(salon.id):
        return Account.objects.create_account(salon=salon, email=email, password=STRONG_PASSWORD)


def _salon_name(salon) -> str:
    return resolve_translation(salon.name, "uk")


def _token_from(body: str) -> str:
    """The token of the verification link in an email body."""
    return body.partition("#token=")[2].split()[0]


def _assert_agreed_letter(letter, *, to: str, salon) -> None:
    assert letter.to == [to]
    assert letter.subject == SUBJECT_VERIFY

    salon_name = _salon_name(salon)
    body = letter.body
    # The token is per-send, so the sentence is matched up to "#token=".
    link_start = f"{settings.FRONTEND_URL.format(slug=salon.slug)}/verify-email#token="
    link_text = LINK_TEXT.format(salon=salon_name, link=link_start)
    assert GREETING in body
    assert link_text in body
    assert VALIDITY_TEXT in body
    assert NOT_YOU_TEXT in body
    assert (
        body.index(GREETING)
        < body.index(link_text)
        < body.index(VALIDITY_TEXT)
        < body.index(NOT_YOU_TEXT)
    )
    assert body.rstrip().endswith(salon_name)


# --- registration: POST auth/register/ ---------------------------------


def test_registration_email_is_ukrainian_with_the_salon_name_and_the_link_in_the_agreed_order(
    client, salon
) -> None:
    response = client.post(
        _register_url(salon.slug),
        {"email": "new-client@example.com", "password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 202
    assert len(mail.outbox) == 1
    _assert_agreed_letter(mail.outbox[0], to="new-client@example.com", salon=salon)


def test_registration_email_link_verifies_the_account(client, salon) -> None:
    client.post(
        _register_url(salon.slug),
        {"email": "new-client@example.com", "password": STRONG_PASSWORD},
        format="json",
    )
    token = _token_from(mail.outbox[0].body)

    response = client.post(_verify_url(salon.slug), {"token": token}, format="json")

    assert response.status_code == 204
    account = Account.unscoped_objects.get(salon=salon, email="new-client@example.com")
    assert account.email_verified_at is not None


# --- resend: POST auth/resend-verification/ ----------------------------


def test_resend_email_is_the_same_ukrainian_letter(client, salon) -> None:
    account = _make_account(salon)

    response = client.post(_resend_url(salon.slug), {"email": account.email}, format="json")

    assert response.status_code == 202
    assert len(mail.outbox) == 1
    _assert_agreed_letter(mail.outbox[0], to=account.email, salon=salon)


def test_resend_email_link_verifies_the_account(client, salon) -> None:
    account = _make_account(salon)
    client.post(_resend_url(salon.slug), {"email": account.email}, format="json")
    token = _token_from(mail.outbox[0].body)

    response = client.post(_verify_url(salon.slug), {"token": token}, format="json")

    assert response.status_code == 204
    assert Account.unscoped_objects.get(pk=account.pk).email_verified_at is not None
