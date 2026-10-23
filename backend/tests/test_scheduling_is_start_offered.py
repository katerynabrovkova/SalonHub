"""
Stage 16 sub-step 6a — `scheduling.services.is_start_offered`
(docs/DECISIONS.md § Stage 16 planning, "Sub-step 6 details", 6a): whether
one start is offered for a service, with a specific specialist or with any
qualifying one, answered by the engine exactly as booking does.

The salon runs on Asia/Tbilisi (UTC+04:00 all year), so salon-local and UTC
dates differ near midnight. All fixed `now`/start values are tz-aware
literals, never `timezone.now()`.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from core.tenancy import tenant_context
from scheduling.services import is_start_offered
from specialists.models import Specialist, SpecialistService
from tests.conftest import make_appointment, make_working_hours

pytestmark = pytest.mark.django_db

UTC = dt.UTC
TBILISI = ZoneInfo("Asia/Tbilisi")

# A Monday with 09:00-18:00 Tbilisi working hours == 05:00-14:00 UTC, so
# 05:00 UTC is the first on-grid candidate for the `service` fixture.
MONDAY = dt.date(2026, 8, 17)
FIRST_CANDIDATE = dt.datetime(2026, 8, 17, 5, 0, tzinfo=UTC)

# Well within the default min_lead_time_hours=3 / max_advance_days=60 window.
NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=UTC)


@pytest.fixture
def tbilisi_salon(salon):
    salon.timezone = "Asia/Tbilisi"
    salon.save(update_fields=["timezone"])
    return salon


def _monday_hours(salon, specialist: Specialist) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )


def _linked_specialist(salon, service, name: str) -> Specialist:
    with tenant_context(salon.id):
        specialist = Specialist.objects.create(salon=salon, name={"en": name})
        SpecialistService.objects.create(salon=salon, specialist=specialist, service=service)
    return specialist


def _offered(salon, service, specialist: Specialist | None, start: dt.datetime) -> bool:
    with tenant_context(salon.id):
        return is_start_offered(
            salon=salon, service=service, specialist=specialist, start=start, now=NOW
        )


def test_start_offered_for_a_specific_specialist(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)

    assert _offered(tbilisi_salon, service, specialist, FIRST_CANDIDATE) is True


def test_start_off_the_slot_grid_is_not_offered(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)

    off_grid = FIRST_CANDIDATE + dt.timedelta(minutes=5)
    assert _offered(tbilisi_salon, service, specialist, off_grid) is False


def test_start_taken_by_an_active_appointment_is_not_offered(
    tbilisi_salon, service, specialist, customer
):
    _monday_hours(tbilisi_salon, specialist)
    make_appointment(
        salon=tbilisi_salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
    )

    assert _offered(tbilisi_salon, service, specialist, FIRST_CANDIDATE) is False


def test_any_offered_when_one_linked_specialist_is_free(
    tbilisi_salon, service, specialist, customer
):
    other = _linked_specialist(tbilisi_salon, service, "Mary")
    _monday_hours(tbilisi_salon, specialist)
    _monday_hours(tbilisi_salon, other)
    make_appointment(
        salon=tbilisi_salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
    )

    assert _offered(tbilisi_salon, service, None, FIRST_CANDIDATE) is True


def test_any_not_offered_when_every_linked_specialist_is_taken(
    tbilisi_salon, service, specialist, customer
):
    other = _linked_specialist(tbilisi_salon, service, "Mary")
    for busy in (specialist, other):
        _monday_hours(tbilisi_salon, busy)
        make_appointment(
            salon=tbilisi_salon,
            customer=customer,
            specialist=busy,
            service=service,
            start=FIRST_CANDIDATE,
        )

    assert _offered(tbilisi_salon, service, None, FIRST_CANDIDATE) is False


def test_same_instant_with_another_utc_offset_is_offered(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)

    other_offset = FIRST_CANDIDATE.astimezone(dt.timezone(dt.timedelta(hours=2)))
    assert other_offset.utcoffset() != FIRST_CANDIDATE.astimezone(TBILISI).utcoffset()
    assert _offered(tbilisi_salon, service, specialist, other_offset) is True


def test_naive_start_raises_value_error(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)

    with pytest.raises(ValueError):
        _offered(tbilisi_salon, service, specialist, FIRST_CANDIDATE.replace(tzinfo=None))


def test_start_uses_the_salon_local_date(tbilisi_salon, service, specialist):
    """01:00 Tuesday in Tbilisi is 21:00 Monday in UTC. Only Tuesday has
    working hours, so a check that took the UTC date would see no slots."""
    tuesday = MONDAY + dt.timedelta(days=1)
    make_working_hours(
        salon=tbilisi_salon,
        specialist=specialist,
        day_of_week=tuesday.weekday(),
        start_time=dt.time(0, 0),
        end_time=dt.time(6, 0),
    )
    start = dt.datetime.combine(tuesday, dt.time(1, 0), tzinfo=TBILISI)
    assert start.astimezone(UTC).date() == MONDAY

    assert _offered(tbilisi_salon, service, specialist, start) is True
