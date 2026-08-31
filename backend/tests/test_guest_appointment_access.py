import datetime as dt

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from booking.guest_tokens import issue_guest_token
from booking.models import AppointmentStatus, CancelledBy, GuestAccessToken
from core.tenancy import tenant_context
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture
def appointment(salon, customer, specialist, service):
    return make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=START
    )


@pytest.fixture
def token(salon, appointment):
    with tenant_context(salon.id):
        raw_token, _row = issue_guest_token(appointment)
    return raw_token


def _detail_url(salon, appointment) -> str:
    return f"/api/v1/salons/{salon.slug}/guest/appointments/{appointment.id}/"


def _cancel_url(salon, appointment) -> str:
    return f"/api/v1/salons/{salon.slug}/guest/appointments/{appointment.id}/cancel/"


# --- retrieve -----------------------------------------------------------------


def test_retrieve_with_a_valid_token_returns_the_appointment(client, salon, appointment, token):
    response = client.get(_detail_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)

    assert response.status_code == 200
    assert response.data["id"] == appointment.id
    assert response.data["status"] == AppointmentStatus.CONFIRMED


def test_retrieve_with_no_token_is_rejected(client, salon, appointment):
    response = client.get(_detail_url(salon, appointment))

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"


def test_retrieve_with_a_tampered_signature_is_rejected(client, salon, appointment, token):
    response = client.get(_detail_url(salon, appointment), HTTP_X_GUEST_TOKEN=token + "x")

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"


def test_retrieve_with_a_token_for_a_different_appointment_is_rejected(
    client, salon, customer, specialist, service, appointment, token
):
    other_appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(days=1),
    )

    response = client.get(_detail_url(salon, other_appointment), HTTP_X_GUEST_TOKEN=token)

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"


def test_retrieve_with_an_expired_token_is_rejected(client, salon, appointment, token):
    with tenant_context(salon.id):
        GuestAccessToken.objects.filter(appointment=appointment).update(
            expires_at=timezone.now() - dt.timedelta(days=1)
        )

    response = client.get(_detail_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_or_expired_token"


def test_all_guest_token_failure_modes_produce_an_identical_response(
    client, salon, customer, specialist, service, appointment, token
):
    """
    docs/DECISIONS.md § Stage 3 sub-step 3 decisions: every guest-token
    failure mode collapses into the same response, so none is
    distinguishable to the caller.
    """
    other_appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(days=1),
    )
    with tenant_context(salon.id):
        GuestAccessToken.objects.filter(appointment=appointment).update(
            expires_at=timezone.now() - dt.timedelta(days=1)
        )

    missing = client.get(_detail_url(salon, appointment))
    tampered = client.get(_detail_url(salon, appointment), HTTP_X_GUEST_TOKEN=token + "x")
    wrong_appointment = client.get(_detail_url(salon, other_appointment), HTTP_X_GUEST_TOKEN=token)
    expired = client.get(_detail_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)

    responses = [missing, tampered, wrong_appointment, expired]
    assert all(r.status_code == 400 for r in responses)
    assert all(r.data == missing.data for r in responses)


# --- cancel ---------------------------------------------------------------


def test_cancel_transitions_status_and_records_cancelled_by_guest(
    client, salon, appointment, token
):
    response = client.post(_cancel_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)

    assert response.status_code == 200
    appointment.refresh_from_db()
    assert appointment.status == AppointmentStatus.CANCELLED
    assert appointment.cancelled_by == CancelledBy.GUEST
    assert appointment.cancelled_at is not None


def test_cancelling_twice_rejects_the_second_attempt_but_retrieve_still_works(
    client, salon, appointment, token
):
    first = client.post(_cancel_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)
    assert first.status_code == 200

    second = client.post(_cancel_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)
    assert second.status_code == 400
    assert second.data["error"]["code"] == "invalid_or_expired_token"

    # Cancelling must not revoke view access (docs/DECISIONS.md § Stage 3 decisions).
    retrieve = client.get(_detail_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)
    assert retrieve.status_code == 200
    assert retrieve.data["status"] == AppointmentStatus.CANCELLED


def test_cancel_is_rejected_for_an_appointment_not_in_an_active_status(
    salon, customer, specialist, service, client
):
    completed = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.COMPLETED,
    )
    with tenant_context(salon.id):
        raw_token, _row = issue_guest_token(completed)

    response = client.post(_cancel_url(salon, completed), HTTP_X_GUEST_TOKEN=raw_token)

    assert response.status_code == 409
    assert response.data["error"]["code"] == "invalid_state_transition"


def test_cancel_updates_both_the_appointment_and_the_token_row(client, salon, appointment, token):
    """
    Stage 7.E: the guest cancel view is rewritten to call
    booking.services.cancel_appointment, which knows nothing about
    GuestAccessToken. The token row's cancelled_via_token_at update is the
    thing most at risk of being dropped when the inline logic moves into the
    service — proving both effects land guards the rewrite.
    """
    response = client.post(_cancel_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)

    assert response.status_code == 200
    with tenant_context(salon.id):
        appointment.refresh_from_db()
        token_row = GuestAccessToken.objects.get(appointment=appointment)
    assert appointment.status == AppointmentStatus.CANCELLED
    assert token_row.cancelled_via_token_at is not None


def test_cancel_of_an_already_cancelled_appointment_surfaces_the_services_exception(
    client, salon, customer, specialist, service
):
    """
    Appointment cancelled through some other path (not this token) before
    this guest ever tries to cancel — so token validation (for_cancel=True,
    gated on *this token's own* cancelled_via_token_at) succeeds, and the
    request reaches booking.services.cancel_appointment, which must reject
    it. Proves InvalidStateTransitionError propagates through the view via
    core.exceptions.exception_handler with no view-level try/except, and
    that .details reaches the response body.
    """
    already_cancelled = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CANCELLED,
    )
    with tenant_context(salon.id):
        raw_token, _row = issue_guest_token(already_cancelled)

    response = client.post(_cancel_url(salon, already_cancelled), HTTP_X_GUEST_TOKEN=raw_token)

    assert response.status_code == 409
    assert response.data["error"]["code"] == "invalid_state_transition"
    assert response.data["error"]["details"] == {"current_status": AppointmentStatus.CANCELLED}


# --- guest token has no power elsewhere ------------------------------------


def test_guest_token_is_rejected_on_a_non_guest_endpoint(client, salon, token):
    response = client.post(
        f"/api/v1/salons/{salon.slug}/auth/logout/",
        {"refresh": "irrelevant"},
        HTTP_X_GUEST_TOKEN=token,
    )

    assert response.status_code == 401


def test_guest_token_endpoint_is_throttled_after_the_configured_rate(
    client, salon, appointment, token
):
    for _ in range(20):  # guest_token: 20/min (docs/DECISIONS.md § Stage 3 decisions)
        response = client.get(_detail_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)
        assert response.status_code == 200

    response = client.get(_detail_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)
    assert response.status_code == 429
