"""
Stage 12 — CSRF protection for cookie-authenticated unsafe requests
(docs/DECISIONS.md § Stage 12 "CSRF protection resolved").

RED phase: written against the agreed contract before any CSRF enforcement
exists. Nothing here imports an unwritten symbol, so collection succeeds.

Today, with no enforcement wired in:

* the tests that assert a **403** (missing / wrong `X-CSRFToken` on a
  cookie-authenticated unsafe request, or on login / refresh / logout) FAIL —
  the request currently goes through with its normal 2xx. That failure is the
  gap this sub-step closes.
* the tests that assert **success** (correct token present; Bearer-header auth
  exempt; guest-token auth exempt) PASS today and must keep passing after the
  green phase — they pin that enforcement does not over-block.
* `test_csrf_priming_endpoint_returns_204_and_sets_cookie` FAILS today because
  the `auth/csrf/` route does not exist yet (404).

The contract under test (docs/DECISIONS.md § Stage 12):

* `GET auth/csrf/` -> 204, sets the `csrftoken` cookie (AllowAny).
* An unsafe request whose access token came from the **cookie** must carry a
  valid `X-CSRFToken` header (Django's CSRF engine, default names). Missing or
  wrong -> 403.
* `auth/login/`, `auth/refresh/`, `auth/logout/` are in scope (login-CSRF
  included, decided 2026-09-10).
* A request authenticated by `Authorization: Bearer` (no cookies) is exempt.
* A guest-token request (`X-Guest-Token` header, no cookies) is exempt —
  out of scope, already CSRF-immune.

The client here is `APIClient(enforce_csrf_checks=True)`: Django's test client
sets `_dont_enforce_csrf_checks` unless this flag is on, and the planned
`enforce_csrf`-style check honours that flag exactly as
`SessionAuthentication.enforce_csrf()` does.
"""

import datetime as dt

import pytest
from django.middleware.csrf import get_token
from django.test import RequestFactory
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from booking.guest_tokens import issue_guest_token
from core.tenancy import tenant_context
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "a-strong-passw0rd!"
START = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrftoken"


@pytest.fixture
def client() -> APIClient:
    return APIClient(enforce_csrf_checks=True)


def _csrf_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/csrf/"


def _categories_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/categories/"


def _login_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/login/"


def _refresh_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/refresh/"


def _logout_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/logout/"


def _cancel_url(slug: str, appointment_id: int) -> str:
    return f"/api/v1/salons/{slug}/guest/appointments/{appointment_id}/cancel/"


def _account_access_token(account) -> str:
    # Built the way AccountTokenObtainPairSerializer.get_token() does — not
    # RefreshToken.for_user(), which eagerly writes a User-FK OutstandingToken.
    token = AccessToken()
    token["user_id"] = str(account.pk)
    token["identity_model"] = "account"
    return str(token)


def _account_refresh_token(account) -> str:
    token = RefreshToken()
    token["user_id"] = str(account.pk)
    token["identity_model"] = "account"
    return str(token)


def _fresh_csrf_token() -> str:
    """A masked CSRF token plus the matching secret. Django's engine unmasks
    both the cookie value and the header value to the same secret, so using
    this one string for both sides is a valid pair."""
    return get_token(RequestFactory().get("/"))


def _prime_csrf(client: APIClient) -> str:
    token = _fresh_csrf_token()
    client.cookies[CSRF_COOKIE] = token
    return token


# --- 1. priming endpoint --------------------------------------------------


def test_csrf_priming_endpoint_returns_204_and_sets_cookie(client, salon) -> None:
    response = client.get(_csrf_url(salon.slug))

    assert response.status_code == 204
    assert not response.content  # 204, no body
    assert response.cookies.get(CSRF_COOKIE) is not None


# --- 2-5. account-authenticated write (cookie) ---------------------------


def test_account_write_without_csrf_header_is_rejected(client, salon, admin_account) -> None:
    client.cookies[ACCESS_COOKIE] = _account_access_token(admin_account)
    _prime_csrf(client)

    response = client.post(_categories_url(salon.slug), {"name": {"en": "No CSRF"}}, format="json")

    assert response.status_code == 403


def test_account_write_with_correct_csrf_header_succeeds(client, salon, admin_account) -> None:
    client.cookies[ACCESS_COOKIE] = _account_access_token(admin_account)
    csrf = _prime_csrf(client)

    response = client.post(
        _categories_url(salon.slug),
        {"name": {"en": "With CSRF"}},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )

    assert response.status_code == 201


def test_account_write_with_wrong_csrf_header_is_rejected(client, salon, admin_account) -> None:
    client.cookies[ACCESS_COOKIE] = _account_access_token(admin_account)
    _prime_csrf(client)

    response = client.post(
        _categories_url(salon.slug),
        {"name": {"en": "Wrong CSRF"}},
        format="json",
        HTTP_X_CSRFTOKEN="this-is-not-the-real-token",
    )

    assert response.status_code == 403


def test_bearer_header_auth_is_exempt_from_csrf(client, salon, admin_account) -> None:
    # No cookies at all — token rides the Authorization header, so this is a
    # non-browser client and CSRF does not apply.
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {_account_access_token(admin_account)}")

    response = client.post(_categories_url(salon.slug), {"name": {"en": "Bearer"}}, format="json")

    assert response.status_code == 201


# --- 6-7. login ---------------------------------------------------------


def test_login_without_csrf_header_is_rejected(client, salon, admin_account) -> None:
    # The client primed the csrftoken cookie via auth/csrf/ first, but sends
    # no header — login-CSRF is in scope.
    _prime_csrf(client)

    response = client.post(
        _login_url(salon.slug),
        {"email": admin_account.email, "password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 403


def test_login_with_correct_csrf_header_succeeds(client, salon, admin_account) -> None:
    csrf = _prime_csrf(client)

    response = client.post(
        _login_url(salon.slug),
        {"email": admin_account.email, "password": STRONG_PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )

    assert response.status_code == 200
    assert response.cookies.get(ACCESS_COOKIE) is not None
    assert response.cookies.get(REFRESH_COOKIE) is not None


# --- 8. refresh + logout ----------------------------------------------


def test_refresh_without_csrf_header_is_rejected(client, salon, admin_account) -> None:
    client.cookies[REFRESH_COOKIE] = _account_refresh_token(admin_account)
    _prime_csrf(client)

    response = client.post(_refresh_url(salon.slug), {}, format="json")

    assert response.status_code == 403


def test_refresh_with_correct_csrf_header_succeeds(client, salon, admin_account) -> None:
    client.cookies[REFRESH_COOKIE] = _account_refresh_token(admin_account)
    csrf = _prime_csrf(client)

    response = client.post(_refresh_url(salon.slug), {}, format="json", HTTP_X_CSRFTOKEN=csrf)

    assert response.status_code == 200


def test_logout_without_csrf_header_is_rejected(client, salon, admin_account) -> None:
    client.cookies[ACCESS_COOKIE] = _account_access_token(admin_account)
    client.cookies[REFRESH_COOKIE] = _account_refresh_token(admin_account)
    _prime_csrf(client)

    response = client.post(_logout_url(salon.slug), {}, format="json")

    assert response.status_code == 403


def test_logout_with_correct_csrf_header_succeeds(client, salon, admin_account) -> None:
    client.cookies[ACCESS_COOKIE] = _account_access_token(admin_account)
    client.cookies[REFRESH_COOKIE] = _account_refresh_token(admin_account)
    csrf = _prime_csrf(client)

    response = client.post(_logout_url(salon.slug), {}, format="json", HTTP_X_CSRFTOKEN=csrf)

    assert response.status_code == 205


# --- 9. guest token exemption ---------------------------------------------


def test_guest_token_write_is_exempt_from_csrf(
    client, salon, customer, specialist, service
) -> None:
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=START
    )
    with tenant_context(salon.id):
        raw_token, _row = issue_guest_token(appointment)

    # X-Guest-Token header, no cookies, no X-CSRFToken.
    response = client.post(_cancel_url(salon.slug, appointment.id), HTTP_X_GUEST_TOKEN=raw_token)

    assert response.status_code == 200
