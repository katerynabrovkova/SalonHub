"""
Stage 17.1 — the engine's salon guard (docs/DECISIONS.md § "Stage 17 sub-step
order", 17.1 and 17.1b): `compute_candidate_start_times`, `day_schedule`,
`no_free_start_reason` and `compute_multi_specialist_availability` raise
ValueError when the specialist or the service does not belong to the `salon`
argument. With "any" (specialist=None, and always in
`compute_multi_specialist_availability`) the service is checked up front.

Each test has real WorkingHours, so a missing guard shows up as a returned
result, not as an accidental exception. `match="salon"` keeps an unrelated
ValueError (a naive `now`, for one) from satisfying these tests.

`now` is passed in explicitly; nothing here reads the real clock.
"""

import datetime as dt

import pytest

from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from scheduling.services import (
    compute_candidate_start_times,
    compute_multi_specialist_availability,
    day_schedule,
    no_free_start_reason,
)
from specialists.models import Specialist
from tests.conftest import make_working_hours

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2030, 1, 1, tzinfo=dt.UTC)
MONDAY = dt.date(2030, 1, 7)


@pytest.fixture
def other_service(other_salon):
    with tenant_context(other_salon.id):
        other_category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Nails"})
        return Service.objects.create(
            salon=other_salon,
            category=other_category,
            name={"en": "Manicure"},
            duration_minutes=60,
            price="500.00",
        )


@pytest.fixture
def other_specialist(other_salon):
    with tenant_context(other_salon.id):
        return Specialist.objects.create(salon=other_salon, name={"en": "Mary"})


def _hours(salon, specialist) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )


# --- compute_candidate_start_times -----------------------------------------


def test_compute_candidate_start_times_wrong_salon_argument_raises(
    salon, other_salon, specialist, service
):
    _hours(salon, specialist)

    with tenant_context(salon.id), pytest.raises(ValueError, match="salon"):
        compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=other_salon,
            date_from=MONDAY,
            date_to=MONDAY,
            now=NOW,
        )


def test_compute_candidate_start_times_specialist_from_another_salon_raises(
    salon, other_salon, other_specialist, service
):
    """Only the specialist mismatches: the service belongs to `salon`."""
    _hours(other_salon, other_specialist)

    with tenant_context(salon.id), pytest.raises(ValueError, match="salon"):
        compute_candidate_start_times(
            specialist=other_specialist,
            service=service,
            salon=salon,
            date_from=MONDAY,
            date_to=MONDAY,
            now=NOW,
        )


def test_compute_candidate_start_times_service_from_another_salon_raises(
    salon, specialist, other_service
):
    """Only the service mismatches: the specialist belongs to `salon`."""
    _hours(salon, specialist)

    with tenant_context(salon.id), pytest.raises(ValueError, match="salon"):
        compute_candidate_start_times(
            specialist=specialist,
            service=other_service,
            salon=salon,
            date_from=MONDAY,
            date_to=MONDAY,
            now=NOW,
        )


# --- compute_multi_specialist_availability ---------------------------------


def test_compute_multi_specialist_availability_service_from_another_salon_raises(
    salon, specialist, other_service
):
    """17.1b: with no qualifying specialist the per-specialist check never
    runs, so without an up-front check this returns {}, which looks like "no
    free time"."""
    _hours(salon, specialist)

    with tenant_context(salon.id), pytest.raises(ValueError, match="salon"):
        compute_multi_specialist_availability(
            service=other_service,
            salon=salon,
            date_from=MONDAY,
            date_to=MONDAY,
            now=NOW,
        )


# --- day_schedule ----------------------------------------------------------


def test_day_schedule_wrong_salon_argument_raises(salon, other_salon, specialist, service):
    _hours(salon, specialist)

    with tenant_context(salon.id), pytest.raises(ValueError, match="salon"):
        day_schedule(salon=other_salon, service=service, specialist=specialist, date=MONDAY)


def test_day_schedule_any_specialist_service_from_another_salon_raises(
    salon, specialist, other_service
):
    """specialist=None: the service is checked up front, before any specialist
    is looked up, so an empty specialist list cannot hide the mismatch."""
    _hours(salon, specialist)

    with tenant_context(salon.id), pytest.raises(ValueError, match="salon"):
        day_schedule(salon=salon, service=other_service, specialist=None, date=MONDAY)


# --- no_free_start_reason --------------------------------------------------


def test_no_free_start_reason_wrong_salon_argument_raises(salon, other_salon, specialist, service):
    _hours(salon, specialist)

    with tenant_context(salon.id), pytest.raises(ValueError, match="salon"):
        no_free_start_reason(
            salon=other_salon,
            service=service,
            specialist=specialist,
            date_from=MONDAY,
            date_to=MONDAY,
            weekdays=None,
            time_from=None,
            time_to=None,
            now=NOW,
        )
