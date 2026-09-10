"""
Stage 3-R.E — login / refresh / logout retargeted to Account
(docs/DECISIONS.md § Stage 3-R.E).

Stage 12 — cookie transport (docs/DECISIONS.md § Stage 12 "Cookie mechanism
resolved"). RED phase: the tests in section C below are written against the
agreed cookie contract before ``LoginView`` / ``RefreshView`` / ``LogoutView``
set any cookie and before a cookie-reading DRF authentication class exists.
Nothing here imports an unwritten symbol, so collection succeeds; every
pre-implementation failure is an assertion failure showing today's actual
JSON-body-token behavior, which is exactly the gap Stage 12 closes.

The contract under test (docs/DECISIONS.md § Stage 3-R.E and § Stage 12):

* ``POST auth/login/`` ``{email, password}`` against an ``Account`` in the
  bound salon (salon from the path slug, never the body) -> 200 that sets two
  httpOnly cookies, an access-token cookie and a refresh-token cookie, and
  returns NO ``access`` / ``refresh`` keys in the JSON body.
* Both cookies are ``HttpOnly`` and ``SameSite=Lax``. The access cookie has no
  ``Path`` restriction (``Path=/``); the refresh cookie is scoped to
  ``Path=/api/v1/salons/<slug>/auth/refresh/`` so the browser only sends it to
  the refresh endpoint.
* ``POST auth/refresh/`` reads the refresh token from its cookie, not the
  request body, and returns a fresh pair of cookies.
* Any authenticated endpoint accepts the access cookie alone, with no
  ``Authorization`` header.
* ``POST auth/logout/`` blacklists the refresh token and clears both cookies.
* The flat ``/api/v1/auth/{login,refresh,logout}/`` routes are **removed**.
* Login failure (wrong password vs. unknown email) is a byte-identical
  ``401`` — no-enumeration.

NOTE — assumed cookie names. docs/DECISIONS.md § Stage 12 fixes that there are
two cookies (one access, one refresh) but does not spell out the literal cookie
names; ``ACCESS_COOKIE`` / ``REFRESH_COOKIE`` below are this test's assumption
and must be reconciled with the names the implementation sub-step picks.

NOTE — refresh cookie Path. Per docs/DECISIONS.md § Stage 12 the refresh cookie
is scoped to the ``auth/refresh/`` endpoint. (The task prompt said
``auth/login/``; treated as a typo for ``auth/refresh/``, which is the only
scoping that matches the doc's rationale — "the browser only attaches it to the
refresh endpoint itself".)
"""

import pytest
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Account
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "a-strong-passw0rd!"

# Assumed — see module docstring.
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"

factory = APIRequestFactory()


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _login_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/login/"


def _refresh_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/refresh/"


def _logout_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/logout/"


def _make_account(salon, email: str = "login-me@example.com") -> Account:
    with tenant_context(salon.id):
        return Account.objects.create_account(salon=salon, email=email, password=STRONG_PASSWORD)


class _AuthenticatedProbeView(APIView):
    """No permission_classes override: relies on the project-wide
    DEFAULT_PERMISSION_CLASSES=[IsAuthenticated] default, same pattern as
    tests/test_auth_identity_model_claim.py. Called via .as_view() directly so
    the real DRF authentication pipeline runs against whatever the request
    carries — here, only a cookie."""

    def get(self, request) -> Response:
        return Response({"ok": True})


# --- A. salon-scoped routes exist and behave ------------------------------


def test_login_with_valid_account_credentials_sets_httponly_cookies(client, salon) -> None:
    account = _make_account(salon)

    response = client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 200

    access = response.cookies.get(ACCESS_COOKIE)
    refresh = response.cookies.get(REFRESH_COOKIE)
    assert access is not None, "login must set an access-token cookie"
    assert refresh is not None, "login must set a refresh-token cookie"

    assert access["httponly"]
    assert refresh["httponly"]
    assert access["samesite"] == "Lax"
    assert refresh["samesite"] == "Lax"

    # Access cookie unrestricted; refresh cookie scoped to the refresh endpoint.
    assert access["path"] == "/"
    assert refresh["path"] == f"/api/v1/salons/{salon.slug}/auth/refresh/"


def test_login_response_body_no_longer_contains_tokens(client, salon) -> None:
    account = _make_account(salon)

    response = client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 200
    assert "access" not in response.data
    assert "refresh" not in response.data


def test_refresh_reads_the_refresh_token_from_the_cookie_not_the_body(client, salon) -> None:
    account = _make_account(salon)
    client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    )

    # No body at all — the refresh token must come from the cookie the test
    # client is now holding.
    response = client.post(_refresh_url(salon.slug), {}, format="json")

    assert response.status_code == 200
    assert response.cookies.get(ACCESS_COOKIE) is not None
    assert "access" not in response.data


def test_authenticated_endpoint_accepts_the_access_cookie_with_no_auth_header(salon) -> None:
    account = _make_account(salon)
    # Built the way AccountTokenObtainPairSerializer.get_token() does — not
    # RefreshToken.for_user(), which eagerly writes an OutstandingToken row
    # keyed to a User FK and rejects an Account instance.
    token = AccessToken()
    token["user_id"] = str(account.pk)
    token["identity_model"] = "account"

    request = factory.get("/")
    request.COOKIES[ACCESS_COOKIE] = str(token)
    # A routed request reaches this view under /api/v1/salons/<slug>/... with
    # TenantResolutionMiddleware having bound the salon; bind it explicitly
    # here since the probe bypasses routing (same reason _make_account does).
    with tenant_context(salon.id):
        response = _AuthenticatedProbeView.as_view()(request)

    assert response.status_code == 200


def test_logout_blacklists_the_refresh_token_and_clears_both_cookies(client, salon) -> None:
    account = _make_account(salon)
    client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    )
    refresh_value = client.cookies[REFRESH_COOKIE].value

    logout = client.post(_logout_url(salon.slug), {}, format="json")
    assert logout.status_code == 205

    # Both cookies cleared (empty value / expired).
    assert logout.cookies[ACCESS_COOKIE].value == ""
    assert logout.cookies[REFRESH_COOKIE].value == ""

    # The refresh token itself is dead: presenting it again is rejected.
    client.cookies[REFRESH_COOKIE] = refresh_value
    reused = client.post(_refresh_url(salon.slug), {}, format="json")
    assert reused.status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/login/",
        "/api/v1/auth/refresh/",
        "/api/v1/auth/logout/",
    ],
)
def test_flat_auth_route_no_longer_resolves(client, path: str) -> None:
    assert client.post(path, {}, format="json").status_code == 404


# --- B. no-enumeration on login failure -----------------------------------


def test_wrong_password_and_unknown_email_return_byte_identical_401(client, salon) -> None:
    account = _make_account(salon)

    wrong_password = client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": "not-the-password!"},
        format="json",
    )
    unknown_email = client.post(
        _login_url(salon.slug),
        {"email": "nobody-here@example.com", "password": "irrelevant-anyway!"},
        format="json",
    )

    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_password.data == unknown_email.data
    assert wrong_password.content == unknown_email.content
