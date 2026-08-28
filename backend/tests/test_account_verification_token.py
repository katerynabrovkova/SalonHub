"""
Stage 3-R.D.3 — account email-verification token
(``accounts/tokens.py``; docs/DECISIONS.md § Stage 3-R.D.3).

The token payload is ``{account_id, email}`` — salon is deliberately NOT in
it (it is known from the URL the emailed link points at). ``read_`` is
tenant-scoped on top of that: it resolves the account through
``Account.objects``, so a token minted in one salon cannot be read under
another (``test_token_minted_for_another_salon_is_not_readable_here``).

D.3 only generates and round-trips the token — the verify endpoint that
consumes it in production is D.4, same as ``booking/guest_tokens`` was
unit-tested a sub-step before its endpoint existed.
"""

import pytest
from django.core import signing

from accounts import tokens
from accounts.models import Account
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "a-strong-passw0rd!"


def _make_account(salon, email: str = "verify-me@example.com") -> Account:
    with tenant_context(salon.id):
        return Account.objects.create_account(salon=salon, email=email, password=STRONG_PASSWORD)


def test_generate_then_read_round_trips_to_account_id(salon) -> None:
    account = _make_account(salon)
    with tenant_context(salon.id):
        token = tokens.generate_account_verification_token(account)
        assert tokens.read_account_verification_token(token) == account.id


def test_tampered_token_raises_bad_signature(salon) -> None:
    account = _make_account(salon)
    with tenant_context(salon.id):
        token = tokens.generate_account_verification_token(account)
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
        with pytest.raises(signing.BadSignature):
            tokens.read_account_verification_token(tampered)


def test_expired_token_raises_signature_expired(salon, monkeypatch) -> None:
    account = _make_account(salon)
    with tenant_context(salon.id):
        token = tokens.generate_account_verification_token(account)
        monkeypatch.setattr(tokens, "ACCOUNT_VERIFICATION_TOKEN_MAX_AGE", -1)
        with pytest.raises(signing.SignatureExpired):
            tokens.read_account_verification_token(token)


def test_token_for_a_changed_email_is_rejected(salon) -> None:
    account = _make_account(salon)
    with tenant_context(salon.id):
        token = tokens.generate_account_verification_token(account)
        Account.objects.filter(pk=account.pk).update(email="changed@example.com")
        with pytest.raises(signing.BadSignature):
            tokens.read_account_verification_token(token)


def test_token_minted_for_another_salon_is_not_readable_here(salon, other_salon) -> None:
    account = _make_account(salon, email="cross@example.com")
    with tenant_context(salon.id):
        token = tokens.generate_account_verification_token(account)
    with tenant_context(other_salon.id):
        with pytest.raises(signing.BadSignature):
            tokens.read_account_verification_token(token)
