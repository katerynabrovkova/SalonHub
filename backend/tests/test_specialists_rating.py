"""
Stage 16 sub-step 4a — `specialists.services.annotate_rating`
(docs/DECISIONS.md § Stage 16 planning, "Sub-step 4 details", 4a): the
average rating and review count shared by the specialists views and the AI
assistant. The aggregate must not be inflated by the `services` prefetch the
views use (§ Stage 13 amendment "/specialists page: serializer and card
scope").
"""

import datetime as dt

import pytest
from django.utils import timezone

from booking.models import AppointmentStatus
from catalog.models import Service
from core.tenancy import tenant_context
from reviews.models import Review
from specialists.models import Specialist
from specialists.services import annotate_rating
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db


def _make_review(*, salon, specialist, customer, service, rating: int, day: int) -> Review:
    """One COMPLETED appointment and its Review, `day` days ahead so the
    Appointment exclusion constraint never trips."""
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=timezone.now() + dt.timedelta(days=100 + day),
        status=AppointmentStatus.COMPLETED,
    )
    with tenant_context(salon.id):
        return Review.objects.create(
            salon=salon,
            appointment=appointment,
            customer=customer,
            specialist=specialist,
            rating=rating,
        )


def test_average_rating_and_review_count(salon, specialist, customer, service):
    for day, rating in enumerate([4, 5]):
        _make_review(
            salon=salon,
            specialist=specialist,
            customer=customer,
            service=service,
            rating=rating,
            day=day,
        )
    with tenant_context(salon.id):
        unreviewed = Specialist.objects.create(salon=salon, name={"en": "Mary"})

        rows = {row.pk: row for row in annotate_rating(Specialist.objects.all())}

    assert rows[specialist.pk].average_rating == 4.5
    assert rows[specialist.pk].review_count == 2
    assert rows[unreviewed.pk].average_rating is None
    assert rows[unreviewed.pk].review_count == 0


def test_services_prefetch_does_not_inflate_the_aggregate(
    salon, service_category, specialist, customer, service
):
    with tenant_context(salon.id):
        second_service = Service.objects.create(
            salon=salon,
            category=service_category,
            name={"en": "Pedicure"},
            duration_minutes=30,
            price="300.00",
        )
        specialist.services.set([service, second_service], through_defaults={"salon_id": salon.id})
    for day, rating in enumerate([3, 4, 5]):
        _make_review(
            salon=salon,
            specialist=specialist,
            customer=customer,
            service=service,
            rating=rating,
            day=day,
        )

    with tenant_context(salon.id):
        row = annotate_rating(Specialist.objects.prefetch_related("services")).get(pk=specialist.pk)
        service_count = len(row.services.all())

    assert service_count == 2
    assert row.review_count == 3
    assert row.average_rating == 4.0
