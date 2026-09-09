"""
Availability time-grid GET endpoint tests (docs/ARCHITECTURE.md § 6;
docs/DECISIONS.md § Stage 6.I decisions).

Driven through DRF's APIClient over the real URL
(/api/v1/salons/<slug>/availability/), not direct service calls and not a
manually-entered tenant_context() — this is the only way tenant context
actually binds the way a real request binds it, via
tenants.middleware.TenantResolutionMiddleware reading the URL slug.

No wall-clock dependency: the view computes `now = timezone.now()` itself
(§ Stage 6.I decisions, "the single now = timezone.now() call site"), so
every test whose result depends on where `now` falls relative to the
fixtures' dates freezes it via monkeypatch — never the real clock. Tests
whose result doesn't depend on `now` at all (a validation error raised
before the view ever reaches timezone.now(), or the zero-qualifying-
specialists empty-result path) don't need the freeze, and don't use it.
"""

import datetime as dt

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from specialists.models import Specialist, SpecialistService
from tests.conftest import make_working_hours

pytestmark = pytest.mark.django_db

UTC = dt.UTC
MONDAY = dt.date(2026, 8, 17)  # Europe/Kyiv is EEST (+3) in August — matches
# the rest of the scheduling test suite's own fixed calendar date.
FROZEN_NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=UTC)  # a day before MONDAY


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _availability_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/availability/"


def _freeze_now(monkeypatch: pytest.MonkeyPatch, value: dt.datetime = FROZEN_NOW) -> None:
    monkeypatch.setattr(timezone, "now", lambda: value)


def _make_specialist(*, salon, name: str, is_active: bool = True) -> Specialist:
    with tenant_context(salon.id):
        return Specialist.objects.create(salon=salon, name={"en": name}, is_active=is_active)


def _link_specialist_service(*, salon, specialist: Specialist, service: Service) -> None:
    with tenant_context(salon.id):
        SpecialistService.objects.create(salon=salon, specialist=specialist, service=service)


# --- any-specialist mode / specific-specialist mode / local-time shape -----


def test_any_specialist_mode_returns_union_of_local_times(client, monkeypatch, salon, service):
    """
    Two qualifying specialists, disjoint shifts on the same day, each
    exactly wide enough for the `service` fixture's occupied time (60min
    duration + 15min buffer = 75min) to fit once, at the window's own
    start. The any-specialist (no `specialist` param) response is the union
    of both — proving compute_multi_specialist_availability is actually
    wired in, and proving the local-time conversion: each string carries
    the Europe/Kyiv UTC+3 offset embedded, asserted exactly, not just
    counted.
    """
    _freeze_now(monkeypatch)
    ana = _make_specialist(salon=salon, name="Ana")
    bo = _make_specialist(salon=salon, name="Bo")
    _link_specialist_service(salon=salon, specialist=ana, service=service)
    _link_specialist_service(salon=salon, specialist=bo, service=service)
    make_working_hours(
        salon=salon,
        specialist=ana,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(10, 15),
    )
    make_working_hours(
        salon=salon,
        specialist=bo,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(11, 0),
        end_time=dt.time(12, 15),
    )

    response = client.get(
        _availability_url(salon),
        {"service": service.id, "date_from": MONDAY.isoformat(), "date_to": MONDAY.isoformat()},
    )

    assert response.status_code == 200
    assert response.data == {
        "available_times": [
            "2026-08-17T09:00:00+03:00",
            "2026-08-17T11:00:00+03:00",
        ]
    }


def test_specific_specialist_mode_returns_that_specialists_own_local_times(
    client, monkeypatch, salon, service
):
    """
    Same two-specialist setup as the any-specialist test above, but with
    `specialist=ana.id` passed directly — the response must be Ana's own
    slot only, not the union, proving the direct
    compute_candidate_start_times path is actually taken when `specialist`
    is present.
    """
    _freeze_now(monkeypatch)
    ana = _make_specialist(salon=salon, name="Ana")
    bo = _make_specialist(salon=salon, name="Bo")
    _link_specialist_service(salon=salon, specialist=ana, service=service)
    _link_specialist_service(salon=salon, specialist=bo, service=service)
    make_working_hours(
        salon=salon,
        specialist=ana,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(10, 15),
    )
    make_working_hours(
        salon=salon,
        specialist=bo,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(11, 0),
        end_time=dt.time(12, 15),
    )

    response = client.get(
        _availability_url(salon),
        {
            "service": service.id,
            "specialist": ana.id,
            "date_from": MONDAY.isoformat(),
            "date_to": MONDAY.isoformat(),
        },
    )

    assert response.status_code == 200
    assert response.data == {"available_times": ["2026-08-17T09:00:00+03:00"]}


# --- deactivated specialist, direct mode ------------------------------------


def test_deactivated_specialist_direct_mode_returns_empty_not_400(
    client, monkeypatch, salon, service
):
    """
    Pins the § Stage 6.I "no is_active filter at the view" decision: an
    inactive specialist's id is a legitimate, if empty, answer (200), never
    a 400 "doesn't exist." WorkingHours exist (so the empty result is
    provably from the is_active guard, not from having nothing scheduled).
    """
    _freeze_now(monkeypatch)
    inactive = _make_specialist(salon=salon, name="Cleo", is_active=False)
    make_working_hours(
        salon=salon,
        specialist=inactive,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(10, 15),
    )

    response = client.get(
        _availability_url(salon),
        {
            "service": service.id,
            "specialist": inactive.id,
            "date_from": MONDAY.isoformat(),
            "date_to": MONDAY.isoformat(),
        },
    )

    assert response.status_code == 200
    assert response.data == {"available_times": []}


# --- validation: missing / malformed params ---------------------------------


def test_missing_service_returns_400(client, salon):
    response = client.get(
        _availability_url(salon),
        {"date_from": MONDAY.isoformat(), "date_to": MONDAY.isoformat()},
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert "service" in response.data["error"]["details"]


def test_missing_date_from_returns_400(client, salon, service):
    response = client.get(
        _availability_url(salon),
        {"service": service.id, "date_to": MONDAY.isoformat()},
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert "date_from" in response.data["error"]["details"]


def test_malformed_date_returns_400(client, salon, service):
    response = client.get(
        _availability_url(salon),
        {"service": service.id, "date_from": "not-a-date", "date_to": MONDAY.isoformat()},
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert "date_from" in response.data["error"]["details"]


def test_malformed_service_id_returns_400(client, salon):
    response = client.get(
        _availability_url(salon),
        {"service": "not-an-id", "date_from": MONDAY.isoformat(), "date_to": MONDAY.isoformat()},
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert "service" in response.data["error"]["details"]


def test_unknown_service_id_returns_400(client, salon):
    response = client.get(
        _availability_url(salon),
        {"service": 999999, "date_from": MONDAY.isoformat(), "date_to": MONDAY.isoformat()},
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert "service" in response.data["error"]["details"]


def test_cross_tenant_service_id_returns_400_not_404(client, salon, other_salon):
    """
    The tenant-isolation assertion, made real: `other_service` genuinely
    exists in the database, just under a different salon. Resolved against
    `salon`'s scoped queryset, it is indistinguishable from a nonexistent
    id — 400, the same as test_unknown_service_id_returns_400 above, never
    404 (§ Stage 6.I decisions: this is a query-param reference, not a
    URL-addressed resource).
    """
    with tenant_context(other_salon.id):
        other_category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Nails"})
        other_service = Service.objects.create(
            salon=other_salon,
            category=other_category,
            name={"en": "Manicure"},
            duration_minutes=60,
            price="500.00",
        )

    response = client.get(
        _availability_url(salon),
        {
            "service": other_service.id,
            "date_from": MONDAY.isoformat(),
            "date_to": MONDAY.isoformat(),
        },
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert "service" in response.data["error"]["details"]


# --- InvalidDateRangeError propagation --------------------------------------


def test_date_from_after_date_to_returns_400(client, salon, service, specialist):
    """
    InvalidDateRangeError, raised inside compute_open_windows and already
    mapped to 400 by core/exceptions.py — the view does not pre-check the
    range itself (§ Stage 6.I decisions). Uses specific-specialist mode so
    compute_candidate_start_times (and so compute_open_windows) is reached
    directly, without depending on any qualifying-specialist setup.
    """
    response = client.get(
        _availability_url(salon),
        {
            "service": service.id,
            "specialist": specialist.id,
            "date_from": (MONDAY + dt.timedelta(days=3)).isoformat(),
            "date_to": MONDAY.isoformat(),
        },
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_date_range"


# --- AllowAny -----------------------------------------------------------


def test_unauthenticated_request_succeeds(client, salon, service):
    """
    Regression guard for § Stage 6.I decisions' AllowAny trap:
    DEFAULT_PERMISSION_CLASSES is IsAuthenticated globally, so a view that
    forgot to set permission_classes = [AllowAny] would 401 here. No
    specialists are linked to `service` at all, so the any-specialist result
    is deterministically empty regardless of when this test happens to run
    — no `now` freeze needed, since compute_multi_specialist_availability
    never reaches a date computation with zero qualifying specialists.
    """
    response = client.get(
        _availability_url(salon),
        {"service": service.id, "date_from": MONDAY.isoformat(), "date_to": MONDAY.isoformat()},
    )

    assert response.status_code == 200
    assert response.data == {"available_times": []}
