"""
Stage 3-R.D.5 — resend-verification endpoint
(``POST /api/v1/salons/<slug>/auth/resend-verification/``;
docs/DECISIONS.md § Stage 3-R.D.5).

RED phase: written against the agreed contract before
``ResendVerificationView`` or its URL tail exist. Nothing here imports from
the unwritten view — only ``accounts.tokens`` (the D.3/D.4
verification-token helpers the resend path reuses as-is), ``accounts.models``
and ``core.tenancy`` — so collection succeeds and every pre-implementation
failure is a clean 404: the ``salons/<slug>/`` prefix resolves, the
``auth/resend-verification/`` tail does not yet. The already-verified test
sets its account up through the *existing* D.4 verify endpoint, so its only
pre-implementation failure is the resend call itself.

The contract under test (docs/DECISIONS.md § Stage 3-R.D.5):

* an identical empty ``202`` regardless of outcome;
* a fresh verification email is sent ONLY when the account exists in the
  bound salon AND is still unverified;
* an already-verified account is a deliberate silent no-op (empty ``202``,
  no mail) — against joe-job flooding of a stranger's confirmed inbox, and
  so verified-state does not leak;
* unknown email and wrong-salon email are likewise empty ``202`` with no
  mail;
* throttled at the ``resend_verification`` rate (3/hour).
"""

import pytest
from django.conf import settings
from django.core import mail
from rest_framework.test import APIClient

from accounts import tokens
from accounts.models import Account
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "a-strong-passw0rd!"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture(autouse=True)
def _subdomain_frontend_url(settings):
    # Credential-email links are subdomain-based: FRONTEND_URL carries a
    # `{slug}` placeholder filled per salon via `.format(slug=...)` — see
    # docs/DECISIONS.md § "Frontend routing: subdomain-based". Mirrors the
    # production `.env` pattern (`FRONTEND_URL=http://{slug}.localhost:3000`).
    settings.FRONTEND_URL = "http://{slug}.testserver"


def _resend_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/resend-verification/"


def _verify_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/verify-email/"


def _make_account(salon, email: str = "resend-me@example.com") -> Account:
    with tenant_context(salon.id):
        return Account.objects.create_account(salon=salon, email=email, password=STRONG_PASSWORD)


def _mint_verify_token(account: Account, salon) -> str:
    with tenant_context(salon.id):
        return tokens.generate_account_verification_token(account)


def _assert_empty_202(response) -> None:
    assert response.status_code == 202
    assert response.data is None
    assert response.content == b""


def test_resend_for_unverified_account_returns_202_empty_and_sends_one_verification_email(
    client, salon
) -> None:
    account = _make_account(salon)

    response = client.post(_resend_url(salon.slug), {"email": account.email}, format="json")

    _assert_empty_202(response)

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == [account.email]
    assert f"{settings.FRONTEND_URL.format(slug=salon.slug)}/verify-email#token=" in sent.body

    # The emailed link carries a fresh, working verification token for this
    # account.
    emailed_token = sent.body.split("#token=")[1].split()[0]
    with tenant_context(salon.id):
        assert tokens.read_account_verification_token(emailed_token) == account.pk


def test_resend_for_already_verified_account_returns_202_empty_and_sends_nothing(
    client, salon
) -> None:
    account = _make_account(salon)
    # Verify through the real D.4 endpoint, so email_verified_at is set the
    # same way production sets it.
    verified = client.post(
        _verify_url(salon.slug),
        {"token": _mint_verify_token(account, salon)},
        format="json",
    )
    assert verified.status_code == 204
    mail.outbox.clear()

    # Resend against an already-verified address: deliberate silent no-op —
    # identical empty 202, and no mail at all (anti joe-job, no
    # verified-state leak).
    response = client.post(_resend_url(salon.slug), {"email": account.email}, format="json")

    _assert_empty_202(response)
    assert mail.outbox == []


def test_resend_for_unknown_email_returns_identical_202_empty_and_sends_nothing(
    client, salon
) -> None:
    response = client.post(
        _resend_url(salon.slug), {"email": "no-such-account@example.com"}, format="json"
    )

    _assert_empty_202(response)
    assert mail.outbox == []


def test_resend_for_account_in_another_salon_returns_202_empty_and_sends_nothing(
    client, salon, other_salon
) -> None:
    account = _make_account(salon, email="cross-resend@example.com")

    response = client.post(_resend_url(other_salon.slug), {"email": account.email}, format="json")

    _assert_empty_202(response)
    assert mail.outbox == []


def test_resend_is_throttled_after_the_configured_rate(client, salon) -> None:
    for i in range(3):  # resend_verification: 3/hour (docs/DECISIONS.md § Stage 3-R.D.5)
        response = client.post(
            _resend_url(salon.slug),
            {"email": f"throttle-{i}@example.com"},
            format="json",
        )
        assert response.status_code == 202

    response = client.post(
        _resend_url(salon.slug),
        {"email": "throttle-over@example.com"},
        format="json",
    )
    assert response.status_code == 429
