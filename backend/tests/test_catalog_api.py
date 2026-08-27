"""
Catalog API tests (docs/ARCHITECTURE.md § 4, § 13; Stage 4 sub-step 5).
"""

import psycopg
import pytest
from django.db import IntegrityError
from rest_framework.test import APIClient

from catalog.models import Service, ServiceCategory
from catalog.serializers import ServiceCategorySerializer
from core.exceptions import exception_handler
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _category_list_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/categories/"


def _category_detail_url(salon, category) -> str:
    return f"/api/v1/salons/{salon.slug}/categories/{category.id}/"


def _service_list_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/services/"


def _service_detail_url(salon, service) -> str:
    return f"/api/v1/salons/{salon.slug}/services/{service.id}/"


# --- public reads / write authorization ------------------------------------


def test_guest_can_list_categories_without_auth(client, salon, service_category):
    response = client.get(_category_list_url(salon))

    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == service_category.id


def test_guest_can_retrieve_a_service_without_auth(client, salon, service):
    response = client.get(_service_detail_url(salon, service))

    assert response.status_code == 200
    assert response.data["id"] == service.id


def test_guest_cannot_create_a_category(client, salon):
    response = client.post(_category_list_url(salon), {"name": "New Cat"})

    assert response.status_code == 401


def test_guest_cannot_create_a_service(client, salon, service_category):
    response = client.post(
        _service_list_url(salon),
        {
            "category_id": service_category.id,
            "name": "New Service",
            "duration_minutes": 30,
            "price": "100.00",
        },
    )

    assert response.status_code == 401


# --- cross-salon staff: no elevated visibility ------------------------------


def test_cross_salon_staff_include_inactive_gets_ordinary_public_result(
    client, salon, other_salon, other_salon_admin_account, service_category
):
    with tenant_context(salon.id):
        ServiceCategory.objects.filter(pk=service_category.pk).update(is_active=False)

    client.force_authenticate(user=other_salon_admin_account)
    response = client.get(_category_list_url(salon) + "?include_inactive=true")

    assert response.status_code == 200
    assert response.data["results"] == []


# --- soft delete -------------------------------------------------------------


def test_soft_deleted_service_hidden_from_public_but_row_persists(
    client, salon, admin_account, service
):
    client.force_authenticate(user=admin_account)
    delete_response = client.delete(_service_detail_url(salon, service))
    assert delete_response.status_code == 204

    client.force_authenticate(user=None)
    public_response = client.get(_service_detail_url(salon, service))
    assert public_response.status_code == 404

    with tenant_context(salon.id):
        service.refresh_from_db()
    assert service.is_active is False


def test_deleting_category_with_active_services_returns_409(
    client, salon, admin_account, service_category, service
):
    client.force_authenticate(user=admin_account)
    response = client.delete(_category_detail_url(salon, service_category))

    assert response.status_code == 409
    assert response.data["error"]["code"] == "category_has_active_services"
    with tenant_context(salon.id):
        service_category.refresh_from_db()
    assert service_category.is_active is True


def test_deactivate_then_reactivate_round_trip(client, salon, admin_account, service_category):
    client.force_authenticate(user=admin_account)

    delete_response = client.delete(_category_detail_url(salon, service_category))
    assert delete_response.status_code == 204

    hidden = client.get(_category_detail_url(salon, service_category))
    assert hidden.status_code == 404

    reactivate_response = client.patch(
        _category_detail_url(salon, service_category), {"is_active": True}, format="json"
    )
    assert reactivate_response.status_code == 200
    assert reactivate_response.data["is_active"] is True

    visible_again = client.get(_category_detail_url(salon, service_category))
    assert visible_again.status_code == 200


# --- duplicate name: validate_name path and IntegrityError path ------------


def test_duplicate_category_name_returns_400_via_validate_name(
    client, salon, admin_account, service_category
):
    client.force_authenticate(user=admin_account)
    response = client.post(_category_list_url(salon), {"name": service_category.name})

    assert response.status_code == 400
    assert response.data["error"]["details"]["name"] == [
        "A category with this name already exists."
    ]


def test_race_that_slips_past_validate_name_still_returns_structured_400(
    client, salon, admin_account, service_category, monkeypatch
):
    """
    validate_name is check-then-write and can't close a real concurrent
    race (docs/DECISIONS.md § Stage 4 decisions). Monkeypatching it to a
    no-op simulates "the check somehow didn't fire," so the duplicate
    reaches the real database constraint, and this asserts
    core.exceptions.exception_handler's UniqueViolation branch — not
    validate_name — is what produces the 400.
    """
    monkeypatch.setattr(ServiceCategorySerializer, "validate_name", lambda self, value: value)
    client.force_authenticate(user=admin_account)

    response = client.post(_category_list_url(salon), {"name": service_category.name})

    assert response.status_code == 400
    assert response.data["error"]["code"] == "unique_violation"


def test_unique_violation_is_translated_to_a_structured_400_not_a_500():
    """Unit-level: exercises core.exceptions.exception_handler's branch directly."""
    try:
        try:
            raise psycopg.errors.UniqueViolation("duplicate key value violates unique constraint")
        except psycopg.errors.UniqueViolation as cause:
            raise IntegrityError("duplicate key") from cause
    except IntegrityError as exc:
        response = exception_handler(exc, {})

    assert response is not None
    assert response.status_code == 400
    assert response.data["error"]["code"] == "unique_violation"


# --- cross-salon FK / server-assigned salon ---------------------------------


def test_posting_a_category_id_from_another_salon_returns_400(
    client, salon, other_salon, admin_account
):
    with tenant_context(other_salon.id):
        foreign_category = ServiceCategory.objects.create(salon=other_salon, name="Foreign")

    client.force_authenticate(user=admin_account)
    response = client.post(
        _service_list_url(salon),
        {
            "category_id": foreign_category.id,
            "name": "Cross Salon Service",
            "duration_minutes": 30,
            "price": "100.00",
        },
    )

    assert response.status_code == 400
    assert "category_id" in response.data["error"]["details"]


def test_client_supplied_salon_in_body_is_ignored(client, salon, other_salon, admin_account):
    client.force_authenticate(user=admin_account)
    response = client.post(_category_list_url(salon), {"name": "New Cat", "salon": other_salon.id})

    assert response.status_code == 201
    assert response.data["salon"] == salon.id


# --- query count / pagination shape -----------------------------------------


def test_service_list_select_related_avoids_n_plus_one(
    client, salon, service_category, django_assert_num_queries
):
    with tenant_context(salon.id):
        for i in range(5):
            Service.objects.create(
                salon=salon,
                category=service_category,
                name=f"Service {i}",
                duration_minutes=30,
                price="100.00",
            )

    # 1 Salon lookup (TenantResolutionMiddleware) + 1 COUNT (pagination) + 1
    # SELECT with JOIN — verified empirically, not assumed.
    with django_assert_num_queries(3):
        response = client.get(_service_list_url(salon))
    assert response.status_code == 200
    assert response.data["count"] == 5


def test_category_list_query_count(client, salon, django_assert_num_queries):
    with tenant_context(salon.id):
        for i in range(5):
            ServiceCategory.objects.create(salon=salon, name=f"Category {i}")

    # 1 Salon lookup (TenantResolutionMiddleware) + 1 COUNT (pagination) + 1 SELECT
    with django_assert_num_queries(3):
        response = client.get(_category_list_url(salon))
    assert response.status_code == 200
    assert response.data["count"] == 5


def test_service_list_response_has_paginated_shape(client, salon, service):
    response = client.get(_service_list_url(salon))

    assert response.status_code == 200
    assert set(response.data.keys()) == {"count", "next", "previous", "results"}
    assert response.data["results"][0]["id"] == service.id
