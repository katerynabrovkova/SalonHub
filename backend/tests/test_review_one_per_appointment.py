import datetime as dt

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from booking.models import AppointmentStatus
from core.tenancy import tenant_context
from reviews.models import Review
from tests.conftest import make_appointment


@pytest.mark.django_db
def test_one_review_per_appointment(salon, customer, specialist, service) -> None:
    start = timezone.now() - dt.timedelta(days=1)
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.COMPLETED,
    )

    with tenant_context(salon.id):
        Review.objects.create(
            salon=salon,
            appointment=appointment,
            customer=customer,
            specialist=specialist,
            rating=5,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Review.objects.create(
                    salon=salon,
                    appointment=appointment,
                    customer=customer,
                    specialist=specialist,
                    rating=4,
                )
