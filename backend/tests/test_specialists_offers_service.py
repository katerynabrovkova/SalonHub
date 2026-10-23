"""
Stage 16 sub-step 6a — `specialists.services.specialist_offers_service`
(docs/DECISIONS.md § Stage 16 planning, "Sub-step 6 details", 6a): the one
specialist-service link check shared by booking, the slots view and the
assistant tools.
"""

import pytest

from catalog.models import Service
from core.tenancy import tenant_context
from specialists.models import Specialist, SpecialistService
from specialists.services import specialist_offers_service

pytestmark = pytest.mark.django_db


def test_linked_pair_offers_the_service(salon, service, specialist):
    with tenant_context(salon.id):
        assert specialist_offers_service(specialist, service) is True


def test_unlinked_pair_does_not_offer_the_service(salon, service):
    with tenant_context(salon.id):
        unlinked = Specialist.objects.create(salon=salon, name={"en": "Mary"})

        assert specialist_offers_service(unlinked, service) is False


def test_specialist_linked_only_to_another_service_does_not_offer_it(
    salon, service_category, service
):
    with tenant_context(salon.id):
        other_service = Service.objects.create(
            salon=salon,
            category=service_category,
            name={"en": "Pedicure"},
            duration_minutes=60,
            price="600.00",
        )
        mary = Specialist.objects.create(salon=salon, name={"en": "Mary"})
        SpecialistService.objects.create(salon=salon, specialist=mary, service=other_service)

        assert specialist_offers_service(mary, other_service) is True
        assert specialist_offers_service(mary, service) is False


def test_only_the_higher_pk_specialist_is_unlinked(salon, service, specialist):
    with tenant_context(salon.id):
        unlinked = Specialist.objects.create(salon=salon, name={"en": "Mary"})
        assert unlinked.pk > specialist.pk

        assert specialist_offers_service(unlinked, service) is False
        assert specialist_offers_service(specialist, service) is True
