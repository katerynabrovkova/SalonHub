"""
Specialist API tests (docs/ARCHITECTURE.md § 4, § 13; Stage 5 sub-step 5).
"""

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from booking.models import AppointmentStatus
from catalog.models import Service
from core.tenancy import tenant_context
from reviews.models import Review
from specialists.models import Specialist
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
    response = client.post(
        _specialist_list_url(salon), {"name": {"en": "New Specialist"}}, format="json"
    )

    assert response.status_code == 401


def test_filter_by_service_returns_only_matching_specialists(client, salon, service_category):
    from catalog.models import Service

    with tenant_context(salon.id):
        service_a = Service.objects.create(
            salon=salon,
            category=service_category,
            name={"en": "Manicure"},
            duration_minutes=60,
            price="500.00",
        )
        service_b = Service.objects.create(
            salon=salon,
            category=service_category,
            name={"en": "Pedicure"},
            duration_minutes=60,
            price="500.00",
        )
        specialist_1 = Specialist.objects.create(salon=salon, name={"en": "Jane"})
        specialist_2 = Specialist.objects.create(salon=salon, name={"en": "Ann"})
        specialist_3 = Specialist.objects.create(salon=salon, name={"en": "Mia"})
        specialist_1.services.set([service_a], through_defaults={"salon_id": salon.id})
        specialist_2.services.set([service_a], through_defaults={"salon_id": salon.id})
        specialist_3.services.set([service_b], through_defaults={"salon_id": salon.id})

    response = client.get(_specialist_list_url(salon) + f"?service={service_a.id}")

    assert response.status_code == 200
    returned_ids = {row["id"] for row in response.data["results"]}
    assert returned_ids == {specialist_1.id, specialist_2.id}


def test_filter_by_nonexistent_service_returns_empty(client, salon, specialist):
    response = client.get(_specialist_list_url(salon) + "?service=999999")

    assert response.status_code == 200
    assert response.data["results"] == []


def test_no_filter_returns_all(client, salon, specialist):
    with tenant_context(salon.id):
        other = Specialist.objects.create(salon=salon, name={"en": "Ann"})

    response = client.get(_specialist_list_url(salon))

    assert response.status_code == 200
    returned_ids = {row["id"] for row in response.data["results"]}
    assert returned_ids == {specialist.id, other.id}


def test_client_supplied_salon_in_body_is_ignored(client, salon, other_salon, admin_account):
    client.force_authenticate(user=admin_account)
    response = client.post(
        _specialist_list_url(salon),
        {"name": {"en": "New Specialist"}, "salon": other_salon.id},
        format="json",
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


# --- Stage 11.5: translatable name/bio read/write contract ----------------


def _make_specialist(salon, *, name, bio=None):
    with tenant_context(salon.id):
        return Specialist.objects.create(salon=salon, name=name, bio={} if bio is None else bio)


def test_specialist_name_and_bio_resolve_to_requested_language(client, salon):
    sp = _make_specialist(
        salon,
        name={"en": "Jane", "uk": "Джейн"},
        bio={"en": "Nail artist", "uk": "Майстриня манікюру"},
    )

    uk = client.get(_specialist_detail_url(salon, sp) + "?lang=uk")
    assert uk.status_code == 200
    assert uk.data["name"] == "Джейн"
    assert uk.data["bio"] == "Майстриня манікюру"

    en = client.get(_specialist_detail_url(salon, sp) + "?lang=en")
    assert en.data["name"] == "Jane"
    assert en.data["bio"] == "Nail artist"


def test_specialist_name_falls_back_to_english_without_lang_param(client, salon):
    sp = _make_specialist(salon, name={"en": "Jane", "uk": "Джейн"})

    response = client.get(_specialist_detail_url(salon, sp))
    assert response.status_code == 200
    assert response.data["name"] == "Jane"


def test_specialist_list_resolves_name_per_lang(client, salon):
    _make_specialist(salon, name={"en": "Jane", "uk": "Джейн"})

    response = client.get(_specialist_list_url(salon) + "?lang=uk")
    assert response.status_code == 200
    assert response.data["results"][0]["name"] == "Джейн"


def test_create_specialist_with_unsupported_language_key_returns_400(client, salon, admin_account):
    client.force_authenticate(user=admin_account)
    response = client.post(
        _specialist_list_url(salon),
        {"name": {"en": "Jane", "fr": "Jeanne"}},
        format="json",
    )

    assert response.status_code == 400
    assert "name" in response.data["error"]["details"]


def test_patch_specialist_with_unsupported_language_bio_key_returns_400(
    client, salon, admin_account, specialist
):
    client.force_authenticate(user=admin_account)
    response = client.patch(
        _specialist_detail_url(salon, specialist),
        {"bio": {"en": "Bio", "fr": "Biographie"}},
        format="json",
    )

    assert response.status_code == 400
    assert "bio" in response.data["error"]["details"]


def test_create_specialist_with_services_is_unaffected_by_the_split(
    client, salon, admin_account, service
):
    client.force_authenticate(user=admin_account)
    response = client.post(
        _specialist_list_url(salon),
        {"name": {"en": "Jane"}, "services": [service.id]},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["services"] == [service.id]


def test_posting_a_service_from_another_salon_returns_400(
    client, salon, other_salon, admin_account
):
    with tenant_context(other_salon.id):
        from catalog.models import Service, ServiceCategory

        cat = ServiceCategory.objects.create(salon=other_salon, name={"en": "Foreign"})
        foreign_service = Service.objects.create(
            salon=other_salon,
            category=cat,
            name={"en": "Foreign Service"},
            duration_minutes=30,
            price="100.00",
        )

    client.force_authenticate(user=admin_account)
    response = client.post(
        _specialist_list_url(salon),
        {"name": {"en": "Jane"}, "services": [foreign_service.id]},
        format="json",
    )

    assert response.status_code == 400
    assert "services" in response.data["error"]["details"]


# --- Stage 13 /specialists amendment: photo, nested services, ratings ------
#
# RED phase (docs/DECISIONS.md § Stage 13 amendment "/specialists page:
# serializer and card scope") — SpecialistReadSerializer/SpecialistListCreateView
# do not yet expose `photo`, nested `services`, `average_rating`, or
# `review_count`. These tests are written against the *decided* contract and
# are expected to fail against the current, unmodified serializer/view —
# either with a KeyError on a field that doesn't exist yet, a bare-id vs.
# nested-object mismatch, or (test 6) a query count that scales with the
# number of specialists because nothing is prefetched/annotated yet.

_review_slot_counter = 0


def _make_review(
    *, salon, specialist, customer, service, rating: int = 5, text: str = ""
) -> Review:
    """Create one COMPLETED appointment + its Review. Each call uses a fresh
    day-spaced slot so the Appointment exclusion constraint never trips
    (same pattern as test_review_list_endpoint.py's _make_review)."""
    global _review_slot_counter
    _review_slot_counter += 1
    start = timezone.now() + dt.timedelta(days=100 + _review_slot_counter)
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.COMPLETED,
    )
    with tenant_context(salon.id):
        return Review.objects.create(
            salon=salon,
            appointment=appointment,
            customer=customer,
            specialist=specialist,
            rating=rating,
            text=text,
        )


@pytest.fixture(autouse=True)
def _reset_review_slot_counter():
    global _review_slot_counter
    _review_slot_counter = 0
    yield


def _make_service(salon, service_category, *, name: str) -> Service:
    with tenant_context(salon.id):
        return Service.objects.create(
            salon=salon,
            category=service_category,
            name={"en": name},
            duration_minutes=30,
            price="300.00",
        )


def test_photo_field_exposed(client, salon, specialist):
    with tenant_context(salon.id):
        specialist.photo = "https://example.com/photos/jane.jpg"
        specialist.save(update_fields=["photo"])

    response = client.get(_specialist_detail_url(salon, specialist))

    assert response.status_code == 200
    assert response.data["photo"] == "https://example.com/photos/jane.jpg"


def test_services_nested_not_bare_ids(client, salon, service_category, specialist):
    service_a = _make_service(salon, service_category, name="Manicure")
    service_b = _make_service(salon, service_category, name="Pedicure")
    with tenant_context(salon.id):
        specialist.services.set([service_a, service_b], through_defaults={"salon_id": salon.id})

    response = client.get(_specialist_detail_url(salon, specialist))

    assert response.status_code == 200
    returned = {row["id"]: row["name"] for row in response.data["services"]}
    assert returned == {service_a.id: "Manicure", service_b.id: "Pedicure"}


def test_average_rating_and_review_count_correct(client, salon, specialist, customer, service):
    _make_review(salon=salon, specialist=specialist, customer=customer, service=service, rating=5)
    _make_review(salon=salon, specialist=specialist, customer=customer, service=service, rating=4)
    _make_review(salon=salon, specialist=specialist, customer=customer, service=service, rating=3)

    response = client.get(_specialist_detail_url(salon, specialist))

    assert response.status_code == 200
    assert response.data["average_rating"] == 4.0
    assert response.data["review_count"] == 3


def test_zero_reviews_specialist_shows_null_rating_and_appears_in_list(client, salon, specialist):
    response = client.get(_specialist_list_url(salon))

    assert response.status_code == 200
    returned_ids = [row["id"] for row in response.data["results"]]
    assert specialist.id in returned_ids
    row = next(row for row in response.data["results"] if row["id"] == specialist.id)
    assert row["average_rating"] is None
    assert row["review_count"] == 0


def test_no_row_multiplication_with_multiple_services_and_reviews(
    client, salon, service_category, specialist, customer, service
):
    service_a = _make_service(salon, service_category, name="Gel Polish")
    service_b = _make_service(salon, service_category, name="Pedicure")
    service_c = _make_service(salon, service_category, name="Brows")
    with tenant_context(salon.id):
        specialist.services.set(
            [service_a, service_b, service_c], through_defaults={"salon_id": salon.id}
        )

    _make_review(salon=salon, specialist=specialist, customer=customer, service=service, rating=5)
    _make_review(salon=salon, specialist=specialist, customer=customer, service=service, rating=4)
    _make_review(salon=salon, specialist=specialist, customer=customer, service=service, rating=3)

    response = client.get(_specialist_detail_url(salon, specialist))

    assert response.status_code == 200
    assert response.data["review_count"] == 3


def test_no_n_plus_one_queries(client, salon, service_category, customer, service):
    service_a = _make_service(salon, service_category, name="Gel Polish")
    service_b = _make_service(salon, service_category, name="Pedicure")

    with tenant_context(salon.id):
        specialists = [
            Specialist.objects.create(salon=salon, name={"en": f"Specialist {i}"})
            for i in range(20)
        ]
        for sp in specialists:
            sp.services.set([service_a, service_b], through_defaults={"salon_id": salon.id})

    for sp in specialists:
        _make_review(salon=salon, specialist=sp, customer=customer, service=service, rating=5)
        _make_review(salon=salon, specialist=sp, customer=customer, service=service, rating=3)

    with CaptureQueriesContext(connection) as ctx:
        response = client.get(_specialist_list_url(salon))

    assert response.status_code == 200
    assert len(response.data["results"]) == 20
    # A fixed, small number of queries regardless of how many specialists are
    # on the page (count + list + prefetch(services) + prefetch(reviews), not
    # N queries for N specialists' services/reviews).
    assert len(ctx.captured_queries) <= 10, (
        f"expected a fixed small query count, got {len(ctx.captured_queries)}: "
        + "\n".join(q["sql"] for q in ctx.captured_queries)
    )
