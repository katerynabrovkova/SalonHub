"""
Auth views (docs/ARCHITECTURE.md § 3, § 13).

`LoginView`/`RefreshView`/`LogoutView` are wired under `/api/v1/auth/`
(accounts/urls.py) and still authenticate against `AUTH_USER_MODEL`; they
move under `/api/v1/salons/<slug>/auth/` against `Account` in 3-R.E. The
`User`-based registration, email-verification and password-reset endpoints
were removed in Stage 3-R.D.2 (docs/DECISIONS.md).

`RegisterView` (Stage 3-R.D.3) is the first `Account`-based replacement: it
is wired under the salon prefix (accounts/salon_urls.py,
`/api/v1/salons/<slug>/auth/register/`), reads its salon from the bound
tenant context exactly as `booking.views.GuestBookingCreateView` does, and
creates a `role=client` unverified `Account`. Email-verification and
password-reset follow in 3-R.D.4-D.5.

Login and refresh opt out of the project-wide IsAuthenticated default with
AllowAny; logout relies on that default deliberately — it needs a valid
access token to blacklist a refresh token (docs/DECISIONS.md § Stage 3
decisions).
"""

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.models import Account
from accounts.serializers import LogoutSerializer, RegisterSerializer
from accounts.tasks import send_verification_email
from accounts.tokens import generate_account_verification_token
from core.exceptions import InvalidOrExpiredTokenError
from core.tenancy import get_current_salon_id
from tenants.models import Salon


class RegisterView(APIView):
    """
    Client self-registration (docs/DECISIONS.md § Stage 3-R.D.3). Public
    write (AllowAny), set explicitly — DEFAULT_PERMISSION_CLASSES is
    IsAuthenticated globally.

    No-enumeration on two levels: the tenant-scoped `Account.objects`
    manager can only see this salon's rows, and the response is an
    identical empty-body 202 whether the email is new or already registered
    here — a returning address gets no second row and no further email (the
    "you already have an account" notice waits for 3-R.E, when there is a
    login to point it at). The `account_salon_email_uniq` constraint plus
    core.exceptions.exception_handler's UniqueViolation branch is the
    check-then-create race backstop, not the normal-path duplicate handler.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Salon is the tenant root, not a TenantScopedModel — plain manager,
        # same as booking.views.GuestBookingCreateView.
        salon = get_object_or_404(Salon, pk=get_current_salon_id())
        email = serializer.validated_data["email"]

        if Account.objects.filter(email=email).first() is None:
            account = Account.objects.create_account(
                salon=salon,
                email=email,
                password=serializer.validated_data["password"],
            )
            token = generate_account_verification_token(account)
            send_verification_email.delay(account.email, token, salon.slug)

        return Response(status=status.HTTP_202_ACCEPTED)


class LoginView(TokenObtainPairView):
    # TokenViewBase (simplejwt) has no explicit annotation on
    # permission_classes, so django-stubs infers its type from `()` — the
    # empty-tuple literal type — and any non-empty override is flagged
    # regardless of list vs. tuple syntax. Known stub friction, not a real
    # type error.
    permission_classes = (AllowAny,)  # type: ignore[assignment]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class RefreshView(TokenRefreshView):
    permission_classes = (AllowAny,)  # type: ignore[assignment]


class LogoutView(APIView):
    def post(self, request: Request) -> Response:
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError as exc:
            raise InvalidOrExpiredTokenError("Invalid or already-used refresh token.") from exc
        return Response(status=status.HTTP_205_RESET_CONTENT)
