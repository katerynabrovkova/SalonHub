"""
Stage 15 session cycle S1 (backend) — RED phase.
See docs/DECISIONS.md § "Session renewal and session lifetime, decided
21.09.2026", decisions 1, 2, 3 and the S1 line of decision 9.

* Login / refresh set ``access_token`` and ``refresh_token`` with ``max-age`` equal
  to the SIMPLE_JWT lifetimes, plus a non-httpOnly ``session_hint`` cookie
  (value ``"1"``, ``Path=/``, refresh-lifetime ``max-age``, SameSite/Secure equal
  to the access cookie's).
* Logout no longer authenticates: it always answers 205, clears all three cookies
  (deletion = ``max-age=0`` on the same Path) and blacklists the refresh token
  when it is present and valid. It stays CSRF protected.

The client enforces CSRF (same as tests/test_csrf_protection.py); the
``csrftoken`` cookie + ``X-CSRFToken`` header pair is supplied explicitly.
Lifetimes are read from ``settings.SIMPLE_JWT``, never hard-coded.
"""

import datetime as dt

import pytest
from django.conf import settings
from django.middleware.csrf import get_token
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from accounts.models import Account
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "a-strong-passw0rd!"

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
SESSION_HINT_COOKIE = "session_hint"
CSRF_COOKIE = "csrftoken"

ALL_SESSION_COOKIES = (ACCESS_COOKIE, REFRESH_COOKIE, SESSION_HINT_COOKIE)


@pytest.fixture
def client() -> APIClient:
    return APIClient(enforce_csrf_checks=True)


def _login_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/login/"


def _refresh_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/refresh/"


def _logout_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/logout/"


def _refresh_path(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/refresh/"


def _access_seconds() -> int:
    return int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds())


def _refresh_seconds() -> int:
    return int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())


def _prime_csrf(client: APIClient) -> str:
    """A masked CSRF token used for both the cookie and the header — a valid pair."""
    token = get_token(RequestFactory().get("/"))
    client.cookies[CSRF_COOKIE] = token
    return token


def _account_access_token(account: Account) -> str:
    token = AccessToken()
    token["user_id"] = str(account.pk)
    token["identity_model"] = "account"
    return str(token)


def _expired_access_token(account: Account) -> str:
    token = AccessToken()
    token["user_id"] = str(account.pk)
    token["identity_model"] = "account"
    token.set_exp(
        from_time=timezone.now() - dt.timedelta(days=1), lifetime=dt.timedelta(minutes=15)
    )
    return str(token)


def _account_refresh_token(account: Account, *, remaining: dt.timedelta | None = None) -> str:
    token = RefreshToken()
    token["user_id"] = str(account.pk)
    token["identity_model"] = "account"
    if remaining is not None:
        token.set_exp(from_time=timezone.now(), lifetime=remaining)
    return str(token)


def _login(client: APIClient, salon, email: str = "session@example.com"):
    """Real login through the endpoint, with the CSRF pair. Returns (response, csrf)."""
    with tenant_context(salon.id):
        Account.objects.create_account(salon=salon, email=email, password=STRONG_PASSWORD)
    csrf = _prime_csrf(client)
    response = client.post(
        _login_url(salon.slug),
        {"email": email, "password": STRONG_PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 200
    return response, csrf


def _assert_cleared(response, name: str, path: str) -> None:
    cookie = response.cookies.get(name)
    assert cookie is not None, f"{name} must be deleted by the response"
    assert cookie.value == ""
    assert int(cookie["max-age"]) == 0
    assert cookie["path"] == path


def _assert_all_three_cleared(response, salon) -> None:
    _assert_cleared(response, ACCESS_COOKIE, "/")
    _assert_cleared(response, REFRESH_COOKIE, _refresh_path(salon.slug))
    _assert_cleared(response, SESSION_HINT_COOKIE, "/")


def _assert_all_three_set_fresh(response, salon) -> None:
    access = response.cookies[ACCESS_COOKIE]
    refresh = response.cookies[REFRESH_COOKIE]
    hint = response.cookies[SESSION_HINT_COOKIE]
    assert int(access["max-age"]) == _access_seconds()
    assert int(refresh["max-age"]) == _refresh_seconds()
    assert int(hint["max-age"]) == _refresh_seconds()
    assert access.value and refresh.value and hint.value == "1"
    assert refresh["path"] == _refresh_path(salon.slug)


# --- 1-2. login ----------------------------------------------------------


def test_login_sets_access_and_refresh_cookies_with_max_age(client, salon) -> None:
    response, _csrf = _login(client, salon)

    access = response.cookies[ACCESS_COOKIE]
    refresh = response.cookies[REFRESH_COOKIE]
    assert int(access["max-age"]) == _access_seconds()
    assert int(refresh["max-age"]) == _refresh_seconds()
    assert access["httponly"]
    assert refresh["httponly"]


def test_login_sets_the_session_hint_cookie(client, salon) -> None:
    response, _csrf = _login(client, salon)

    hint = response.cookies.get(SESSION_HINT_COOKIE)
    access = response.cookies[ACCESS_COOKIE]
    assert hint is not None, "login must set the session_hint cookie"
    assert hint.value == "1"
    assert not hint["httponly"]
    assert hint["path"] == "/"
    assert int(hint["max-age"]) == _refresh_seconds()
    assert hint["samesite"] == access["samesite"]
    assert hint["secure"] == access["secure"]


# --- 3-5. refresh --------------------------------------------------------


def test_refresh_resets_all_three_cookies_with_fresh_max_age(client, salon) -> None:
    _login(client, salon)
    csrf = client.cookies[CSRF_COOKIE].value

    response = client.post(_refresh_url(salon.slug), {}, format="json", HTTP_X_CSRFTOKEN=csrf)

    assert response.status_code == 200
    _assert_all_three_set_fresh(response, salon)


def test_refresh_new_refresh_token_expiry_is_fresh_not_inherited(
    client, salon, admin_account
) -> None:
    # Guard: the rotating serializer probably already does this, so this test
    # may pass in the red phase.
    old = _account_refresh_token(admin_account, remaining=dt.timedelta(days=1))
    client.cookies[REFRESH_COOKIE] = old
    csrf = _prime_csrf(client)

    before = timezone.now()
    response = client.post(_refresh_url(salon.slug), {}, format="json", HTTP_X_CSRFTOKEN=csrf)
    after = timezone.now()

    assert response.status_code == 200
    new = response.cookies[REFRESH_COOKIE].value
    assert new != old
    exp = dt.datetime.fromtimestamp(RefreshToken(new)["exp"], tz=dt.UTC)
    lifetime = settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]
    tolerance = dt.timedelta(seconds=30)
    assert before + lifetime - tolerance <= exp <= after + lifetime + tolerance


def test_refresh_works_with_no_access_cookie(client, salon, admin_account) -> None:
    # Guard: refresh is AllowAny and cookie-driven, so this may already pass.
    client.cookies[REFRESH_COOKIE] = _account_refresh_token(admin_account)
    csrf = _prime_csrf(client)
    assert ACCESS_COOKIE not in client.cookies

    response = client.post(_refresh_url(salon.slug), {}, format="json", HTTP_X_CSRFTOKEN=csrf)

    assert response.status_code == 200
    _assert_all_three_set_fresh(response, salon)


# --- 6-10. logout --------------------------------------------------------


def test_logout_clears_all_three_cookies_and_blacklists_the_refresh_token(client, salon) -> None:
    _login(client, salon)
    csrf = client.cookies[CSRF_COOKIE].value
    refresh_value = client.cookies[REFRESH_COOKIE].value

    logout = client.post(_logout_url(salon.slug), {}, format="json", HTTP_X_CSRFTOKEN=csrf)

    assert logout.status_code == 205
    _assert_all_three_cleared(logout, salon)

    client.cookies[REFRESH_COOKIE] = refresh_value
    reused = client.post(_refresh_url(salon.slug), {}, format="json", HTTP_X_CSRFTOKEN=csrf)
    assert reused.status_code == 401


def test_logout_with_expired_access_cookie_still_logs_out(client, salon, admin_account) -> None:
    refresh_value = _account_refresh_token(admin_account)
    client.cookies[ACCESS_COOKIE] = _expired_access_token(admin_account)
    client.cookies[REFRESH_COOKIE] = refresh_value
    csrf = _prime_csrf(client)

    logout = client.post(_logout_url(salon.slug), {}, format="json", HTTP_X_CSRFTOKEN=csrf)

    assert logout.status_code == 205
    _assert_all_three_cleared(logout, salon)

    client.cookies[REFRESH_COOKIE] = refresh_value
    reused = client.post(_refresh_url(salon.slug), {}, format="json", HTTP_X_CSRFTOKEN=csrf)
    assert reused.status_code == 401


def test_logout_with_no_session_cookies_is_idempotent(client, salon) -> None:
    csrf = _prime_csrf(client)

    logout = client.post(_logout_url(salon.slug), {}, format="json", HTTP_X_CSRFTOKEN=csrf)

    assert logout.status_code == 205
    _assert_all_three_cleared(logout, salon)


def test_logout_with_invalid_refresh_token_still_clears_cookies(
    client, salon, admin_account
) -> None:
    # (a) garbage refresh token
    client.cookies[REFRESH_COOKIE] = "this-is-not-a-jwt"
    csrf = _prime_csrf(client)
    garbage = client.post(_logout_url(salon.slug), {}, format="json", HTTP_X_CSRFTOKEN=csrf)
    assert garbage.status_code == 205
    _assert_all_three_cleared(garbage, salon)

    # (b) a refresh token that is already blacklisted
    blacklisted = _account_refresh_token(admin_account)
    RefreshToken(blacklisted).blacklist()
    second = APIClient(enforce_csrf_checks=True)
    second.cookies[REFRESH_COOKIE] = blacklisted
    csrf = _prime_csrf(second)
    response = second.post(_logout_url(salon.slug), {}, format="json", HTTP_X_CSRFTOKEN=csrf)
    assert response.status_code == 205
    _assert_all_three_cleared(response, salon)


def test_logout_without_csrf_header_is_403_even_with_an_expired_access_cookie(
    client, salon, admin_account
) -> None:
    client.cookies[ACCESS_COOKIE] = _expired_access_token(admin_account)
    client.cookies[REFRESH_COOKIE] = _account_refresh_token(admin_account)
    _prime_csrf(client)  # valid csrftoken cookie, but no X-CSRFToken header

    response = client.post(_logout_url(salon.slug), {}, format="json")

    assert response.status_code == 403
    for name in ALL_SESSION_COOKIES:
        assert name not in response.cookies, f"{name} must not be touched on a CSRF failure"


def test_all_three_session_cookies_are_secure_when_the_secure_setting_is_on(
    client, salon, settings
) -> None:
    # Secure is off in the test environment, so test 2's equality check cannot
    # catch a hint cookie that ignores SESSION_COOKIE_SECURE.
    settings.SESSION_COOKIE_SECURE = True

    response, _csrf = _login(client, salon)

    for name in ALL_SESSION_COOKIES:
        assert response.cookies[name]["secure"], f"{name} must carry the Secure flag"
