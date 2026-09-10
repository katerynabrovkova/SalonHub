"""
Stage 3-R.D.5 — password-reset endpoints
(``POST /api/v1/salons/<slug>/auth/password-reset/`` and
``.../password-reset/confirm/``; docs/DECISIONS.md § Stage 3-R.D.5).

RED phase: written against the agreed contract before
``PasswordResetRequestView`` / ``PasswordResetConfirmView`` or their URL
tails exist. Nothing here imports from the unwritten views — only Django's
own password-reset primitives (``default_token_generator``, the
``urlsafe_base64``/``force_bytes`` helpers), ``accounts.models`` and
``core.tenancy`` — so collection succeeds and every pre-implementation
failure is a clean 404: the ``salons/<slug>/`` prefix resolves, the
``auth/password-reset/`` and ``auth/password-reset/confirm/`` tails do not
yet (``test_confirm_get_is_405`` is a 404 too until the route lands).

The contract under test (docs/DECISIONS.md § Stage 3-R.D.5):

* reset-request: an identical empty ``202`` whether or not an account with
  that email exists in the bound salon; a reset email is enqueued only when
  one does, and the lookup never reaches past the bound tenant. Throttled at
  the ``password_reset`` rate (3/hour).
* reset-confirm: ``{uid, token, new_password}`` -> empty ``204`` and the
  password is actually changed. The generator token self-invalidates on the
  password change (single use). Every uid/token failure — malformed base64,
  unknown account, tampered or expired token, wrong salon — collapses to one
  neutral ``400`` with ``error.code == "invalid_or_expired_token"`` and
  leaves the password untouched. A weak ``new_password`` is a *field-level*
  ``400`` (password strength), not that collapse, and also leaves the
  password untouched.
"""

import pytest
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from accounts.models import Account
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "a-strong-passw0rd!"
NEW_PASSWORD = "an-even-str0nger-passphrase!"
OTHER_NEW_PASSWORD = "y3t-another-genu1ne-secret!"


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


def _reset_request_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/password-reset/"


def _reset_confirm_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/password-reset/confirm/"


def _make_account(salon, email: str = "reset-me@example.com") -> Account:
    with tenant_context(salon.id):
        return Account.objects.create_account(salon=salon, email=email, password=STRONG_PASSWORD)


def _encode_uid(pk: int) -> str:
    return urlsafe_base64_encode(force_bytes(pk))


def _make_reset_token(account: Account) -> str:
    return default_token_generator.make_token(account)


def _assert_empty_202(response) -> None:
    assert response.status_code == 202
    assert response.data is None
    assert response.content == b""


# --- reset-request: POST auth/password-reset/ ---------------------------


def test_reset_request_for_existing_account_returns_202_empty_and_sends_one_salon_aware_email(
    client, salon
) -> None:
    account = _make_account(salon)

    response = client.post(_reset_request_url(salon.slug), {"email": account.email}, format="json")

    _assert_empty_202(response)

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == [account.email]
    assert f"{settings.FRONTEND_URL.format(slug=salon.slug)}/reset-password#uid=" in sent.body

    # The emailed link carries a working credential, not just the right URL
    # shape: the uid is this account's, and the token verifies against it.
    after = sent.body.partition("#uid=")[2]
    uid_value, _, token_tail = after.partition("&token=")
    token_value = token_tail.split()[0]
    assert uid_value == _encode_uid(account.pk)
    assert default_token_generator.check_token(account, token_value)


def test_reset_request_for_unknown_email_returns_identical_202_empty_and_sends_nothing(
    client, salon
) -> None:
    # No account exists. The response must be byte-identical to the
    # existing-account branch above (status 202, empty body); the ONLY
    # observable difference is that no mail is sent.
    response = client.post(
        _reset_request_url(salon.slug),
        {"email": "nobody-here@example.com"},
        format="json",
    )

    _assert_empty_202(response)
    assert mail.outbox == []


def test_reset_request_for_account_in_another_salon_returns_202_empty_and_sends_nothing(
    client, salon, other_salon
) -> None:
    account = _make_account(salon, email="cross@example.com")

    # Same address, POSTed to a different salon: the lookup is tenant-scoped
    # and must not see the account — identical empty 202, no mail, no
    # cross-salon existence leak.
    response = client.post(
        _reset_request_url(other_salon.slug), {"email": account.email}, format="json"
    )

    _assert_empty_202(response)
    assert mail.outbox == []


def test_reset_request_is_throttled_after_the_configured_rate(client, salon) -> None:
    for i in range(3):  # password_reset: 3/hour (docs/DECISIONS.md § Stage 3-R.D.5)
        response = client.post(
            _reset_request_url(salon.slug),
            {"email": f"throttle-{i}@example.com"},
            format="json",
        )
        assert response.status_code == 202

    response = client.post(
        _reset_request_url(salon.slug),
        {"email": "throttle-over@example.com"},
        format="json",
    )
    assert response.status_code == 429


# --- reset-confirm: POST auth/password-reset/confirm/ ------------------


def test_valid_uid_and_token_and_strong_password_returns_204_and_changes_the_password(
    client, salon
) -> None:
    account = _make_account(salon)
    uid = _encode_uid(account.pk)
    token = _make_reset_token(account)

    response = client.post(
        _reset_confirm_url(salon.slug),
        {"uid": uid, "token": token, "new_password": NEW_PASSWORD},
        format="json",
    )

    assert response.status_code == 204
    assert response.data is None

    row = Account.unscoped_objects.get(pk=account.pk)
    assert row.check_password(NEW_PASSWORD)
    assert not row.check_password(STRONG_PASSWORD)


def test_reset_token_is_single_use_second_confirm_with_same_token_returns_neutral_400(
    client, salon
) -> None:
    account = _make_account(salon)
    uid = _encode_uid(account.pk)
    token = _make_reset_token(account)

    first = client.post(
        _reset_confirm_url(salon.slug),
        {"uid": uid, "token": token, "new_password": NEW_PASSWORD},
        format="json",
    )
    assert first.status_code == 204

    # The generator hash folds in the current password hash, so changing the
    # password invalidates the very token that authorised the change.
    second = client.post(
        _reset_confirm_url(salon.slug),
        {"uid": uid, "token": token, "new_password": OTHER_NEW_PASSWORD},
        format="json",
    )

    assert second.status_code == 400
    assert second.data["error"]["code"] == "invalid_or_expired_token"

    row = Account.unscoped_objects.get(pk=account.pk)
    assert row.check_password(NEW_PASSWORD)
    assert not row.check_password(OTHER_NEW_PASSWORD)


def test_confirm_with_malformed_base64_uid_returns_neutral_400(client, salon) -> None:
    account = _make_account(salon)
    token = _make_reset_token(account)

    response = client.post(
        _reset_confirm_url(salon.slug),
        {"uid": "!!!!not-base64!!!!", "token": token, "new_password": NEW_PASSWORD},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"
    assert Account.unscoped_objects.get(pk=account.pk).check_password(STRONG_PASSWORD)


def test_confirm_with_uid_naming_no_account_returns_neutral_400(client, salon) -> None:
    # A well-formed uid that decodes to a pk no account has.
    response = client.post(
        _reset_confirm_url(salon.slug),
        {
            "uid": _encode_uid(999_999),
            "token": "irrelevant-token",
            "new_password": NEW_PASSWORD,
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"


def test_confirm_with_tampered_token_returns_neutral_400(client, salon) -> None:
    account = _make_account(salon)
    uid = _encode_uid(account.pk)
    token = _make_reset_token(account)
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")

    response = client.post(
        _reset_confirm_url(salon.slug),
        {"uid": uid, "token": tampered, "new_password": NEW_PASSWORD},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"
    assert Account.unscoped_objects.get(pk=account.pk).check_password(STRONG_PASSWORD)


def test_confirm_with_expired_token_returns_neutral_400(client, salon, settings) -> None:
    account = _make_account(salon)
    uid = _encode_uid(account.pk)
    token = _make_reset_token(account)

    # Django's PasswordResetTokenGenerator reads PASSWORD_RESET_TIMEOUT at
    # check time; a negative timeout ages every token out instantly.
    settings.PASSWORD_RESET_TIMEOUT = -1

    response = client.post(
        _reset_confirm_url(salon.slug),
        {"uid": uid, "token": token, "new_password": NEW_PASSWORD},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"
    assert Account.unscoped_objects.get(pk=account.pk).check_password(STRONG_PASSWORD)


def test_confirm_with_weak_new_password_returns_field_level_400_and_does_not_change_the_password(
    client, salon
) -> None:
    account = _make_account(salon)
    uid = _encode_uid(account.pk)
    token = _make_reset_token(account)

    response = client.post(
        _reset_confirm_url(salon.slug),
        {"uid": uid, "token": token, "new_password": "123"},
        format="json",
    )

    assert response.status_code == 400
    # Password strength is a field validation error, NOT the neutral
    # token-failure collapse.
    assert response.data["error"]["code"] != "invalid_or_expired_token"
    assert "password" in str(response.data).lower()

    assert Account.unscoped_objects.get(pk=account.pk).check_password(STRONG_PASSWORD)


def test_confirm_with_uid_and_token_minted_in_another_salon_returns_neutral_400(
    client, salon, other_salon
) -> None:
    account = _make_account(salon, email="cross-confirm@example.com")
    uid = _encode_uid(account.pk)
    token = _make_reset_token(account)

    # Valid credentials, wrong salon in the URL: the account load is
    # tenant-scoped and misses, collapsing to the same neutral 400.
    response = client.post(
        _reset_confirm_url(other_salon.slug),
        {"uid": uid, "token": token, "new_password": NEW_PASSWORD},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"
    assert Account.unscoped_objects.get(pk=account.pk).check_password(STRONG_PASSWORD)


def test_confirm_get_is_405(client, salon) -> None:
    response = client.get(_reset_confirm_url(salon.slug))

    assert response.status_code == 405
