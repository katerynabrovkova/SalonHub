"""
SpecialistWriteSerializer tests (docs/DECISIONS.md § Stage 5 decisions).

Serializer-level only — no views/urls exist yet for specialists (Stage 5
sub-step 5.D covers the serializer alone). Exercises the serializer directly,
inside tenant_context, the same way catalog's serializer-level checks
(catalog/serializers.py's validate_name) are unit-tested independently of the
view layer.
"""

import pytest

from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from specialists.models import Specialist
from specialists.serializers import SpecialistWriteSerializer

pytestmark = pytest.mark.django_db


@pytest.fixture
def other_salon_service_category(other_salon):
    with tenant_context(other_salon.id):
        return ServiceCategory.objects.create(salon=other_salon, name={"en": "Nails"})


@pytest.fixture
def other_salon_service(other_salon, other_salon_service_category):
    with tenant_context(other_salon.id):
        return Service.objects.create(
            salon=other_salon,
            category=other_salon_service_category,
            name={"en": "Foreign Manicure"},
            duration_minutes=60,
            price="500.00",
            buffer_minutes=15,
        )


def test_attaching_a_service_from_another_salon_returns_400_under_services(
    salon, other_salon_service
):
    with tenant_context(salon.id):
        serializer = SpecialistWriteSerializer(
            data={"name": {"en": "Jane"}, "services": [other_salon_service.id]}
        )

        assert not serializer.is_valid()

    assert "services" in serializer.errors
    assert serializer.errors["services"]
    assert "non_field_errors" not in serializer.errors


def test_attaching_a_service_from_the_same_salon_succeeds(salon, service):
    with tenant_context(salon.id):
        serializer = SpecialistWriteSerializer(
            data={"name": {"en": "Jane"}, "services": [service.id]}
        )

        assert serializer.is_valid(), serializer.errors
        instance = serializer.save(salon_id=salon.id)

        assert list(instance.services.all()) == [service]


def test_two_specialists_in_the_same_salon_may_share_a_name(salon):
    """
    Guards the deliberate absence of UniqueConstraint(salon, name) on
    Specialist (docs/DECISIONS.md § Stage 5 decisions) — two specialists in
    one salon may legitimately share a name.
    """
    with tenant_context(salon.id):
        first = SpecialistWriteSerializer(data={"name": {"en": "Jane"}})
        assert first.is_valid(), first.errors
        first.save(salon_id=salon.id)

        second = SpecialistWriteSerializer(data={"name": {"en": "Jane"}})
        assert second.is_valid(), second.errors
        second.save(salon_id=salon.id)

        assert Specialist.objects.filter(name={"en": "Jane"}).count() == 2
