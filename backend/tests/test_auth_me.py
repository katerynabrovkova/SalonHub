"""
Stage 12 — ``/me/`` endpoint (docs/DECISIONS.md § Stage 12, "`/me/` endpoint
(Stage 12)").

RED phase: written before ``MeView`` (or whatever the view is named), its
serializer, and its URL entry exist. There is no unwritten symbol imported
here — the test drives the endpoint purely through ``APIClient`` HTTP calls,
the same way ``tests/test_auth_login_refresh_logout_account.py`` does — so
collection succeeds and every failure below is a routing/assertion failure
against today's actual (nonexistent) surface, not an import error.

The contract under test (docs/DECISIONS.md § "`/me/` endpoint (Stage 12)"):

* ``GET /api/v1/salons/<slug>/auth/me/``, authenticated (Account, cookie-based
  JWT via ``AccountJWTCookieAuthentication`` — the project-wide
  ``DEFAULT_AUTHENTICATION_CLASSES``/``DEFAULT_PERMISSION_CLASSES`` default,
  same posture as ``LogoutView``: no explicit
  ``authentication_classes``/``permission_classes`` override expected).
* Success body is exactly ``{"email": ..., "role": ...}`` — no ``salon`` key
  (the frontend already has the slug from the subdomain before login
  happens), no ``id``, no ``email_verified_at`` (deferred — verification
  gates the guest->account Customer merge, not login/session state).
* No credential at all -> 401, same as any other endpoint relying on the
  global ``IsAuthenticated`` default.
* A valid access cookie minted under a *different* salon's tenant context is
  rejected. The mechanism is the same tenant-scoped lookup
  ``AccountJWTAuthentication.get_user()`` already performs for every
  Account-authenticated endpoint (accounts/authentication.py): under the
  wrong salon's bound tenant context, ``Account.objects.get(pk=user_id)``
  simply cannot see a row belonging to another salon and raises
  ``Account.DoesNotExist`` -> ``AuthenticationFailed`` -> 401. This is not a
  new guard ``/me/`` needs to add — it falls out of the existing
  authentication class for free, the same as every cross-tenant case in
  tests/test_auth_identity_model_claim.py resolves to 401, never 404 (the
  URL itself resolves fine under either slug; it's the credential that's
  rejected).
"""

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "a-strong-passw0rd!"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _login_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/login/"


def _me_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/me/"


def _make_account(
    salon, email: str = "me-endpoint@example.com", role: str = AccountRole.CLIENT
) -> Account:
    with tenant_context(salon.id):
        return Account.objects.create_account(
            salon=salon, email=email, password=STRONG_PASSWORD, role=role
        )


def test_me_authenticated_returns_email_and_role(client, salon) -> None:
    account = _make_account(salon, email="whoami@example.com", role=AccountRole.ADMIN)
    client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    )

    response = client.get(_me_url(salon.slug))

    assert response.status_code == 200
    # _make_account links no Customer, so name/phone are null (docs/DECISIONS.md
    # § Stage 15 planning, item 7) -- see test_me_with_no_linked_customer_...
    # below for the same case asserted as its own dedicated test.
    assert response.data == {
        "email": account.email,
        "role": account.role,
        "name": None,
        "phone": None,
        "email_verified": False,
    }


def test_me_with_a_linked_customer_returns_its_name_and_phone(client, salon) -> None:
    with tenant_context(salon.id):
        customer = Customer.objects.create(
            salon=salon, name="Alice", email="alice@example.com", phone="+10000000000"
        )
        account = Account.objects.create_account(
            salon=salon,
            email="linked@example.com",
            password=STRONG_PASSWORD,
            role=AccountRole.CLIENT,
            customer=customer,
        )
    client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    )

    response = client.get(_me_url(salon.slug))

    assert response.status_code == 200
    assert response.data == {
        "email": account.email,
        "role": account.role,
        "name": "Alice",
        "phone": "+10000000000",
        "email_verified": False,
    }


def test_me_with_no_linked_customer_returns_null_name_and_phone(client, salon) -> None:
    """An Account with no linked Customer yet is a real, not-rare state
    (item 5's recon: an Account can be logged in before its guest-Customer
    merge ever happens) -- /me/ must keep returning 200 with null name/phone,
    not raise or 404."""
    account = _make_account(salon, email="unlinked@example.com")
    client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    )

    response = client.get(_me_url(salon.slug))

    assert response.status_code == 200
    assert response.data["name"] is None
    assert response.data["phone"] is None


def test_me_unauthenticated_returns_401(client, salon) -> None:
    response = client.get(_me_url(salon.slug))

    assert response.status_code == 401


def test_me_returns_email_verified_false_when_email_verified_at_is_none(client, salon) -> None:
    """docs/DECISIONS.md § Stage 15 planning, item 5, "Extended 19.09.2026":
    `MeSerializer` gains a read-only `email_verified` boolean derived from
    `Account.email_verified_at is not None`."""
    account = _make_account(salon, email="unverified@example.com")
    assert account.email_verified_at is None
    client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    )

    response = client.get(_me_url(salon.slug))

    assert response.status_code == 200
    assert response.data["email_verified"] is False


def test_me_returns_email_verified_true_when_email_verified_at_is_set(client, salon) -> None:
    """Same decision as above — the true branch."""
    account = _make_account(salon, email="verified@example.com")
    with tenant_context(salon.id):
        account.email_verified_at = timezone.now()
        account.save(update_fields=["email_verified_at"])
    client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    )

    response = client.get(_me_url(salon.slug))

    assert response.status_code == 200
    assert response.data["email_verified"] is True


def test_me_does_not_expose_email_verified_at(client, salon) -> None:
    """The timestamp itself must never be exposed, only the derived boolean
    (docs/DECISIONS.md § Stage 15 planning, item 5, "Extended 19.09.2026")."""
    account = _make_account(salon, email="no-timestamp-leak@example.com")
    client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    )

    response = client.get(_me_url(salon.slug))

    assert response.status_code == 200
    assert "email_verified_at" not in response.data


def test_me_wrong_salon_cookie_returns_401_or_404(client, salon, other_salon) -> None:
    account = _make_account(salon, email="salon-a-account@example.com")
    client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": STRONG_PASSWORD},
        format="json",
    )

    # Same cookies, but the URL now binds `other_salon`'s tenant context —
    # the access token names a pk that `other_salon`'s tenant-scoped
    # Account.objects manager cannot see (see module docstring).
    response = client.get(_me_url(other_salon.slug))

    assert response.status_code == 401
