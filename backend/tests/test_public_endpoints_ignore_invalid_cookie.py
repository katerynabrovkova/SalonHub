"""
Public (AllowAny) auth endpoints must ignore an invalid ``access_token`` cookie.

RED phase. Today these views inherit the project-default authentication
classes, so ``AccountJWTCookieAuthentication`` validates the cookie during
``initial()`` — before permissions are consulted — and a stale or garbage
cookie turns a public request into ``401 token_not_valid``. The planned fix
(green phase) opts exactly these six views out of authentication.

Tests 1-6: for each public view, a request carrying a garbage cookie must get
exactly the status and error code the same request gets with no cookie, and
never ``token_not_valid``. Tests 7-8 guard against over-fixing: ``MeView`` and
``LogoutView`` must keep rejecting an invalid cookie with 401 (they already do).
"""

import datetime as dt

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "a-strong-passw0rd!"
ACCESS_COOKIE = "access_token"
GARBAGE = "this-is-not-a-jwt"


def _url(slug: str, tail: str) -> str:
    return f"/api/v1/salons/{slug}/auth/{tail}/"


def _client_with_cookie(value: str | None) -> APIClient:
    client = APIClient()
    if value is not None:
        client.cookies[ACCESS_COOKIE] = value
    return client


def _expired_access_token() -> str:
    """A genuinely expired, correctly signed access JWT (not a garbage string)."""
    token = AccessToken()
    token["user_id"] = "1"
    token["identity_model"] = "account"
    token.set_exp(from_time=timezone.now() - dt.timedelta(days=1), lifetime=dt.timedelta(minutes=15))
    return str(token)


def _error_code(response) -> str | None:
    data = getattr(response, "data", None)
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return data["error"].get("code")
    return None


def _assert_same_as_no_cookie(baseline, other) -> None:
    assert _error_code(other) != "token_not_valid"
    assert other.status_code != 401
    assert other.status_code == baseline.status_code
    assert _error_code(other) == _error_code(baseline)


def _post_pair(slug: str, tail: str, body: dict):
    url = _url(slug, tail)
    baseline = _client_with_cookie(None).post(url, body, format="json")
    garbage = _client_with_cookie(GARBAGE).post(url, body, format="json")
    return baseline, garbage


# --- 1-6. public views ignore an invalid cookie ---------------------------


def test_register_ignores_invalid_access_cookie(salon) -> None:
    body = {"email": "cookie-register@example.com", "password": STRONG_PASSWORD}
    url = _url(salon.slug, "register")

    baseline = _client_with_cookie(None).post(url, body, format="json")
    garbage = _client_with_cookie(GARBAGE).post(url, body, format="json")
    expired = _client_with_cookie(_expired_access_token()).post(url, body, format="json")

    assert baseline.status_code == 202
    _assert_same_as_no_cookie(baseline, garbage)
    _assert_same_as_no_cookie(baseline, expired)


def test_verify_email_ignores_invalid_access_cookie(salon) -> None:
    # Invalid token -> deterministic neutral 400, no side effects.
    baseline, garbage = _post_pair(salon.slug, "verify-email", {"token": "not-a-real-token"})

    assert baseline.status_code == 400
    _assert_same_as_no_cookie(baseline, garbage)


def test_password_reset_request_ignores_invalid_access_cookie(salon) -> None:
    # Malformed email -> serializer 400, nothing sent.
    baseline, garbage = _post_pair(salon.slug, "password-reset", {"email": "not-an-email"})

    assert baseline.status_code == 400
    _assert_same_as_no_cookie(baseline, garbage)


def test_password_reset_confirm_ignores_invalid_access_cookie(salon) -> None:
    # Missing fields -> serializer 400, no password changed.
    baseline, garbage = _post_pair(salon.slug, "password-reset/confirm", {})

    assert baseline.status_code == 400
    _assert_same_as_no_cookie(baseline, garbage)


def test_resend_verification_ignores_invalid_access_cookie(salon) -> None:
    baseline, garbage = _post_pair(salon.slug, "resend-verification", {"email": "not-an-email"})

    assert baseline.status_code == 400
    _assert_same_as_no_cookie(baseline, garbage)


def test_auth_csrf_ignores_invalid_access_cookie(salon) -> None:
    url = _url(salon.slug, "csrf")

    baseline = _client_with_cookie(None).get(url)
    garbage = _client_with_cookie(GARBAGE).get(url)

    assert baseline.status_code == 204
    _assert_same_as_no_cookie(baseline, garbage)


# --- 7-8. protected views still reject an invalid cookie ------------------


def test_me_with_invalid_access_cookie_is_still_401(salon) -> None:
    response = _client_with_cookie(GARBAGE).get(_url(salon.slug, "me"))

    assert response.status_code == 401


def test_logout_with_invalid_access_cookie_is_still_401(salon) -> None:
    response = _client_with_cookie(GARBAGE).post(_url(salon.slug, "logout"), {}, format="json")

    assert response.status_code == 401
