"""
Public (AllowAny) auth endpoints must ignore an invalid ``access_token`` cookie.

RED phase. Today these views inherit the project-default authentication
classes, so ``AccountJWTCookieAuthentication`` validates the cookie during
``initial()`` — before permissions are consulted — and a stale or garbage
cookie turns a public request into ``401 token_not_valid``. The planned fix
(green phase) opts exactly these seven views out of authentication.

Tests 1-7: for each public view, a request carrying a garbage cookie must get
exactly the status and error code the same request gets with no cookie, and
never ``token_not_valid``. Test 8 guards against over-fixing: ``MeView`` must keep
rejecting an invalid cookie with 401. Test 9 pins the new logout contract
(docs/DECISIONS.md, Session renewal and session lifetime, decided 21.09.2026):
``LogoutView`` no longer authenticates, so an invalid cookie still logs out (205).
"""

import datetime as dt
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from django.middleware.csrf import get_token
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Account, AccountRole, Customer
from booking.guest_tokens import issue_guest_token
from booking.models import AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from specialists.models import Specialist
from tests.conftest import make_appointment, make_working_hours

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
    token.set_exp(
        from_time=timezone.now() - dt.timedelta(days=1), lifetime=dt.timedelta(minutes=15)
    )
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


def test_password_reset_check_ignores_invalid_access_cookie(salon) -> None:
    # Missing fields -> neutral 400, nothing changed (docs/DECISIONS.md §
    # "Item 11 decisions (forgot password)", "Early link check").
    baseline, garbage = _post_pair(salon.slug, "password-reset/check", {})

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
    # The project-wide default stays strict (docs/DECISIONS.md § "Fix: public
    # views ignore an invalid access cookie (decided 29.10.2026)"): the bad
    # cookie itself is rejected, not just treated as anonymous.
    assert _error_code(response) == "token_not_valid"


def test_logout_with_invalid_access_cookie_still_logs_out(salon) -> None:
    # Session cycle S1 (docs/DECISIONS.md § Session renewal, decision 3): logout
    # no longer authenticates, so a stale access cookie must not block it.
    client = APIClient(enforce_csrf_checks=True)
    client.cookies[ACCESS_COOKIE] = GARBAGE
    token = get_token(RequestFactory().get("/"))
    client.cookies["csrftoken"] = token

    response = client.post(_url(salon.slug, "logout"), {}, format="json", HTTP_X_CSRFTOKEN=token)

    assert response.status_code == 205
    assert response.cookies[ACCESS_COOKIE].value == ""
    assert int(response.cookies[ACCESS_COOKIE]["max-age"]) == 0


# --- 10. guest-reachable views ignore an invalid cookie --------------------
#
# docs/DECISIONS.md § "Fix: public views ignore an invalid access cookie
# (decided 29.10.2026)". RED phase: these 16 views still use the default
# authentication classes, so every bad cookie below turns into a 401 today.
# Each request is sent twice, once without a cookie and once with a bad one,
# and must get the same status and error code. A request that consumes
# something (a slot, a guest token's cancel, a review) gets its own row for
# each of the two sends.

# Monday 09:00-18:00 Europe/Kyiv == 06:00-15:00 UTC, as in
# test_booking_source.py; SAFE_NOW is inside the lead-time/advance window.
SAFE_NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=dt.UTC)
MONDAY = dt.date(2026, 8, 17)
FIRST_CANDIDATE = dt.datetime(2026, 8, 17, 6, 0, tzinfo=dt.UTC)

COOKIE_KINDS = ["garbage", "expired", "other_salon"]

Request = tuple[str, str, dict, dict]


def _valid_access_token(account: Account) -> str:
    """Built the way AccountTokenObtainPairSerializer.get_token() does (see
    test_auth_login_refresh_logout_account.py)."""
    token = AccessToken()
    token["user_id"] = str(account.pk)
    token["identity_model"] = "account"
    token["session_version"] = account.session_version
    return str(token)


def _bad_cookie(kind: str, valid_elsewhere: Account) -> str:
    if kind == "garbage":
        return GARBAGE
    if kind == "expired":
        return _expired_access_token()
    return _valid_access_token(valid_elsewhere)


def _send(client: APIClient, request: Request):
    method, path, data, headers = request
    if method == "GET":
        return client.get(path, data, **headers)
    return client.post(path, data, format="json", **headers)


def _salon_path(ctx: SimpleNamespace, tail: str) -> str:
    return f"/api/v1/salons/{ctx.salon.slug}/{tail}"


def _guest_appointment(ctx: SimpleNamespace, n: int, status: str, start: dt.datetime):
    with tenant_context(ctx.salon.id):
        customer = Customer.objects.create(
            salon=ctx.salon, name="Guest", email=f"guest-{n}@example.com", phone="+10000000000"
        )
    appointment = make_appointment(
        salon=ctx.salon,
        customer=customer,
        specialist=ctx.specialist,
        service=ctx.service,
        start=start,
        status=status,
    )
    with tenant_context(ctx.salon.id):
        raw_token, _row = issue_guest_token(appointment)
    return appointment, raw_token


def _guest_view(tail: str, method: str, status: str) -> Callable[[SimpleNamespace, int], Request]:
    def build(ctx: SimpleNamespace, n: int) -> Request:
        start = FIRST_CANDIDATE + dt.timedelta(hours=2 * n)
        appointment, token = _guest_appointment(ctx, n, status, start)
        path = _salon_path(ctx, f"guest/appointments/{appointment.id}/{tail}")
        return method, path, {}, {"HTTP_X_GUEST_TOKEN": token}

    return build


def _guest_booking(ctx: SimpleNamespace, n: int) -> Request:
    body = {
        "specialist": ctx.specialist.id,
        "service": ctx.service.id,
        # One week apart, so the second send does not hit the first's slot.
        "start_datetime": (FIRST_CANDIDATE + dt.timedelta(days=7 * n)).isoformat(),
        "customer_name": "Alice",
        "customer_email": f"booker-{n}@example.com",
        "customer_phone": "+10000000000",
    }
    return "POST", _salon_path(ctx, "bookings/"), body, {}


def _review_create(ctx: SimpleNamespace, n: int) -> Request:
    start = SAFE_NOW - dt.timedelta(days=2) + dt.timedelta(hours=2 * n)
    appointment, token = _guest_appointment(ctx, n, AppointmentStatus.COMPLETED, start)
    path = _salon_path(ctx, f"appointments/{appointment.id}/review/")
    return "POST", path, {"rating": 5, "text": "Lovely."}, {"HTTP_X_GUEST_TOKEN": token}


def _webhook(ctx: SimpleNamespace, n: int) -> Request:
    # Unknown provider reference: a 404 with no side effects, every time.
    body = {
        "event_id": f"evt-cookie-{n}",
        "event_type": "payment.succeeded",
        "provider_reference_id": "unknown-reference",
    }
    return "POST", "/api/v1/webhooks/payments/", body, {}


def _get(
    tail: Callable[[SimpleNamespace], str],
    params: Callable[[SimpleNamespace], dict] | None = None,
) -> Callable[[SimpleNamespace, int], Request]:
    def build(ctx: SimpleNamespace, n: int) -> Request:
        return "GET", _salon_path(ctx, tail(ctx)), (params(ctx) if params else {}), {}

    return build


# view -> (expected no-cookie status, request builder)
PUBLIC_VIEWS: dict[str, tuple[int, Callable[[SimpleNamespace, int], Request]]] = {
    # The 9 pure-public views.
    "salon_detail": (200, _get(lambda ctx: "")),
    "availability": (
        200,
        _get(
            lambda ctx: "availability/",
            lambda ctx: {
                "service": ctx.service.id,
                "date_from": MONDAY.isoformat(),
                "date_to": MONDAY.isoformat(),
            },
        ),
    ),
    "availability_specialists": (
        200,
        _get(
            lambda ctx: "availability/specialists/",
            lambda ctx: {"service": ctx.service.id, "datetime": FIRST_CANDIDATE.isoformat()},
        ),
    ),
    "reviews_list": (200, _get(lambda ctx: "reviews/")),
    "guest_booking": (201, _guest_booking),
    "guest_appointment_detail": (200, _guest_view("", "GET", AppointmentStatus.PENDING_PAYMENT)),
    "guest_appointment_cancel": (
        200,
        _guest_view("cancel/", "POST", AppointmentStatus.PENDING_PAYMENT),
    ),
    "guest_appointment_pay": (201, _guest_view("pay/", "POST", AppointmentStatus.PENDING_PAYMENT)),
    "payment_webhook": (404, _webhook),
    # The 7 mixed views.
    "categories_list": (200, _get(lambda ctx: "categories/")),
    "category_detail": (200, _get(lambda ctx: f"categories/{ctx.category.id}/")),
    "services_list": (200, _get(lambda ctx: "services/")),
    "service_detail": (200, _get(lambda ctx: f"services/{ctx.service.id}/")),
    "specialists_list": (200, _get(lambda ctx: "specialists/")),
    "specialist_detail": (200, _get(lambda ctx: f"specialists/{ctx.specialist.id}/")),
    "review_create": (201, _review_create),
}


@pytest.fixture
def ctx(salon, service_category, service, specialist) -> SimpleNamespace:
    """`specialist` is linked to `service` by the conftest fixture."""
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    return SimpleNamespace(
        salon=salon, category=service_category, service=service, specialist=specialist
    )


@pytest.mark.parametrize("cookie_kind", COOKIE_KINDS)
@pytest.mark.parametrize("view", list(PUBLIC_VIEWS))
def test_guest_reachable_view_ignores_invalid_access_cookie(
    monkeypatch, ctx, admin_account, other_salon_admin_account, view, cookie_kind
) -> None:
    monkeypatch.setattr(timezone, "now", lambda: SAFE_NOW)
    expected_status, build = PUBLIC_VIEWS[view]
    # The webhook has no salon: any account's valid cookie is "valid elsewhere".
    valid_elsewhere = admin_account if view == "payment_webhook" else other_salon_admin_account

    baseline = _send(APIClient(), build(ctx, 0))
    other = _send(_client_with_cookie(_bad_cookie(cookie_kind, valid_elsewhere)), build(ctx, 1))

    assert baseline.status_code == expected_status
    _assert_same_as_no_cookie(baseline, other)


# --- 11. controls: the fix must not loosen anything else -------------------
#
# /auth/me/ still answering 401 with a garbage cookie is already pinned by
# test_me_with_invalid_access_cookie_is_still_401 above.


def _make_inactive(kind: str, ctx: SimpleNamespace) -> int:
    with tenant_context(ctx.salon.id):
        row: ServiceCategory | Service | Specialist
        if kind == "categories":
            row = ServiceCategory.objects.create(
                salon=ctx.salon, name={"en": "Hidden"}, is_active=False
            )
        elif kind == "services":
            row = Service.objects.create(
                salon=ctx.salon,
                category=ctx.category,
                name={"en": "Hidden"},
                duration_minutes=30,
                price="100.00",
                is_active=False,
            )
        else:
            row = Specialist.objects.create(salon=ctx.salon, name={"en": "Hidden"}, is_active=False)
    return row.id


def _ids(response) -> set[int]:
    rows = response.data["results"] if isinstance(response.data, dict) else response.data
    return {row["id"] for row in rows}


@pytest.mark.parametrize("kind", ["categories", "services", "specialists"])
def test_staff_with_valid_cookie_still_sees_include_inactive(ctx, admin_account, kind) -> None:
    hidden_id = _make_inactive(kind, ctx)
    path = _salon_path(ctx, f"{kind}/")

    anonymous = APIClient().get(path, {"include_inactive": "true"})
    staff = _client_with_cookie(_valid_access_token(admin_account)).get(
        path, {"include_inactive": "true"}
    )

    assert anonymous.status_code == 200
    assert hidden_id not in _ids(anonymous)
    assert staff.status_code == 200
    assert hidden_id in _ids(staff)


def _count(kind: str, salon) -> int:
    model = {"categories": ServiceCategory, "services": Service, "specialists": Specialist}[kind]
    return model.unscoped_objects.filter(salon=salon).count()


def _write_body(kind: str, ctx: SimpleNamespace) -> dict:
    if kind == "categories":
        return {"name": {"en": "Brows"}}
    if kind == "services":
        return {
            "name": {"en": "Pedicure"},
            "category_id": ctx.category.id,
            "duration_minutes": 60,
            "price": "300.00",
        }
    return {"name": {"en": "Mia"}}


@pytest.mark.parametrize("cookie_kind", COOKIE_KINDS)
@pytest.mark.parametrize("kind", ["categories", "services", "specialists"])
def test_staff_write_with_invalid_cookie_is_still_denied(
    ctx, other_salon_admin_account, kind, cookie_kind
) -> None:
    # "other_salon" is a valid cookie of an admin of another salon.
    before = _count(kind, ctx.salon)
    client = _client_with_cookie(_bad_cookie(cookie_kind, other_salon_admin_account))

    response = client.post(_salon_path(ctx, f"{kind}/"), _write_body(kind, ctx), format="json")

    assert response.status_code in (401, 403)
    assert _count(kind, ctx.salon) == before


def test_account_with_valid_cookie_can_still_create_review(monkeypatch, ctx, customer) -> None:
    monkeypatch.setattr(timezone, "now", lambda: SAFE_NOW)
    with tenant_context(ctx.salon.id):
        account = Account.objects.create_account(
            salon=ctx.salon,
            email="reviewer@example.com",
            password=STRONG_PASSWORD,
            role=AccountRole.CLIENT,
            customer=customer,
        )
    appointment = make_appointment(
        salon=ctx.salon,
        customer=customer,
        specialist=ctx.specialist,
        service=ctx.service,
        start=SAFE_NOW - dt.timedelta(days=2),
        status=AppointmentStatus.COMPLETED,
    )

    response = _client_with_cookie(_valid_access_token(account)).post(
        _salon_path(ctx, f"appointments/{appointment.id}/review/"),
        {"rating": 5, "text": "Lovely."},
        format="json",
    )

    assert response.status_code == 201


def test_guest_booking_with_valid_account_cookie_needs_no_csrf_token(
    monkeypatch, ctx, customer
) -> None:
    # The frontend's guest booking call sends no X-CSRFToken; a logged-in
    # visitor's valid cookie must not turn it into a 403.
    monkeypatch.setattr(timezone, "now", lambda: SAFE_NOW)
    with tenant_context(ctx.salon.id):
        account = Account.objects.create_account(
            salon=ctx.salon,
            email="logged-in@example.com",
            password=STRONG_PASSWORD,
            role=AccountRole.CLIENT,
            customer=customer,
        )
    client = APIClient(enforce_csrf_checks=True)
    client.cookies[ACCESS_COOKIE] = _valid_access_token(account)

    response = _send(client, _guest_booking(ctx, 0))

    assert response.status_code == 201
