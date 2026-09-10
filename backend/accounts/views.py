"""
Auth views (docs/ARCHITECTURE.md § 3, § 13).

`LoginView`/`RefreshView`/`LogoutView` are wired under
`/api/v1/salons/<slug>/auth/` (accounts/salon_urls.py) and authenticate
against `Account`, tenant-scoped to the salon bound from the URL slug
(Stage 3-R.E, docs/DECISIONS.md). The flat `/api/v1/auth/` surface —
including the pre-E `User`-based login/refresh/logout — is gone: after E,
`User` no longer authenticates via JWT at all, only through the
session-based Django `/admin/`. The `User`-based registration,
email-verification and password-reset endpoints were removed earlier, in
Stage 3-R.D.2 (docs/DECISIONS.md).

`RegisterView` (Stage 3-R.D.3) is the first `Account`-based replacement: it
is wired under the salon prefix (accounts/salon_urls.py,
`/api/v1/salons/<slug>/auth/register/`), reads its salon from the bound
tenant context exactly as `booking.views.GuestBookingCreateView` does, and
creates a `role=client` unverified `Account`. `VerifyEmailView` (Stage
3-R.D.4) consumes the token that registration mails out; password-reset and
resend-verification follow in 3-R.D.5.

Login and refresh opt out of the project-wide IsAuthenticated default with
AllowAny; logout relies on that default deliberately — it needs a valid
access token to blacklist a refresh token (docs/DECISIONS.md § Stage 3
decisions).

Stage 12 (docs/DECISIONS.md § Stage 12) moved the session onto two httpOnly
cookies set by these views: `LoginView`/`RefreshView` write an `access_token`
cookie (`Path=/`) and a `refresh_token` cookie (scoped to this salon's
`auth/refresh/` path) and return an empty JSON body; `RefreshView` and
`LogoutView` read the refresh token from its cookie, not the request body;
`LogoutView` also clears both cookies. Cookie mechanics live in
`accounts/cookies.py`. CSRF protection for unsafe methods is deliberately not
implemented yet (separate deferred sub-step).
"""

import datetime as dt

from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework.exceptions import NotAuthenticated
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.cookies import REFRESH_COOKIE, clear_auth_cookies, set_auth_cookies
from accounts.models import Account, Customer
from accounts.serializers import (
    AccountTokenObtainPairSerializer,
    AccountTokenRefreshSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    ResendVerificationSerializer,
)
from accounts.tasks import send_password_reset_email, send_verification_email
from accounts.tokens import generate_account_verification_token, read_account_verification_token
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


def _verify_and_link(*, account_id: int, salon: Salon, now: dt.datetime) -> None:
    """
    Mark the account's email verified and, now that the address is proven,
    adopt a matching same-salon guest ``Customer`` (docs/DECISIONS.md
    § Stage 3-R.D.4). Module-private: the only caller is ``VerifyEmailView``
    — this is not a reusable service.

    The stamp is a filtered update, so verifying twice with the same token
    selects zero rows the second time and leaves the original timestamp
    intact. The link runs only when the account has no ``Customer`` yet and
    is matched on the verified email alone, never the phone. Both steps
    share one transaction.
    """
    with transaction.atomic():
        Account.objects.filter(pk=account_id, salon=salon, email_verified_at__isnull=True).update(
            email_verified_at=now
        )
        account = Account.objects.get(pk=account_id, salon=salon)
        if account.customer_id is None:
            match = Customer.objects.filter(salon=salon, email=account.email).first()
            if match is not None:
                account.customer = match
                account.save(update_fields=["customer"])


class VerifyEmailView(APIView):
    """
    Confirm a client's email from the token in the verification link
    (docs/DECISIONS.md § Stage 3-R.D.4). Public write (AllowAny) — the
    caller carries only the emailed token, no session.

    Every token failure — expired, tampered, minted for another salon, or
    naming an account that has since changed its email — collapses to one
    neutral ``InvalidOrExpiredTokenError`` (400), the same posture as
    ``booking.guest_tokens.validate_guest_token``. Success is
    ``204 No Content``. There is deliberately no GET handler: verification
    mutates state, so a prefetching proxy or a browser preload must get a
    405, not silently confirm the address.
    """

    permission_classes = [AllowAny]

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        # timezone.now() is I/O (the system clock): call it once here and
        # thread it down, the same discipline as create_guest_appointment
        # and RegisterView (docs/DECISIONS.md § Stage 3-R.D.4).
        now = timezone.now()
        salon = get_object_or_404(Salon, pk=get_current_salon_id())
        try:
            account_id = read_account_verification_token(request.data.get("token", ""))
        except (signing.SignatureExpired, signing.BadSignature) as exc:
            raise InvalidOrExpiredTokenError() from exc
        _verify_and_link(account_id=account_id, salon=salon, now=now)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetRequestView(APIView):
    """
    Start a password reset (docs/DECISIONS.md § Stage 3-R.D.5). Public write
    (AllowAny). No-enumeration: an identical empty ``202`` whether or not an
    ``Account`` with the submitted email exists in the bound salon — the
    reset email is enqueued only when one does, and the lookup runs through
    the tenant-scoped ``Account.objects`` so it can never observe another
    salon's rows. Throttled at the ``password_reset`` scope (a third-party
    email send on our bill, same rationale as ``register``).
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        salon = get_object_or_404(Salon, pk=get_current_salon_id())
        account = Account.objects.filter(email=serializer.validated_data["email"]).first()
        if account is not None:
            uid = urlsafe_base64_encode(force_bytes(account.pk))
            token = default_token_generator.make_token(account)
            send_password_reset_email.delay(account.email, uid, token, salon.slug)

        return Response(status=status.HTTP_202_ACCEPTED)


class PasswordResetConfirmView(APIView):
    """
    Complete a password reset from the ``uid`` + ``token`` in the emailed
    link (docs/DECISIONS.md § Stage 3-R.D.5). Public write (AllowAny), no
    throttle — the generator token is a keyed hash, not brute-forceable in
    the one-hour window.

    ``uid`` (the base64-encoded ``Account`` pk) is carried separately from
    ``token`` because Django's ``PasswordResetTokenGenerator`` does not
    embed the pk in its token, unlike D.4's ``TimestampSigner`` payload.
    Every uid/token failure — malformed base64, a pk naming no account in
    this salon, a tampered or expired token — collapses to one neutral
    ``InvalidOrExpiredTokenError`` (400), the same posture as
    ``VerifyEmailView``. A weak ``new_password`` is a separate field-level
    ``400`` raised by the serializer, never this collapse. Success is
    ``204 No Content`` — the account is not logged in here (login is 3-R.E).
    """

    permission_classes = [AllowAny]

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            account_pk = force_str(urlsafe_base64_decode(serializer.validated_data["uid"]))
            account = Account.objects.get(pk=account_pk)
        except (TypeError, ValueError, OverflowError, Account.DoesNotExist) as exc:
            raise InvalidOrExpiredTokenError() from exc

        if not default_token_generator.check_token(account, serializer.validated_data["token"]):
            raise InvalidOrExpiredTokenError()

        with transaction.atomic():
            account.set_password(serializer.validated_data["new_password"])
            account.save(update_fields=["password"])

        return Response(status=status.HTTP_204_NO_CONTENT)


class ResendVerificationView(APIView):
    """
    Re-send the email-verification link (docs/DECISIONS.md § Stage 3-R.D.5).
    Public write (AllowAny). Reuses D.3/D.4's token helper and Celery task
    unchanged. No-enumeration: an identical empty ``202`` regardless of
    outcome. A fresh token is minted and sent only when the account exists
    in the bound salon *and* is still unverified — an already-verified
    address is a deliberate silent no-op (no mail), both to deny joe-job
    inbox flooding and to keep verified-state from leaking. Throttled at the
    ``resend_verification`` scope.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "resend_verification"

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        serializer = ResendVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        salon = get_object_or_404(Salon, pk=get_current_salon_id())
        account = Account.objects.filter(email=serializer.validated_data["email"]).first()
        if account is not None and account.email_verified_at is None:
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
    serializer_class = AccountTokenObtainPairSerializer

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        # The session rides two httpOnly cookies, not the JSON body
        # (docs/DECISIONS.md § Stage 12). super().post() raises on bad
        # credentials before returning, so no cookie is ever set on a failure.
        response = super().post(request, *args, **kwargs)
        tokens = response.data
        set_auth_cookies(
            response,
            access=tokens["access"],
            refresh=tokens["refresh"],
            salon_slug=str(kwargs["slug"]),
        )
        response.data = {}
        return response


class RefreshView(TokenRefreshView):
    permission_classes = (AllowAny,)  # type: ignore[assignment]
    serializer_class = AccountTokenRefreshSerializer

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        # Refresh token comes from its own cookie, never the request body
        # (docs/DECISIONS.md § Stage 12).
        raw_refresh = request.COOKIES.get(REFRESH_COOKIE)
        if not raw_refresh:
            # Missing credential, not a malformed request — 401, consistent
            # with every other auth failure.
            raise NotAuthenticated("No refresh token cookie.")

        serializer = self.get_serializer(data={"refresh": raw_refresh})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0]) from exc

        data = serializer.validated_data
        response = Response(status=status.HTTP_200_OK)
        set_auth_cookies(
            response,
            access=data["access"],
            # ROTATE_REFRESH_TOKENS is on, so `data` always carries a fresh
            # refresh token; fall back to the presented one defensively.
            refresh=data.get("refresh", raw_refresh),
            salon_slug=str(kwargs["slug"]),
        )
        response.data = {}
        return response


class LogoutView(APIView):
    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        response = Response(status=status.HTTP_205_RESET_CONTENT)
        raw_refresh = request.COOKIES.get(REFRESH_COOKIE)
        try:
            if raw_refresh:
                RefreshToken(raw_refresh).blacklist()
        except TokenError as exc:
            clear_auth_cookies(response, salon_slug=str(kwargs["slug"]))
            raise InvalidOrExpiredTokenError("Invalid or already-used refresh token.") from exc
        clear_auth_cookies(response, salon_slug=str(kwargs["slug"]))
        return response
