"""
Stage 11 Part 2 — public review list endpoint (docs/DECISIONS.md § Stage 11
Part 2). Tests only — written before the view, serializer, and urls.py entry
exist.

    GET /api/v1/salons/<slug>/reviews/

RED shape: no route is registered for ``reviews/`` yet (reviews/urls.py only
carries the write endpoint), so every GET below resolves to a plain Django
404 (``HttpResponseNotFound``, no DRF renderer) and each test fails on
``assert 404 == 200``. That is the "endpoint doesn't exist yet" signal —
not a 500 and not a fixture error.

Structure mirrors test_specialist_api.py (public list read under the same
URL prefix): ``APIClient`` fixture, ``_url(salon)`` helper, ``client.get``
with no auth.

Contract notes / DECISIONS.md discrepancy (flagged, not silently resolved):
DECISIONS.md § Stage 11 Part 2 "Read endpoint" only states "grouped by
specialist ... list of {specialist, reviews} blocks" and "no pagination".
It does NOT record: group ordering (by review count desc), the tie-break,
within-group ordering (created_at desc), the per-review field shape, or the
omission of customer-identifying fields. These tests follow the fuller
contract handed to this task; DECISIONS.md should be tightened when the
endpoint is implemented.

Tie-break chosen here: when two specialists have the same review count, the
lower specialist ``id`` sorts first (deterministic, not incidental DB order).
"""

import datetime as dt

import pytest
from rest_framework.test import APIClient

from accounts.models import Customer
from booking.models import AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from reviews.models import Review
from specialists.models import Specialist
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

BASE_START = dt.datetime(2026, 6, 1, 10, 0, tzinfo=dt.UTC)
BASE_CREATED = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture
def specialist2(salon):
    with tenant_context(salon.id):
        return Specialist.objects.create(salon=salon, name="Zoe")


def _reviews_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/reviews/"


_slot_counter = 0


def _make_review(
    *, salon, specialist, customer, service, rating=5, text="", created_at=None
) -> Review:
    """Create one COMPLETED appointment + its Review. Each call uses a fresh
    day-spaced slot so the Appointment exclusion constraint never trips."""
    global _slot_counter
    _slot_counter += 1
    start = BASE_START + dt.timedelta(days=_slot_counter)
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.COMPLETED,
    )
    with tenant_context(salon.id):
        review = Review.objects.create(
            salon=salon,
            appointment=appointment,
            customer=customer,
            specialist=specialist,
            rating=rating,
            text=text,
        )
        if created_at is not None:
            # .update() bypasses auto_now_add (same pattern as
            # test_account_verification.py).
            Review.objects.filter(pk=review.pk).update(created_at=created_at)
    return review


@pytest.fixture(autouse=True)
def _reset_slot_counter():
    global _slot_counter
    _slot_counter = 0
    yield


def _groups_by_specialist_id(payload) -> dict[int, dict]:
    return {group["specialist"]["id"]: group for group in payload}


# --- 1. grouped by specialist --------------------------------------------


def test_reviews_are_grouped_one_entry_per_specialist_with_correct_counts(
    client, salon, specialist, specialist2, customer, service
):
    _make_review(salon=salon, specialist=specialist, customer=customer, service=service)
    _make_review(salon=salon, specialist=specialist, customer=customer, service=service)
    _make_review(salon=salon, specialist=specialist2, customer=customer, service=service)

    response = client.get(_reviews_url(salon))

    assert response.status_code == 200
    assert isinstance(response.data, list)
    groups = _groups_by_specialist_id(response.data)
    assert set(groups) == {specialist.id, specialist2.id}
    assert len(groups[specialist.id]["reviews"]) == 2
    assert len(groups[specialist2.id]["reviews"]) == 1
    assert groups[specialist.id]["specialist"]["name"] == specialist.name


# --- 2. more reviews ranks first ---------------------------------------


def test_specialist_with_more_reviews_is_listed_before_one_with_fewer(
    client, salon, specialist, specialist2, customer, service
):
    _make_review(salon=salon, specialist=specialist, customer=customer, service=service)
    for _ in range(3):
        _make_review(salon=salon, specialist=specialist2, customer=customer, service=service)

    response = client.get(_reviews_url(salon))

    assert response.status_code == 200
    ordered_ids = [group["specialist"]["id"] for group in response.data]
    assert ordered_ids == [specialist2.id, specialist.id]


# --- 3. within-group newest first ------------------------------------


def test_reviews_within_a_group_are_ordered_newest_first(
    client, salon, specialist, customer, service
):
    oldest = _make_review(
        salon=salon,
        specialist=specialist,
        customer=customer,
        service=service,
        created_at=BASE_CREATED,
    )
    middle = _make_review(
        salon=salon,
        specialist=specialist,
        customer=customer,
        service=service,
        created_at=BASE_CREATED + dt.timedelta(hours=1),
    )
    newest = _make_review(
        salon=salon,
        specialist=specialist,
        customer=customer,
        service=service,
        created_at=BASE_CREATED + dt.timedelta(hours=2),
    )

    response = client.get(_reviews_url(salon))

    assert response.status_code == 200
    review_ids = [r["id"] for r in response.data[0]["reviews"]]
    assert review_ids == [newest.id, middle.id, oldest.id]


# --- 4. zero-review specialist absent -------------------------------


def test_a_specialist_with_no_reviews_does_not_appear(
    client, salon, specialist, specialist2, customer, service
):
    _make_review(salon=salon, specialist=specialist, customer=customer, service=service)

    response = client.get(_reviews_url(salon))

    assert response.status_code == 200
    assert [group["specialist"]["id"] for group in response.data] == [specialist.id]


# --- 5. public, no auth -------------------------------------------


def test_endpoint_is_public_and_returns_200_without_any_auth(
    client, salon, specialist, customer, service
):
    _make_review(salon=salon, specialist=specialist, customer=customer, service=service)

    response = client.get(_reviews_url(salon))

    assert response.status_code == 200


# --- 6. cross-tenant isolation (empirical) --------------------------


def test_reviews_from_a_different_salon_never_appear(
    client, salon, other_salon, specialist, customer, service
):
    mine = _make_review(salon=salon, specialist=specialist, customer=customer, service=service)

    with tenant_context(other_salon.id):
        o_category = ServiceCategory.objects.create(salon=other_salon, name="Hair")
        o_service = Service.objects.create(
            salon=other_salon,
            category=o_category,
            name="Cut",
            duration_minutes=30,
            price="150.00",
            buffer_minutes=0,
        )
        o_specialist = Specialist.objects.create(salon=other_salon, name="Otto")
        o_customer = Customer.objects.create(
            salon=other_salon, name="Olga", email="olga@example.com", phone="+10000000007"
        )
    theirs = _make_review(
        salon=other_salon,
        specialist=o_specialist,
        customer=o_customer,
        service=o_service,
    )

    response = client.get(_reviews_url(salon))

    assert response.status_code == 200
    returned_specialist_ids = {group["specialist"]["id"] for group in response.data}
    returned_review_ids = {r["id"] for group in response.data for r in group["reviews"]}
    assert returned_specialist_ids == {specialist.id}
    assert returned_review_ids == {mine.id}
    assert o_specialist.id not in returned_specialist_ids
    assert theirs.id not in returned_review_ids


# --- 7. empty salon -------------------------------------------


def test_salon_with_no_reviews_returns_200_and_empty_list(client, salon):
    response = client.get(_reviews_url(salon))

    assert response.status_code == 200
    assert response.data == []


# --- 8. no customer-identifying leak --------------------------


def test_response_does_not_leak_customer_identifying_fields(
    client, salon, specialist, customer, service
):
    _make_review(
        salon=salon,
        specialist=specialist,
        customer=customer,
        service=service,
        text="Great work",
    )

    response = client.get(_reviews_url(salon))

    assert response.status_code == 200
    review = response.data[0]["reviews"][0]
    assert {"id", "rating", "text", "created_at"} <= set(review)
    assert "customer" not in review
    assert "customer_id" not in review

    # The customer's PII (name/email/phone) must not appear anywhere in the
    # serialized payload. Customer model identifying fields: name, email, phone.
    blob = str(response.data)
    assert customer.name not in blob
    assert customer.email not in blob
    assert customer.phone not in blob
