"""
Item 9 cycle S1 (backend), RED phase.
See docs/DECISIONS.md § "Item 9 decisions (change password): ending
sessions", decisions 1, 2 and 5.

* Every access and refresh token carries a ``session_version`` claim equal to
  ``Account.session_version``.
* Cookie authentication and refresh reject a token whose claim is missing or
  differs from the account's value, with a 401.
* A successful password reset confirm increments ``session_version``, so the
  access cookie from before the reset stops working on the next request.

Sessions come from a real cookie login, never ``force_authenticate``. A "bump"
is an ``F()`` increment of ``session_version`` done directly in the test.
"""

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.db.models import F
from django.middleware.csrf import get_token
from django.test import RequestFactory
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from accounts.models import Account
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "a-strong-passw0rd!"
NEW_PASSWORD = "an-even-str0nger-passphrase!"

CLAIM = "session_version"
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrftoken"


@pytest.fixture
def client() -> APIClient:
    return APIClient(enforce_csrf_checks=True)


def _login_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/login/"


def _refresh_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/refresh/"


def _me_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/me/"


def _reset_confirm_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/password-reset/confirm/"


def _prime_csrf(client: APIClient) -> str:
    """A masked CSRF token used for both the cookie and the header, a valid pair."""
    token = get_token(RequestFactory().get("/"))
    client.cookies[CSRF_COOKIE] = token
    return token


def _make_account(salon, email: str) -> Account:
    with tenant_context(salon.id):
        return Account.objects.create_account(salon=salon, email=email, password=STRONG_PASSWORD)


def _login(client: APIClient, salon, account: Account, password: str = STRONG_PASSWORD):
    """Real login through the endpoint, with the CSRF pair. Returns the response."""
    csrf = _prime_csrf(client)
    response = client.post(
        _login_url(salon.slug),
        {"email": account.email, "password": password},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 200
    return response


def _refresh(client: APIClient, salon):
    csrf = (
        client.cookies[CSRF_COOKIE].value if CSRF_COOKIE in client.cookies else _prime_csrf(client)
    )
    return client.post(_refresh_url(salon.slug), {}, format="json", HTTP_X_CSRFTOKEN=csrf)


def _me_with_access(salon, access: str):
    """GET auth/me/ from a fresh client carrying only the given access cookie."""
    other = APIClient(enforce_csrf_checks=True)
    other.cookies[ACCESS_COOKIE] = access
    return other.get(_me_url(salon.slug))


def _bump(account: Account) -> None:
    Account.unscoped_objects.filter(pk=account.pk).update(session_version=F("session_version") + 1)


def _session_version(account: Account) -> int:
    return Account.unscoped_objects.get(pk=account.pk).session_version


def _sets_new_cookie(response, name: str) -> bool:
    """True if the response sets ``name`` to a non-empty value (a deletion does not count)."""
    return name in response.cookies and response.cookies[name].value != ""


# --- 1-3. login and refresh carry the claim ------------------------------


def test_login_access_cookie_gets_200_on_me(client, salon) -> None:
    account = _make_account(salon, "a@example.com")
    response = _login(client, salon, account)

    me = _me_with_access(salon, response.cookies[ACCESS_COOKIE].value)

    assert me.status_code == 200


def test_refresh_after_login_succeeds_and_new_access_cookie_gets_200_on_me(client, salon) -> None:
    account = _make_account(salon, "a@example.com")
    login = _login(client, salon, account)
    old_access = login.cookies[ACCESS_COOKIE].value

    response = _refresh(client, salon)

    assert response.status_code == 200
    new_access = response.cookies[ACCESS_COOKIE].value
    assert new_access != old_access
    assert _me_with_access(salon, new_access).status_code == 200


def test_login_access_and_refresh_tokens_carry_the_session_version_claim(client, salon) -> None:
    account = _make_account(salon, "a@example.com")
    response = _login(client, salon, account)

    access = AccessToken(response.cookies[ACCESS_COOKIE].value)
    refresh = RefreshToken(response.cookies[REFRESH_COOKIE].value)
    expected = _session_version(account)

    assert access.get(CLAIM) == expected
    assert refresh.get(CLAIM) == expected


# --- 4-8. a changed or missing claim is rejected -------------------------


def test_after_a_bump_the_old_access_cookie_gets_401_on_me(client, salon) -> None:
    account = _make_account(salon, "a@example.com")
    response = _login(client, salon, account)
    old_access = response.cookies[ACCESS_COOKIE].value

    _bump(account)

    assert _me_with_access(salon, old_access).status_code == 401


def test_after_a_bump_refresh_with_the_old_refresh_cookie_gets_401_and_sets_no_cookies(
    client, salon
) -> None:
    account = _make_account(salon, "a@example.com")
    _login(client, salon, account)

    _bump(account)
    response = _refresh(client, salon)

    assert response.status_code == 401
    assert not _sets_new_cookie(response, ACCESS_COOKIE)
    assert not _sets_new_cookie(response, REFRESH_COOKIE)


def test_hand_built_access_token_without_the_claim_gets_401_on_me(salon) -> None:
    account = _make_account(salon, "a@example.com")
    token = AccessToken()
    token["user_id"] = str(account.pk)
    token["identity_model"] = "account"

    assert _me_with_access(salon, str(token)).status_code == 401


def test_hand_built_refresh_token_without_the_claim_gets_401_on_refresh(client, salon) -> None:
    account = _make_account(salon, "a@example.com")
    token = RefreshToken()
    token["user_id"] = str(account.pk)
    token["identity_model"] = "account"
    client.cookies[REFRESH_COOKIE] = str(token)

    response = _refresh(client, salon)

    assert response.status_code == 401


def test_after_a_bump_a_new_login_works_and_its_access_cookie_gets_200(client, salon) -> None:
    account = _make_account(salon, "a@example.com")
    _login(client, salon, account)

    _bump(account)
    response = _login(client, salon, account)

    assert _me_with_access(salon, response.cookies[ACCESS_COOKIE].value).status_code == 200


# --- 9-11. password reset confirm ----------------------------------------


def _reset_confirm(salon, account: Account, *, token: str | None = None, password: str):
    if token is None:
        # The reset token hashes last_login, which login just changed, so it
        # is built from the current row, not the in-memory account.
        token = default_token_generator.make_token(Account.unscoped_objects.get(pk=account.pk))
    return APIClient().post(
        _reset_confirm_url(salon.slug),
        {
            "uid": urlsafe_base64_encode(force_bytes(account.pk)),
            "token": token,
            "new_password": password,
        },
        format="json",
    )


def test_reset_confirm_increments_session_version_and_ends_the_old_access_cookie(
    client, salon
) -> None:
    account = _make_account(salon, "a@example.com")
    login = _login(client, salon, account)
    old_access = login.cookies[ACCESS_COOKIE].value
    before = _session_version(account)

    response = _reset_confirm(salon, account, password=NEW_PASSWORD)

    assert response.status_code == 204
    assert _session_version(account) == before + 1
    assert _me_with_access(salon, old_access).status_code == 401


def test_reset_confirm_with_an_invalid_token_does_not_change_session_version(salon) -> None:
    account = _make_account(salon, "a@example.com")
    before = _session_version(account)

    response = _reset_confirm(salon, account, token="not-a-valid-token", password=NEW_PASSWORD)

    assert response.status_code == 400
    assert _session_version(account) == before


def test_reset_confirm_with_a_weak_password_does_not_change_session_version(salon) -> None:
    account = _make_account(salon, "a@example.com")
    before = _session_version(account)

    response = _reset_confirm(salon, account, password="123")

    assert response.status_code == 400
    assert _session_version(account) == before


# --- 12. isolation between accounts --------------------------------------


def test_bumping_one_account_does_not_affect_another_in_the_same_salon(salon) -> None:
    account_a = _make_account(salon, "a@example.com")
    account_b = _make_account(salon, "b@example.com")
    _login(APIClient(enforce_csrf_checks=True), salon, account_a)
    login_b = _login(APIClient(enforce_csrf_checks=True), salon, account_b)

    _bump(account_a)

    assert _me_with_access(salon, login_b.cookies[ACCESS_COOKIE].value).status_code == 200
