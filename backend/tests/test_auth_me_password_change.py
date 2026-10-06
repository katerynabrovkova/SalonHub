"""
Stage 15 planning, item 9 (docs/DECISIONS.md § "Item 9 decisions (change
password): ending sessions", "Endpoint and page, decided 06.10.2026",
points 1-6): the authenticated Account changes its password.

    POST /api/v1/salons/<slug>/auth/me/password-change/

Fixtures mirror test_auth_me_email_change.py: an Account linked to
conftest.py's `customer`. Validation and throttle cases use
`client.force_authenticate`; the session cases (cookies cleared, old tokens
rejected, cross-tenant, the notice email) use a real cookie login with the
CSRF pair, because force_authenticate would bypass the token checks under
test. Mail lands in `django.core.mail.outbox` (conftest.py's eager Celery +
pytest-django's locmem backend); the notice is queued with
`transaction.on_commit`, so a test sees it only through
`django_capture_on_commit_callbacks`.

RED shape: the route exists but its view is a stub answering 501, so each
behaviour test fails on its first status assertion.
"""

import pytest
from django.core import mail
from django.middleware.csrf import get_token
from django.test import RequestFactory
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole
from core.i18n import resolve_translation
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

PASSWORD = "a-strong-passw0rd!"
NEW_PASSWORD = "an-even-str0nger-passphrase!"
WEAK_PASSWORD = "123"

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
SESSION_HINT_COOKIE = "session_hint"
CSRF_COOKIE = "csrftoken"

SUBJECT_NOTICE = "Пароль змінено"
GREETING = "Вітаємо!"
SIGNED_OUT_TEXT = (
    "Пароль до вашого акаунта в салоні {salon} щойно змінено. "
    "Ви вийшли з акаунта на всіх пристроях."
)
NOT_YOU_TEXT = (
    "Якщо ви не змінювали пароль, відновіть доступ через «Забули пароль?» на сторінці входу."
)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture
def csrf_client() -> APIClient:
    return APIClient(enforce_csrf_checks=True)


def _password_change_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/auth/me/password-change/"


def _login_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/auth/login/"


def _refresh_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/auth/refresh/"


def _me_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/auth/me/"


@pytest.fixture
def customer_account(salon, customer):
    with tenant_context(salon.id):
        return Account.objects.create_account(
            salon=salon,
            email="alice-account@example.com",
            password=PASSWORD,
            role=AccountRole.CLIENT,
            customer=customer,
        )


def _prime_csrf(client: APIClient) -> str:
    """A masked CSRF token used for both the cookie and the header, a valid pair."""
    token = get_token(RequestFactory().get("/"))
    client.cookies[CSRF_COOKIE] = token
    return token


def _login(client: APIClient, salon, account: Account) -> str:
    """Real cookie login with the CSRF pair. Returns the CSRF token for later writes."""
    csrf = _prime_csrf(client)
    response = client.post(
        _login_url(salon),
        {"email": account.email, "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 200
    return csrf


def _post(client, salon, current: str = PASSWORD, new: str = NEW_PASSWORD, csrf: str = ""):
    return client.post(
        _password_change_url(salon),
        {"current_password": current, "new_password": new},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )


def _salon_name(salon) -> str:
    return resolve_translation(salon.name, "uk")


def _reload_account(account) -> Account:
    return Account.unscoped_objects.get(pk=account.pk)


def _assert_unchanged(account: Account) -> None:
    row = _reload_account(account)
    assert row.check_password(PASSWORD)
    assert row.session_version == account.session_version


def _assert_cleared(response, name: str, path: str) -> None:
    cookie = response.cookies.get(name)
    assert cookie is not None, f"{name} must be deleted by the response"
    assert cookie.value == ""
    assert int(cookie["max-age"]) == 0
    assert cookie["path"] == path


# --- 1-4. Success ------------------------------------------------------------


def test_success_is_204_empty_and_swaps_the_password(csrf_client, salon, customer_account):
    csrf = _login(csrf_client, salon, customer_account)

    response = _post(csrf_client, salon, csrf=csrf)

    assert response.status_code == 204
    assert response.content == b""
    row = _reload_account(customer_account)
    assert row.check_password(NEW_PASSWORD)
    assert not row.check_password(PASSWORD)


def test_success_increments_session_version_by_exactly_one(csrf_client, salon, customer_account):
    before = customer_account.session_version
    csrf = _login(csrf_client, salon, customer_account)

    response = _post(csrf_client, salon, csrf=csrf)

    assert response.status_code == 204
    assert _reload_account(customer_account).session_version == before + 1


def test_success_clears_all_three_session_cookies(csrf_client, salon, customer_account):
    csrf = _login(csrf_client, salon, customer_account)

    response = _post(csrf_client, salon, csrf=csrf)

    assert response.status_code == 204
    _assert_cleared(response, ACCESS_COOKIE, "/")
    _assert_cleared(response, REFRESH_COOKIE, _refresh_url(salon))
    _assert_cleared(response, SESSION_HINT_COOKIE, "/")


def test_after_success_the_old_access_and_refresh_cookies_get_401(
    csrf_client, salon, customer_account
):
    csrf = _login(csrf_client, salon, customer_account)
    old_access = csrf_client.cookies[ACCESS_COOKIE].value
    old_refresh = csrf_client.cookies[REFRESH_COOKIE].value

    response = _post(csrf_client, salon, csrf=csrf)
    assert response.status_code == 204

    me_client = APIClient(enforce_csrf_checks=True)
    me_client.cookies[ACCESS_COOKIE] = old_access
    assert me_client.get(_me_url(salon)).status_code == 401

    refresh_client = APIClient(enforce_csrf_checks=True)
    refresh_client.cookies[REFRESH_COOKIE] = old_refresh
    refresh_csrf = _prime_csrf(refresh_client)
    refresh = refresh_client.post(
        _refresh_url(salon), {}, format="json", HTTP_X_CSRFTOKEN=refresh_csrf
    )
    assert refresh.status_code == 401


# --- 5-10. Validation ----------------------------------------------------------


def test_wrong_current_password_is_400_invalid_password_and_changes_nothing(
    client, salon, customer_account, django_capture_on_commit_callbacks
):
    client.force_authenticate(user=customer_account)

    with django_capture_on_commit_callbacks(execute=True):
        response = _post(client, salon, current="wrong-password")

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_password"
    _assert_unchanged(customer_account)
    assert mail.outbox == []


def test_new_password_equal_to_the_current_one_is_400_same_password(
    client, salon, customer_account, django_capture_on_commit_callbacks
):
    client.force_authenticate(user=customer_account)

    with django_capture_on_commit_callbacks(execute=True):
        response = _post(client, salon, new=PASSWORD)

    assert response.status_code == 400
    assert response.data["error"]["code"] == "same_password"
    _assert_unchanged(customer_account)
    assert mail.outbox == []


def test_weak_new_password_is_a_400_field_error_on_new_password(
    client, salon, customer_account, django_capture_on_commit_callbacks
):
    client.force_authenticate(user=customer_account)

    with django_capture_on_commit_callbacks(execute=True):
        response = _post(client, salon, new=WEAK_PASSWORD)

    assert response.status_code == 400
    assert "new_password" in response.data["error"]["details"]
    _assert_unchanged(customer_account)
    assert mail.outbox == []


def test_new_password_similar_to_the_account_email_is_a_400_on_new_password(
    client, salon, customer_account
):
    # Long enough, not common, not numeric: only UserAttributeSimilarityValidator
    # rejects it, and only when validate_password() gets user=account.
    client.force_authenticate(user=customer_account)

    response = _post(client, salon, new=f"{customer_account.email}1")

    assert response.status_code == 400
    assert "new_password" in response.data["error"]["details"]
    _assert_unchanged(customer_account)


def test_wrong_current_password_wins_over_a_weak_new_password(client, salon, customer_account):
    client.force_authenticate(user=customer_account)

    response = _post(client, salon, current="wrong-password", new=WEAK_PASSWORD)

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_password"
    _assert_unchanged(customer_account)


@pytest.mark.parametrize(
    "body, missing",
    [
        ({}, ["current_password", "new_password"]),
        ({"new_password": NEW_PASSWORD}, ["current_password"]),
        ({"current_password": PASSWORD}, ["new_password"]),
    ],
)
def test_missing_fields_are_a_400(client, salon, customer_account, body, missing):
    client.force_authenticate(user=customer_account)

    response = client.post(_password_change_url(salon), body, format="json")

    assert response.status_code == 400
    for field in missing:
        assert field in response.data["error"]["details"]
    _assert_unchanged(customer_account)


# --- 11-12. Authentication and tenant isolation --------------------------------


def test_unauthenticated_request_is_401(client, salon, customer_account):
    response = _post(client, salon)

    assert response.status_code == 401
    _assert_unchanged(customer_account)


def test_salon_a_cookies_on_salon_b_url_get_401_and_change_nothing(
    csrf_client, salon, other_salon, customer_account
):
    with tenant_context(other_salon.id):
        other_account = Account.objects.create_account(
            salon=other_salon,
            email="bob@example.com",
            password=PASSWORD,
            role=AccountRole.CLIENT,
        )
    csrf = _login(csrf_client, salon, customer_account)

    response = _post(csrf_client, other_salon, csrf=csrf)

    assert response.status_code == 401
    _assert_unchanged(customer_account)
    _assert_unchanged(other_account)


# --- 13. Throttle --------------------------------------------------------------


def test_sixth_request_in_an_hour_is_429_and_wrong_passwords_count(client, salon, customer_account):
    client.force_authenticate(user=customer_account)

    statuses = [_post(client, salon, current="wrong-password").status_code for _ in range(4)]
    statuses.append(_post(client, salon).status_code)
    sixth = _post(client, salon, current=NEW_PASSWORD, new=f"{NEW_PASSWORD}x")

    assert statuses == [400, 400, 400, 400, 204]
    assert sixth.status_code == 429


# --- 14. Notice email ----------------------------------------------------------


def test_success_queues_one_notice_on_commit_to_the_current_address(
    csrf_client, salon, customer_account, django_capture_on_commit_callbacks
):
    csrf = _login(csrf_client, salon, customer_account)

    with django_capture_on_commit_callbacks() as callbacks:
        response = _post(csrf_client, salon, csrf=csrf)

    assert response.status_code == 204
    # Queued, not sent: nothing goes out until the transaction commits.
    assert mail.outbox == []
    assert len(callbacks) == 1

    callbacks[0]()

    assert len(mail.outbox) == 1
    letter = mail.outbox[0]
    assert letter.to == [customer_account.email]
    assert letter.subject == SUBJECT_NOTICE
    salon_name = _salon_name(salon)
    body = letter.body
    signed_out = SIGNED_OUT_TEXT.format(salon=salon_name)
    assert GREETING in body
    assert signed_out in body
    assert NOT_YOU_TEXT in body
    assert body.index(GREETING) < body.index(signed_out) < body.index(NOT_YOU_TEXT)
    assert body.rstrip().endswith(salon_name)
