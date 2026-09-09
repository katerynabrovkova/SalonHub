"""
Specialist-availability GET endpoint tests (docs/ARCHITECTURE.md § 6;
docs/DECISIONS.md § Stage 6.K decisions) — the second of the two
availability endpoints: given a service and a single chosen moment (the
exact ISO string the time-grid endpoint returned), who is free then.

Driven through DRF's APIClient over the real URL
(/api/v1/salons/<slug>/availability/specialists/), not direct service calls
and not a manually-entered tenant_context() — the same reasoning as
test_availability_endpoint.py (§ Stage 6.I decisions): this is the only way
tenant context actually binds the way a real request binds it.

No wall-clock dependency: every case whose outcome depends on lead-time /
max-advance boundaries freezes `now` via monkeypatch, using the same frozen
`now` / MONDAY literals as test_availability_endpoint.py, well inside the
salon fixture's default 3h lead time / 60d advance window so no expected
result can shift. Cases that fail validation before the view ever reaches
`timezone.now()` (missing/malformed params) don't freeze — same precedent
as § Stage 6.I's own tests. The AllowAny case also doesn't freeze: zero
specialists linked to `service` makes the result deterministically empty
regardless of the real clock, the same reasoning § Stage 6.I's own AllowAny
test uses.

This endpoint does not exist yet (§ Stage 6.K is docs-only so far) — this is
the RED phase. Run with --continue-on-collection-errors.
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
# test_availability_endpoint.py's own fixed calendar date.
TUESDAY = MONDAY + dt.timedelta(days=1)
FROZEN_NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=UTC)  # a day before MONDAY


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _availability_specialists_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/availability/specialists/"


def _freeze_now(monkeypatch: pytest.MonkeyPatch, value: dt.datetime = FROZEN_NOW) -> None:
    monkeypatch.setattr(timezone, "now", lambda: value)


def _make_specialist(*, salon, name: str, bio: str = "", is_active: bool = True) -> Specialist:
    # Stage 11.5: `name`/`bio` are translatable JSONFields. The helper still
    # takes plain strings for brevity and wraps them as the English entry;
    # an empty `bio` stays an empty dict (the "no translations yet" state).
    with tenant_context(salon.id):
        return Specialist.objects.create(
            salon=salon,
            name={"en": name},
            bio={"en": bio} if bio else {},
            is_active=is_active,
        )


def _link_specialist_service(*, salon, specialist: Specialist, service: Service) -> None:
    with tenant_context(salon.id):
        SpecialistService.objects.create(salon=salon, specialist=specialist, service=service)


# --- (a) happy path ----------------------------------------------------------


def test_specialists_free_at_chosen_time_returns_them(client, monkeypatch, salon, service):
    """
    Two specialists, identical working hours covering the chosen moment,
    both qualified for `service`. Exact-dict response assertion (matching §
    Stage 6.I's own style) pins the full contract in one happy-path test:
    both are returned, each with exactly {photo, name, bio}. Photo-null and
    ordering get their own focused tests below (test_specialist_photo_field
    _is_null, test_specialist_order_follows_name_ordering) so a break in
    either doesn't hide behind this test's broader failure.
    """
    _freeze_now(monkeypatch)
    ana = _make_specialist(salon=salon, name="Ana", bio="Loves gel manicures.")
    bo = _make_specialist(salon=salon, name="Bo", bio="Precision nail artist.")
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
        start_time=dt.time(9, 0),
        end_time=dt.time(10, 15),
    )

    response = client.get(
        _availability_specialists_url(salon),
        {"service": service.id, "datetime": "2026-08-17T09:00:00+03:00"},
    )

    assert response.status_code == 200
    assert response.data == {
        "specialists": [
            {"photo": None, "name": "Ana", "bio": "Loves gel manicures."},
            {"photo": None, "name": "Bo", "bio": "Precision nail artist."},
        ]
    }


# --- (b) datetime -> UTC conversion / date derivation -------------------------


def test_datetime_offset_is_converted_to_utc_before_matching(client, monkeypatch, salon, service):
    """
    Dana's working hours are 14:00-15:15 *local* (Europe/Kyiv, +03:00),
    which localize to 11:00-12:15 UTC — the mapping's actual key. The
    client submits local "14:00:00+03:00". A view that compared the naive
    numeric value of the submitted string ("14:00") against the mapping's
    UTC keys, instead of first converting the submitted instant to UTC,
    would look for a specialist free at 14:00 UTC and find no one — this
    test fails under that bug and passes only when the offset is actually
    honored.
    """
    _freeze_now(monkeypatch)
    dana = _make_specialist(salon=salon, name="Dana")
    _link_specialist_service(salon=salon, specialist=dana, service=service)
    make_working_hours(
        salon=salon,
        specialist=dana,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(14, 0),
        end_time=dt.time(15, 15),
    )

    response = client.get(
        _availability_specialists_url(salon),
        {"service": service.id, "datetime": "2026-08-17T14:00:00+03:00"},
    )

    assert response.status_code == 200
    assert response.data == {"specialists": [{"photo": None, "name": "Dana", "bio": ""}]}


def test_date_is_derived_in_salon_local_time_not_utc(client, monkeypatch, salon, service):
    """
    Elin's working hours exist only on TUESDAY (local calendar date), not
    MONDAY. The client submits local "2026-08-18T00:00:00+03:00" — a moment
    that is still MONDAY in UTC (2026-08-17T21:00:00Z). A view that derived
    "the date" from the UTC value of the submitted datetime, instead of the
    salon-local date, would call the engine for MONDAY and find nothing —
    this test fails under that bug and passes only when the date is derived
    in the salon's own timezone.
    """
    _freeze_now(monkeypatch)
    elin = _make_specialist(salon=salon, name="Elin")
    _link_specialist_service(salon=salon, specialist=elin, service=service)
    make_working_hours(
        salon=salon,
        specialist=elin,
        day_of_week=TUESDAY.weekday(),
        start_time=dt.time(0, 0),
        end_time=dt.time(1, 15),
    )

    response = client.get(
        _availability_specialists_url(salon),
        {"service": service.id, "datetime": "2026-08-18T00:00:00+03:00"},
    )

    assert response.status_code == 200
    assert response.data == {"specialists": [{"photo": None, "name": "Elin", "bio": ""}]}


# --- (c) empty result is valid, not an error ----------------------------------


def test_no_one_free_at_chosen_time_returns_empty_200(client, monkeypatch, salon, service):
    """
    Fay's only working window (Monday 09:00-10:15 local) is exactly wide
    enough for one candidate, anchored at the window start (09:00) —
    § Stage 6.E decisions' stepping is anchored to window.start, never to
    another grid. 09:15 is not itself a valid candidate start (occupied_
    minutes from 09:15 would run past window.end), so the mapping has no
    entry for that exact moment: a legitimate "nobody free right now"
    answer, not a malformed request.
    """
    _freeze_now(monkeypatch)
    fay = _make_specialist(salon=salon, name="Fay")
    _link_specialist_service(salon=salon, specialist=fay, service=service)
    make_working_hours(
        salon=salon,
        specialist=fay,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(10, 15),
    )

    response = client.get(
        _availability_specialists_url(salon),
        {"service": service.id, "datetime": "2026-08-17T09:15:00+03:00"},
    )

    assert response.status_code == 200
    assert response.data == {"specialists": []}


# --- (d) error contract, inherited from § Stage 6.I decisions -----------------


def test_missing_service_returns_400(client, salon):
    response = client.get(
        _availability_specialists_url(salon),
        {"datetime": "2026-08-17T09:00:00+03:00"},
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert "service" in response.data["error"]["details"]


def test_missing_datetime_returns_400(client, salon, service):
    response = client.get(
        _availability_specialists_url(salon),
        {"service": service.id},
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert "datetime" in response.data["error"]["details"]


def test_malformed_service_id_returns_400(client, salon):
    response = client.get(
        _availability_specialists_url(salon),
        {"service": "not-an-id", "datetime": "2026-08-17T09:00:00+03:00"},
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert "service" in response.data["error"]["details"]


def test_malformed_datetime_returns_400(client, salon, service):
    response = client.get(
        _availability_specialists_url(salon),
        {"service": service.id, "datetime": "not-a-datetime"},
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert "datetime" in response.data["error"]["details"]


def test_naive_datetime_returns_400(client, salon, service):
    """
    New decision, to be recorded in DECISIONS.md § Stage 6.K as a follow-up
    once this is green: a `datetime` with no UTC offset must be rejected,
    not silently interpreted via Django's current timezone. The offset
    exists precisely to disambiguate the moment on a DST-transition day (§
    Stage 6.K decisions' datetime contract); accepting a naive value would
    reintroduce the exact ambiguity the offset requirement removes.
    """
    response = client.get(
        _availability_specialists_url(salon),
        {"service": service.id, "datetime": "2026-08-17T09:00:00"},
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert "datetime" in response.data["error"]["details"]


def test_unknown_service_id_returns_400(client, salon):
    response = client.get(
        _availability_specialists_url(salon),
        {"service": 999999, "datetime": "2026-08-17T09:00:00+03:00"},
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert "service" in response.data["error"]["details"]


def test_cross_tenant_service_id_returns_400_not_404(client, salon, other_salon):
    """
    Same tenant-isolation assertion as § Stage 6.I decisions' own version:
    `other_service` genuinely exists, just under a different salon.
    Resolved against `salon`'s scoped queryset it is indistinguishable from
    a nonexistent id — 400, never 404 (a query-param reference, not a
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
        _availability_specialists_url(salon),
        {"service": other_service.id, "datetime": "2026-08-17T09:00:00+03:00"},
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert "service" in response.data["error"]["details"]


# --- (e) response shape --------------------------------------------------------


def test_specialist_photo_field_is_null(client, monkeypatch, salon, service):
    """
    Pins § Stage 6.J's current state: every specialist's `photo` is `null`
    — no upload flow exists yet. A focused test, kept separate from the
    happy-path exact-dict test above so a broken photo serialization can't
    hide behind a broader failure.
    """
    _freeze_now(monkeypatch)
    gia = _make_specialist(salon=salon, name="Gia")
    _link_specialist_service(salon=salon, specialist=gia, service=service)
    make_working_hours(
        salon=salon,
        specialist=gia,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(10, 15),
    )

    response = client.get(
        _availability_specialists_url(salon),
        {"service": service.id, "datetime": "2026-08-17T09:00:00+03:00"},
    )

    assert response.status_code == 200
    assert response.data["specialists"][0]["photo"] is None


def test_specialist_order_follows_creation_order(client, monkeypatch, salon, service):
    """
    Specialist.Meta.ordering is ["created_at", "id"] (Stage 11.5 sub-step 1:
    `name` became a translatable JSON dict and can no longer be an ORDER BY
    key). Zoe is created before Amy, so the correct response lists Zoe first
    — creation order, not name-alphabetical. Kept separate from the
    happy-path test above for the same failure-attribution reason as
    test_specialist_photo_field_is_null.
    """
    _freeze_now(monkeypatch)
    zoe = _make_specialist(salon=salon, name="Zoe")
    amy = _make_specialist(salon=salon, name="Amy")
    _link_specialist_service(salon=salon, specialist=zoe, service=service)
    _link_specialist_service(salon=salon, specialist=amy, service=service)
    for spec in (zoe, amy):
        make_working_hours(
            salon=salon,
            specialist=spec,
            day_of_week=MONDAY.weekday(),
            start_time=dt.time(9, 0),
            end_time=dt.time(10, 15),
        )

    response = client.get(
        _availability_specialists_url(salon),
        {"service": service.id, "datetime": "2026-08-17T09:00:00+03:00"},
    )

    assert response.status_code == 200
    assert [entry["name"] for entry in response.data["specialists"]] == ["Zoe", "Amy"]


# --- (f) AllowAny --------------------------------------------------------------


def test_unauthenticated_request_succeeds(client, salon, service):
    """
    Regression guard for § Stage 6.K decisions' AllowAny requirement:
    DEFAULT_PERMISSION_CLASSES is IsAuthenticated globally, so a view that
    forgot to set permission_classes = [AllowAny] would 401 here. No
    specialists are linked to `service` at all, so the result is
    deterministically empty regardless of when this test happens to run —
    no `now` freeze needed, same reasoning as § Stage 6.I's own AllowAny
    test.
    """
    response = client.get(
        _availability_specialists_url(salon),
        {"service": service.id, "datetime": "2026-08-17T09:00:00+03:00"},
    )

    assert response.status_code == 200
    assert response.data == {"specialists": []}
