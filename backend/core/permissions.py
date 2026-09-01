"""
DRF permission classes for roles, tenant-scoped staff access, customer
ownership, and guest-token access (docs/ARCHITECTURE.md § 4). These are a
second line of defense on writes — the tenant-scoped manager (core.models)
is the first line, so a missing permission check fails toward "wrong data
invisible," not "wrong data mutable."
"""

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from accounts.models import Account, AccountRole
from booking.guest_tokens import validate_guest_token
from booking.models import Appointment
from core.tenancy import get_current_salon_id


def IsSalonStaff(*roles: str) -> type[BasePermission]:
    """
    Factory: `permission_classes = [IsSalonStaff()]` for any staff role, or
    `[IsSalonStaff(AccountRole.ADMIN)]` to restrict to specific roles.

    Checks the authenticated principal is an `Account` carrying a staff role
    for the currently bound tenant (docs/ARCHITECTURE.md § 4, docs/DECISIONS.md
    § Stage 3-R decisions). Empty `roles` means "any staff role" — for v1 that
    set is exactly `{admin}`, NOT "any Account role": `client` is a role now
    and a client is not staff.

    The `isinstance(..., Account)` check is load-bearing, not a type nicety:
    it is the token-type guard required before any pk lookup, so an
    `Account` token's `user_id` can never be resolved against an unrelated
    `User` row with the same integer pk (docs/DECISIONS.md § Stage 3-R
    decisions, "USER_ID_FIELD / cross-model token collision"). Until 3-R.E
    wires Account-based JWT auth, `request.user` is always a `User` in
    production, so every real request is denied here — an agreed, expected
    interim consequence (same decisions entry).
    """

    class _IsSalonStaff(BasePermission):
        def has_permission(self, request: Request, view: APIView) -> bool:
            account = request.user
            if not (account and account.is_authenticated):
                return False
            if not isinstance(account, Account):
                return False
            salon_id = get_current_salon_id()
            if salon_id is None:
                return False
            target_roles = roles or (AccountRole.ADMIN,)
            return Account.objects.filter(pk=account.pk, role__in=target_roles).exists()

    return _IsSalonStaff


class IsAuthenticatedCustomer(BasePermission):
    """
    request.user is an authenticated Account with a linked Customer row
    (Account.customer) in the currently bound tenant (docs/DECISIONS.md §
    Stage 3-R decisions, "Account model, settled shape"). The
    isinstance(request.user, Account) check is the same token-type guard
    as IsSalonStaff's, above — required before trusting any attribute off
    request.user, so no unrelated authenticated principal (including a
    still-valid legacy User-issued token) is mistaken for an Account.
    docs/ARCHITECTURE.md § 4's "Customer" role definition still describes
    an authenticated User and is stale pending its rewrite.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not (request.user and request.user.is_authenticated):
            return False
        if not isinstance(request.user, Account):
            return False
        salon_id = get_current_salon_id()
        if salon_id is None:
            return False
        return request.user.customer_id is not None


class IsOwnCustomer(BasePermission):
    """
    Object-level: the acting Customer matches obj.customer_id. "Acting
    Customer" comes from either a validated guest token, or an
    authenticated Account's linked Customer row (Account.customer) —
    relies on HasValidGuestToken having already run and attached
    request.guest_access_token when acting as a guest.
    docs/ARCHITECTURE.md § 4's "Customer" role definition still describes
    an authenticated User and is stale pending its rewrite.
    """

    def has_object_permission(self, request: Request, view: APIView, obj: object) -> bool:
        customer_id = self._acting_customer_id(request)
        if customer_id is None:
            return False
        return getattr(obj, "customer_id", None) == customer_id

    @staticmethod
    def _acting_customer_id(request: Request) -> int | None:
        guest_token = getattr(request, "guest_access_token", None)
        if guest_token is not None:
            return guest_token.appointment.customer_id
        if request.user and request.user.is_authenticated:
            if not isinstance(request.user, Account):
                return None
            salon_id = get_current_salon_id()
            if salon_id is None:
                return None
            return request.user.customer_id
        return None


class HasValidGuestToken(BasePermission):
    """
    Validates the X-Guest-Token header (docs/ARCHITECTURE.md § 3, § 4). See
    CLAUDE.md's "DRF object-level permissions" rule for why this class does
    its real work in has_permission rather than has_object_permission: the
    latter is only invoked by views that explicitly call
    check_object_permissions() (DRF generics do this automatically inside
    get_object(); a plain APIView must call it itself) — a permission class
    that only checks the object is a silent no-op on any view shape that
    never reaches that call, e.g. a hypothetical list endpoint.

    Every view listing this permission must declare `guest_token_action` as
    exactly "view", "cancel", or "pay", with no default — has_permission
    denies if it's missing or invalid, so a view that forgets to configure
    this fails closed instead of silently defaulting to "view" mode. Only
    "cancel" treats an already-spent cancel capability as invalid
    (docs/DECISIONS.md § Stage 3 decisions: cancelling must not revoke view
    access). "pay" uses for_cancel=False like "view" — paying does not spend
    the cancel capability, and payability is already gated by
    initiate_payment's own PENDING_PAYMENT check, not by token state
    (docs/DECISIONS.md § Stage 8.D decisions).

    On success, stashes the validated GuestAccessToken on
    request.guest_access_token — note its `appointment_id` is the
    authoritative target appointment; views must resolve the object *from*
    this, never validate this *against* a separately-sourced (e.g.
    URL-supplied) id (see booking/views.py's _GuestTokenAppointmentMixin).
    has_object_permission is a second, redundant confirmation once the
    object is fetched — defense in depth, not the only check.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        action = getattr(view, "guest_token_action", None)
        if action not in ("view", "cancel", "pay"):
            return False
        raw_token = request.headers.get("X-Guest-Token", "")
        token_row = validate_guest_token(raw_token, for_cancel=(action == "cancel"))
        request.guest_access_token = token_row  # type: ignore[attr-defined]
        return True

    def has_object_permission(self, request: Request, view: APIView, obj: Appointment) -> bool:
        token_row = getattr(request, "guest_access_token", None)
        return token_row is not None and token_row.appointment_id == obj.id
