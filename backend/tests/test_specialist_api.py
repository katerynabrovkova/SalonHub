"""
Specialist API tests (docs/ARCHITECTURE.md § 4, § 13; Stage 5 sub-step 5).
"""

import datetime as dt

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from booking.models import AppointmentStatus
from core.tenancy import tenant_context
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture
def deactivated_specialist(salon, specialist):
    with tenant_context(salon.id):
        specialist.is_active = False
        specialist.save(update_fields=["is_active"])
    return specialist


def _specialist_list_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/specialists/"


def _specialist_detail_url(salon, specialist) -> str:
    return f"/api/v1/salons/{salon.slug}/specialists/{specialist.id}/"


# --- public reads / write authorization ------------------------------------


def test_guest_can_list_specialists_without_auth(client, salon, specialist):
    response = client.get(_specialist_list_url(salon))

    assert response.status_code == 200
    assert response.data["results"][0]["id"] == specialist.id


def test_guest_can_retrieve_a_specialist_without_auth(client, salon, specialist):
    response = client.get(_specialist_detail_url(salon, specialist))

    assert response.status_code == 200
    assert response.data["id"] == specialist.id


def test_guest_cannot_create_a_specialist(client, salon):
    response = client.post(_specialist_list_url(salon), {"name": "New Specialist"})

    assert response.status_code == 401


def test_client_supplied_salon_in_body_is_ignored(client, salon, other_salon, admin_account):
    client.force_authenticate(user=admin_account)
    response = client.post(
        _specialist_list_url(salon), {"name": "New Specialist", "salon": other_salon.id}
    )

    assert response.status_code == 201
    assert response.data["salon"] == salon.id


# --- include_inactive: staff-only, this-salon-only --------------------------


def test_staff_can_see_inactive_specialist_via_include_inactive(
    client, salon, admin_account, deactivated_specialist
):
    client.force_authenticate(user=admin_account)
    response = client.get(_specialist_list_url(salon) + "?include_inactive=true")

    assert response.status_code == 200
    assert [row["id"] for row in response.data["results"]] == [deactivated_specialist.id]


def test_anonymous_include_inactive_true_has_no_effect(client, salon, deactivated_specialist):
    """The leak path: an unauthenticated request passing include_inactive=true
    must get exactly the same (empty) result as if it hadn't passed it."""
    response = client.get(_specialist_list_url(salon) + "?include_inactive=true")

    assert response.status_code == 200
    assert response.data["results"] == []


def test_authenticated_customer_include_inactive_true_has_no_effect(
    client, salon, deactivated_specialist
):
    """The other leak path: an authenticated but non-staff principal (an
    Account with role=client) passing include_inactive=true must not get
    elevated visibility either — IsSalonStaff's Account admin-role lookup,
    not just is_authenticated, is what gates this."""
    with tenant_context(salon.id):
        customer = Customer.objects.create(
            salon=salon, name="Bob", email="bob@example.com", phone="+10000000001"
        )
        client_account = Account.objects.create_account(
            salon=salon,
            email="bob@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
            customer=customer,
        )
    client.force_authenticate(user=client_account)

    response = client.get(_specialist_list_url(salon) + "?include_inactive=true")

    assert response.status_code == 200
    assert response.data["results"] == []


def test_cross_salon_staff_include_inactive_gets_ordinary_public_result(
    client, salon, other_salon_admin_account, deactivated_specialist
):
    client.force_authenticate(user=other_salon_admin_account)
    response = client.get(_specialist_list_url(salon) + "?include_inactive=true")

    assert response.status_code == 200
    assert response.data["results"] == []


# --- deactivation: future-appointments refusal ------------------------------


def test_deactivating_specialist_with_confirmed_future_appointment_returns_409_with_ids(
    client, salon, admin_account, specialist, customer, service
):
    start = timezone.now() + dt.timedelta(days=1)
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.CONFIRMED,
    )

    client.force_authenticate(user=admin_account)
    response = client.delete(_specialist_detail_url(salon, specialist))

    assert response.status_code == 409
    assert response.data["error"]["code"] == "specialist_has_future_appointments"
    assert response.data["error"]["details"]["future_appointment_count"] == 1
    assert response.data["error"]["details"]["future_appointment_ids"] == [appointment.id]

    with tenant_context(salon.id):
        specialist.refresh_from_db()
    assert specialist.is_active is True


def test_deactivating_specialist_with_pending_payment_future_appointment_returns_409(
    client, salon, admin_account, specialist, customer, service
):
    """
    The easy-to-get-wrong case: a PENDING_PAYMENT appointment hasn't been
    confirmed yet, but it still holds the slot (it's in
    ACTIVE_APPOINTMENT_STATUSES, same as the exclusion constraint treats it)
    and a customer is still expecting it to happen.
    """
    start = timezone.now() + dt.timedelta(days=1)
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.PENDING_PAYMENT,
    )

    client.force_authenticate(user=admin_account)
    response = client.delete(_specialist_detail_url(salon, specialist))

    assert response.status_code == 409
    assert response.data["error"]["code"] == "specialist_has_future_appointments"


def test_deactivating_specialist_with_only_cancelled_or_past_appointments_succeeds(
    client, salon, admin_account, specialist, customer, service
):
    future_but_cancelled_start = timezone.now() + dt.timedelta(days=1)
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=future_but_cancelled_start,
        status=AppointmentStatus.CANCELLED,
    )
    past_start = timezone.now() - dt.timedelta(days=3)
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=past_start,
        status=AppointmentStatus.CONFIRMED,
    )

    client.force_authenticate(user=admin_account)
    response = client.delete(_specialist_detail_url(salon, specialist))

    assert response.status_code == 204
    with tenant_context(salon.id):
        specialist.refresh_from_db()
    assert specialist.is_active is False


def test_deactivating_specialist_with_appointment_in_progress_returns_409(
    client, salon, admin_account, specialist, customer, service
):
    """Started 10 minutes ago, ends in 50 (service.duration_minutes=60): the
    specialist has a live commitment right now, not just a future one."""
    start = timezone.now() - dt.timedelta(minutes=10)
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.CONFIRMED,
    )

    client.force_authenticate(user=admin_account)
    response = client.delete(_specialist_detail_url(salon, specialist))

    assert response.status_code == 409
    assert response.data["error"]["code"] == "specialist_has_future_appointments"


def test_deactivate_then_reactivate_round_trip(client, salon, admin_account, specialist):
    client.force_authenticate(user=admin_account)

    delete_response = client.delete(_specialist_detail_url(salon, specialist))
    assert delete_response.status_code == 204

    hidden = client.get(_specialist_detail_url(salon, specialist))
    assert hidden.status_code == 404

    reactivate_response = client.patch(
        _specialist_detail_url(salon, specialist), {"is_active": True}, format="json"
    )
    assert reactivate_response.status_code == 200
    assert reactivate_response.data["is_active"] is True

    visible_again = client.get(_specialist_detail_url(salon, specialist))
    assert visible_again.status_code == 200
