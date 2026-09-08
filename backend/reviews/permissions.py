"""
Permission class for the Review write endpoint (docs/DECISIONS.md § Stage 11
Part 2).

A NEW class, deliberately not core.permissions.HasValidGuestToken: that one
is scoped to the retrieve-family guest actions (view/cancel/pay) on an
existing object and reads a required ``guest_token_action`` marker off the
view. Review-create has a different shape — dual identity (Account JWT *or*
guest token), Account taking strict priority — and a different split of
responsibilities: this class does the identity check only; ownership
(does the URL appointment belong to the caller) is resolved in the view,
and eligibility (appointment COMPLETED) in the serializer.

Per CLAUDE.md's "DRF object-level permissions" rule, the real check lives in
``has_permission``: an Account is validated by the existing
``accounts.authentication.AccountJWTAuthentication`` before we get here, and
the guest token is validated here against
``booking.guest_tokens.validate_guest_token``. There is no object-only
codepath that could silently pass.
"""

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from accounts.models import Account
from booking.guest_tokens import validate_guest_token

GUEST_TOKEN_HEADER = "X-Guest-Token"


class CanSubmitAppointmentReview(BasePermission):
    """
    Grants access when the request carries a usable identity:

    * a valid Account JWT whose Account has a linked Customer row
      (``Account.customer``) — this path wins outright, and the
      ``X-Guest-Token`` header is never read or validated; or
    * failing that, a valid ``X-Guest-Token`` for some appointment
      (appointment-scoped, same mechanism as the booking guest endpoints).
      The validated ``GuestAccessToken`` row is stashed on
      ``request.guest_access_token`` for the view to resolve ownership from.

    With neither, returns ``False`` — DRF then produces its standard
    unauthenticated response (401, via SimpleJWT's ``authenticate_header``).
    An invalid ``X-Guest-Token`` (bad signature, expired, unknown) raises
    ``InvalidOrExpiredTokenError`` (400) from ``validate_guest_token``, the
    same as the other guest endpoints.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        account = request.user
        if account and account.is_authenticated:
            # AccountJWTAuthentication only ever authenticates as Account;
            # the isinstance guard matches IsSalonStaff / IsAuthenticatedCustomer.
            if not isinstance(account, Account):
                return False
            return account.customer_id is not None

        raw_token = request.headers.get(GUEST_TOKEN_HEADER, "")
        if not raw_token:
            return False
        request.guest_access_token = validate_guest_token(raw_token)  # type: ignore[attr-defined]
        return True
