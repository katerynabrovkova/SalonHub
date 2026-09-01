"""
core/permissions.py (docs/ARCHITECTURE.md § 4). IsSalonStaff/
IsAuthenticatedCustomer/IsOwnCustomer have no production endpoint yet in
Stage 3 (staff/customer-facing views land in later stages) — exercised
directly here, plus one end-to-end HTTP test proving IsSalonStaff actually
denies cross-salon staff through DRF's real dispatch/exception-handling
path, not just via a unit-level True/False check.
"""

import datetime as dt

import pytest
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from accounts.models import Account, AccountRole, User
from booking.guest_tokens import issue_guest_token
from core.permissions import IsAuthenticatedCustomer, IsOwnCustomer, IsSalonStaff
from core.tenancy import tenant_context
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

factory = APIRequestFactory()


class _StaffOnlyProbeView(APIView):
    permission_classes = [IsSalonStaff()]

    def get(self, request):
        return Response({"ok": True})


class _NoPermissionClassProbeView(APIView):
    def get(self, request):
        return Response({"ok": True})


# --- IsSalonStaff (unit-level) ---------------------------------------------


def test_is_salon_staff_allows_staff_of_the_current_salon(salon, admin_account):
    request = factory.get("/")
    request.user = admin_account
    permission = IsSalonStaff()()

    with tenant_context(salon.id):
        assert permission.has_permission(request, _StaffOnlyProbeView()) is True


def test_is_salon_staff_denies_staff_of_a_different_salon(salon, other_salon, admin_account):
    request = factory.get("/")
    request.user = admin_account
    permission = IsSalonStaff()()

    with tenant_context(other_salon.id):
        assert permission.has_permission(request, _StaffOnlyProbeView()) is False


def test_is_salon_staff_denies_an_anonymous_request(salon):
    request = factory.get("/")
    request.user = None
    permission = IsSalonStaff()()

    with tenant_context(salon.id):
        assert permission.has_permission(request, _StaffOnlyProbeView()) is False


def test_is_salon_staff_restricted_to_a_role_excludes_other_roles(salon, admin_account):
    permission = IsSalonStaff("some_other_role")()
    request = factory.get("/")
    request.user = admin_account

    with tenant_context(salon.id):
        assert permission.has_permission(request, _StaffOnlyProbeView()) is False


def test_is_salon_staff_denies_a_user_principal(salon):
    """request.user is a platform `User`, not an `Account` (the pre-3-R.E
    state: JWT auth still issues User tokens). IsSalonStaff must reject it
    outright rather than fall through to a pk lookup — this is the
    USER_ID_FIELD cross-model collision mitigation recorded in
    docs/DECISIONS.md § Stage 3-R decisions, not just a type nicety."""
    user = User.objects.create_user(email="staff@example.com", password="a-strong-passw0rd!")
    request = factory.get("/")
    request.user = user
    permission = IsSalonStaff()()

    with tenant_context(salon.id):
        assert permission.has_permission(request, _StaffOnlyProbeView()) is False


def test_is_salon_staff_denies_an_account_without_a_staff_role(salon):
    """An Account with role=client is a registered login, not back-office
    access (docs/DECISIONS.md § Stage 3-R decisions)."""
    with tenant_context(salon.id):
        client_account = Account.objects.create_account(
            salon=salon,
            email="client@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
        )
    request = factory.get("/")
    request.user = client_account
    permission = IsSalonStaff()()

    with tenant_context(salon.id):
        assert permission.has_permission(request, _StaffOnlyProbeView()) is False


# --- IsAuthenticatedCustomer (unit-level) ----------------------------------


def test_is_authenticated_customer_true_for_a_users_own_customer_row(salon, customer):
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon,
            email="cust@example.com",
            password="a-strong-passw0rd!",
            customer=customer,
        )

    request = factory.get("/")
    request.user = account
    with tenant_context(salon.id):
        assert IsAuthenticatedCustomer().has_permission(request, APIView()) is True


def test_is_authenticated_customer_false_with_no_linked_customer_row(salon):
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon,
            email="nocust@example.com",
            password="a-strong-passw0rd!",
        )

    request = factory.get("/")
    request.user = account
    with tenant_context(salon.id):
        assert IsAuthenticatedCustomer().has_permission(request, APIView()) is False


# --- IsOwnCustomer (unit-level) --------------------------------------------


def test_is_own_customer_true_for_the_jwt_users_own_customer(salon, customer, specialist, service):
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon,
            email="owner@example.com",
            password="a-strong-passw0rd!",
            customer=customer,
        )
        appointment = make_appointment(
            salon=salon,
            customer=customer,
            specialist=specialist,
            service=service,
            start=dt.datetime(2026, 10, 1, 10, 0, tzinfo=dt.UTC),
        )

    request = factory.get("/")
    request.user = account
    with tenant_context(salon.id):
        assert IsOwnCustomer().has_object_permission(request, APIView(), appointment) is True


def test_is_own_customer_false_for_a_different_customers_appointment(
    salon, customer, specialist, service
):
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon,
            email="notowner@example.com",
            password="a-strong-passw0rd!",
        )
        appointment = make_appointment(
            salon=salon,
            customer=customer,
            specialist=specialist,
            service=service,
            start=dt.datetime(2026, 10, 1, 10, 0, tzinfo=dt.UTC),
        )

    request = factory.get("/")
    request.user = account
    with tenant_context(salon.id):
        assert IsOwnCustomer().has_object_permission(request, APIView(), appointment) is False


def test_is_own_customer_true_for_a_matching_guest_token(salon, customer, specialist, service):
    with tenant_context(salon.id):
        appointment = make_appointment(
            salon=salon,
            customer=customer,
            specialist=specialist,
            service=service,
            start=dt.datetime(2026, 10, 2, 10, 0, tzinfo=dt.UTC),
        )
        _raw_token, token_row = issue_guest_token(appointment)

    request = factory.get("/")
    request.user = None
    request.guest_access_token = token_row

    assert IsOwnCustomer().has_object_permission(request, APIView(), appointment) is True


# --- HTTP-level: fail-closed defaults --------------------------------------


def test_a_view_with_no_permission_class_still_requires_auth():
    """
    Fail-closed default (docs/DECISIONS.md § Stage 3 decisions,
    REST_FRAMEWORK.DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]): a view
    that never sets permission_classes must not be silently open.
    """
    request = factory.get("/")
    response = _NoPermissionClassProbeView.as_view()(request)

    assert response.status_code == 401


def test_staff_of_salon_a_gets_403_hitting_a_staff_only_view_bound_to_salon_b(
    salon, other_salon, admin_account
):
    """
    Same check as the unit-level test above, but through DRF's real
    dispatch/permission/exception-handling path end to end, not just a
    direct has_permission() call.
    """
    request = factory.get("/")
    force_authenticate(request, user=admin_account)

    with tenant_context(other_salon.id):
        response = _StaffOnlyProbeView.as_view()(request)

    assert response.status_code == 403
