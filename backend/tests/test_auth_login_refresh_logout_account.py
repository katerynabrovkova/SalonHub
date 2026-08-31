"""
Stage 3-R.E — login / refresh / logout retargeted to Account
(docs/DECISIONS.md § Stage 3-R.E).

RED phase: written against the agreed contract before the salon-scoped
``auth/login/`` / ``auth/refresh/`` / ``auth/logout/`` routes, or any
Account-based login serializer/view, exist. Nothing here imports an
unwritten symbol — only ``accounts.models.Account`` and ``core.tenancy`` —
so collection succeeds; every pre-implementation failure on the new
salon-scoped routes is a clean 404 (the ``salons/<slug>/`` prefix resolves,
the ``auth/login|refresh|logout/`` tails do not yet). The flat-route-removal
assertions fail the opposite way today: those routes still exist and still
serve `User`-based login (docs/DECISIONS.md's 3-R.D entries — "Login /
refresh / logout stay on `User` at `/api/v1/auth/` until 3-R.E").

The contract under test (docs/DECISIONS.md § Stage 3-R.E):

* ``POST auth/login/`` ``{email, password}`` -> 200 ``{access, refresh}``
  against an ``Account`` in the bound salon. Salon comes from the path
  slug, never the body.
* ``POST auth/refresh/`` ``{refresh}`` -> 200 with a new access token.
* ``POST auth/logout/`` ``{refresh}`` -> blacklists the refresh token.
* The flat ``/api/v1/auth/{login,refresh,logout}/`` routes are **removed**,
  not relocated-and-kept — ``User`` no longer authenticates via JWT at all
  after E.
* Login failure (wrong password vs. unknown email) is a byte-identical
  ``401`` — no-enumeration, same posture as D.3 register / D.5
  password-reset-request.
"""

import pytest
from rest_framework.test import APIClient

from accounts.models import Account
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "a-strong-passw0rd!"


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


# --- A. salon-scoped routes exist and behave ------------------------------


def test_login_with_valid_account_credentials_returns_200_with_tokens(client, salon) -> None:
    account = _make_account(salon)

    response = client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 200
    assert "access" in response.data
    assert "refresh" in response.data


def test_refresh_with_valid_refresh_token_returns_a_new_access_token(client, salon) -> None:
    account = _make_account(salon)
    login = client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    ).data

    response = client.post(_refresh_url(salon.slug), {"refresh": login["refresh"]}, format="json")

    assert response.status_code == 200
    assert "access" in response.data


def test_logout_blacklists_the_refresh_token(client, salon) -> None:
    account = _make_account(salon)
    login = client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    ).data

    logout = client.post(
        _logout_url(salon.slug),
        {"refresh": login["refresh"]},
        HTTP_AUTHORIZATION=f"Bearer {login['access']}",
        format="json",
    )
    assert logout.status_code == 205

    reused = client.post(_refresh_url(salon.slug), {"refresh": login["refresh"]}, format="json")
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
