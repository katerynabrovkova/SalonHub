import datetime as dt

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from booking.models import AppointmentStatus
from core.tenancy import tenant_context
from specialists.models import Specialist
from tests.conftest import make_appointment


@pytest.mark.django_db
def test_overlapping_active_appointments_for_same_specialist_are_rejected(
    salon, customer, specialist, service
) -> None:
    start = timezone.now() + dt.timedelta(days=1)
    make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=start
    )

    overlapping_start = start + dt.timedelta(minutes=10)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            make_appointment(
                salon=salon,
                customer=customer,
                specialist=specialist,
                service=service,
                start=overlapping_start,
            )


@pytest.mark.django_db
def test_back_to_back_appointment_after_buffer_is_allowed(
    salon, customer, specialist, service
) -> None:
    start = timezone.now() + dt.timedelta(days=1)
    first = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=start
    )

    second = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=first.blocked_until,
    )
    assert second.pk is not None


@pytest.mark.django_db
def test_overlapping_appointments_for_different_specialists_are_allowed(
    salon, customer, specialist, service
) -> None:
    with tenant_context(salon.id):
        other_specialist = Specialist.objects.create(salon=salon, name={"en": "Other Specialist"})

    start = timezone.now() + dt.timedelta(days=1)
    make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=start
    )
    second = make_appointment(
        salon=salon, customer=customer, specialist=other_specialist, service=service, start=start
    )
    assert second.pk is not None


@pytest.mark.django_db
def test_overlapping_with_a_cancelled_appointment_is_allowed(
    salon, customer, specialist, service
) -> None:
    start = timezone.now() + dt.timedelta(days=1)
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.CANCELLED,
    )
    second = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=start
    )
    assert second.pk is not None
