"""
Guards against the DRF "object-level permissions never fire on list views"
trap recorded in CLAUDE.md. Three layers, each tested here:

1. HasValidGuestToken.has_permission fails closed if a view forgets to (or
   incorrectly) declares guest_token_action — instead of silently defaulting
   to "view" mode.
2. booking/views.py's _GuestTokenAppointmentMixin resolves the target object
   from the token, not the URL, so a URL/token appointment mismatch can't
   reach the wrong object even if check_object_permissions is bypassed.
3. A backstop: every view that lists HasValidGuestToken must be a DRF
   generic, whose get_object() guarantees check_object_permissions() is
   actually called.
"""

import datetime as dt

import pytest
from django.utils import timezone
from rest_framework import generics
from rest_framework.test import APIRequestFactory

from booking.guest_tokens import issue_guest_token
from booking.urls import urlpatterns as booking_urlpatterns
from booking.views import _GuestTokenAppointmentMixin
from core.permissions import HasValidGuestToken
from core.tenancy import tenant_context
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

factory = APIRequestFactory()


class _View:
    guest_token_action: str | None = "view"


def test_has_permission_denies_when_guest_token_action_is_not_declared():
    request = factory.get("/")
    view = _View()
    view.guest_token_action = None  # simulates a view that never set it

    assert HasValidGuestToken().has_permission(request, view) is False


def test_has_permission_denies_an_invalid_guest_token_action_value():
    request = factory.get("/")
    view = _View()
    view.guest_token_action = "list"  # not "view" or "cancel"

    assert HasValidGuestToken().has_permission(request, view) is False


def test_has_permission_allows_and_stashes_the_token_for_a_valid_action(
    monkeypatch, salon, customer, specialist, service
):
    with tenant_context(salon.id):
        appointment = make_appointment(
            salon=salon,
            customer=customer,
            specialist=specialist,
            service=service,
            start=dt.datetime(2026, 11, 1, 10, 0, tzinfo=dt.UTC),
        )
        raw_token, token_row = issue_guest_token(appointment)

    # validate_guest_token checks expires_at (appointment end + 30 days,
    # booking/guest_tokens.py) against the real timezone.now(); freeze now to
    # the appointment start so the token stays valid regardless of the date.
    monkeypatch.setattr(timezone, "now", lambda: appointment.start_datetime)

    request = factory.get("/", HTTP_X_GUEST_TOKEN=raw_token)
    view = _View()
    view.guest_token_action = "view"

    with tenant_context(salon.id):
        assert HasValidGuestToken().has_permission(request, view) is True
    assert request.guest_access_token.id == token_row.id  # type: ignore[attr-defined]


class _FakeMixinView(_GuestTokenAppointmentMixin):
    def __init__(self, request, kwargs):
        self.request = request
        self.kwargs = kwargs

    def check_object_permissions(self, request, obj):
        pass  # the thing under test is get_object(), not this hook


def test_get_object_resolves_from_the_token_and_rejects_a_url_mismatch(
    salon, customer, specialist, service
):
    """
    Simulates a view where check_object_permissions is a no-op (as if that
    hook were never wired up) — get_object() must still refuse to hand back
    a different appointment than the one the token was issued for, purely
    from comparing ids before any object is fetched.
    """
    with tenant_context(salon.id):
        real_appointment = make_appointment(
            salon=salon,
            customer=customer,
            specialist=specialist,
            service=service,
            start=dt.datetime(2026, 11, 2, 10, 0, tzinfo=dt.UTC),
        )
        other_appointment = make_appointment(
            salon=salon,
            customer=customer,
            specialist=specialist,
            service=service,
            start=dt.datetime(2026, 11, 3, 10, 0, tzinfo=dt.UTC),
        )
        _raw_token, token_row = issue_guest_token(real_appointment)

    request = factory.get("/")
    request.guest_access_token = token_row  # simulates has_permission already ran
    view = _FakeMixinView(request, {"appointment_id": other_appointment.id})

    with tenant_context(salon.id), pytest.raises(Exception) as exc_info:
        view.get_object()
    assert exc_info.value.__class__.__name__ == "InvalidOrExpiredTokenError"


def test_get_object_returns_the_tokens_own_appointment_when_url_matches(
    salon, customer, specialist, service
):
    with tenant_context(salon.id):
        appointment = make_appointment(
            salon=salon,
            customer=customer,
            specialist=specialist,
            service=service,
            start=dt.datetime(2026, 11, 4, 10, 0, tzinfo=dt.UTC),
        )
        _raw_token, token_row = issue_guest_token(appointment)

    request = factory.get("/")
    request.guest_access_token = token_row
    view = _FakeMixinView(request, {"appointment_id": appointment.id})

    with tenant_context(salon.id):
        resolved = view.get_object()
    assert resolved.id == appointment.id


def test_every_view_using_has_valid_guest_token_is_a_drf_generic():
    checked = 0
    for pattern in booking_urlpatterns:
        view_cls = pattern.callback.cls  # type: ignore[attr-defined]
        if HasValidGuestToken in getattr(view_cls, "permission_classes", []):
            checked += 1
            assert issubclass(view_cls, generics.GenericAPIView), (
                f"{view_cls.__name__} uses HasValidGuestToken but isn't a DRF "
                "generic — check_object_permissions() won't run automatically."
            )
    assert checked == 3  # detail, cancel, and pay — guards against a silent no-op
