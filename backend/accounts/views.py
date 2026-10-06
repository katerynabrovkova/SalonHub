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

Login, refresh and logout opt out of the project-wide IsAuthenticated default
with AllowAny. Logout also opts out of authentication: it never needs a valid
access token. It always clears all session cookies and answers 205, and it
blacklists the refresh token when one is present and valid (an invalid,
expired or already blacklisted one is ignored). It stays CSRF protected via
`enforce_csrf_on_unsafe`. See docs/DECISIONS.md, Session renewal and session
lifetime, decided 21.09.2026.

Stage 12 (docs/DECISIONS.md § Stage 12) moved the session onto two httpOnly
cookies set by these views: `LoginView`/`RefreshView` write an `access_token`
cookie (`Path=/`) and a `refresh_token` cookie (scoped to this salon's
`auth/refresh/` path) and return an empty JSON body; `RefreshView` and
`LogoutView` read the refresh token from its cookie, not the request body;
`LogoutView` also clears them (and the `session_hint` cookie). Cookie mechanics
live in `accounts/cookies.py`. Unsafe cookie-authenticated requests are CSRF-checked
via Django's engine — `AccountJWTCookieAuthentication` does this for
Account-authenticated writes; the three AllowAny cookie-driven views here call
`accounts.csrf.enforce_csrf_on_unsafe` themselves (the auth class never runs
for them). `AuthCsrfView` (`GET auth/csrf/`) primes the `csrftoken` cookie.
"""

import datetime as dt

from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import F
from django.middleware.csrf import get_token as get_csrf_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework.exceptions import NotAuthenticated, NotFound
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.cookies import REFRESH_COOKIE, clear_auth_cookies, set_auth_cookies
from accounts.csrf import enforce_csrf_on_unsafe
from accounts.models import Account, Customer
from accounts.serializers import (
    AccountTokenObtainPairSerializer,
    AccountTokenRefreshSerializer,
    MeCustomerUpdateSerializer,
    MeEmailChangeSerializer,
    MePasswordChangeSerializer,
    MeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    ResendVerificationSerializer,
)
from accounts.services import is_email_taken
from accounts.tasks import (
    send_email_change_confirmation_email,
    send_email_change_notice_email,
    send_email_change_unavailable_email,
    send_password_changed_email,
    send_password_reset_email,
    send_verification_email,
)
from accounts.tokens import (
    generate_account_verification_token,
    generate_email_change_token,
    read_account_verification_token,
    read_email_change_token,
)
from core.exceptions import (
    EmailUnavailableError,
    InvalidOrExpiredTokenError,
    InvalidPasswordError,
)
from core.i18n import resolve_translation
from core.tenancy import get_current_salon_id
from core.urls import build_salon_frontend_url
from tenants.models import Salon


class PublicEndpointMixin:
    """
    Opts a public (AllowAny) view out of authentication entirely.

    The ``access_token`` cookie is a session cookie (no ``max_age``), so it
    outlives the 15-minute token inside it and a browser keeps sending a stale
    one. DRF runs authentication before permissions, so with the project-default
    classes a stale or garbage cookie raises ``InvalidToken`` (401
    ``token_not_valid``) before ``AllowAny`` is ever consulted. A public
    endpoint has no use for the caller's identity, so it must not authenticate
    at all. List this mixin BEFORE ``APIView`` in the bases, or the default
    ``authentication_classes`` silently wins.
    """

    authentication_classes = ()


def _token_from_body(request: Request) -> str:
    """
    The ``token`` of a token endpoint's body (docs/DECISIONS.md § "Malformed
    body on token endpoints (verify-email, email-change confirm)"). A body
    that is not an object, or a token that is not a string, yields ``""`` so
    it follows the empty-token path to ``InvalidOrExpiredTokenError`` instead
    of crashing into a 500. A missing token stays ``""`` as before.
    """
    data = request.data
    if not isinstance(data, dict):
        return ""
    token = data.get("token", "")
    return token if isinstance(token, str) else ""


class RegisterView(PublicEndpointMixin, APIView):
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


class VerifyEmailView(PublicEndpointMixin, APIView):
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
            account_id = read_account_verification_token(_token_from_body(request))
        except (signing.SignatureExpired, signing.BadSignature) as exc:
            raise InvalidOrExpiredTokenError() from exc
        _verify_and_link(account_id=account_id, salon=salon, now=now)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetRequestView(PublicEndpointMixin, APIView):
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


class PasswordResetConfirmView(PublicEndpointMixin, APIView):
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
            # Ends every session of the account (docs/DECISIONS.md § "Item 9
            # decisions (change password): ending sessions").
            Account.objects.filter(pk=account.pk).update(session_version=F("session_version") + 1)

        return Response(status=status.HTTP_204_NO_CONTENT)


class ResendVerificationView(PublicEndpointMixin, APIView):
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


class AuthCsrfView(PublicEndpointMixin, APIView):
    """
    ``GET /api/v1/salons/<slug>/auth/csrf/`` — primes the ``csrftoken`` cookie
    (docs/DECISIONS.md § Stage 12). ``get_csrf_token(request)`` forces
    ``CsrfViewMiddleware`` to write the cookie onto the response. Public
    (AllowAny); the frontend calls this once before rendering the login form.
    """

    permission_classes = [AllowAny]

    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        get_csrf_token(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


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
        # AllowAny + cookie-driven, so AccountJWTCookieAuthentication never runs
        # its CSRF guard for this view — enforce it here. login-CSRF is in
        # scope (docs/DECISIONS.md § Stage 12).
        enforce_csrf_on_unsafe(request)
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
        # See LoginView.post: AllowAny + cookie-driven, CSRF enforced here.
        enforce_csrf_on_unsafe(request)
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


class MeView(APIView):
    """
    ``GET auth/me/`` — the current Account's `email` + `role` + linked
    `Customer`'s `name`/`phone` (docs/DECISIONS.md § "`/me/` endpoint
    (Stage 12)", widened in § Stage 15 planning, item 7). No explicit
    `authentication_classes`/`permission_classes` override: relies on the
    project-wide defaults (`AccountJWTCookieAuthentication` +
    `IsAuthenticated`), same posture as `LogoutView` — the cookie-carried
    access token is all that's needed, and DEFAULT_PERMISSION_CLASSES
    already rejects an unauthenticated request with 401.

    `MeSerializer.get_name`/`get_phone` read `request.user.customer`, a
    forward FK access that costs one extra query when accessed (not
    prefetched here with `select_related`) -- deliberately not optimized:
    this is a single-object endpoint (one Account per request, never a
    list), so there's no N+1 to guard against, unlike
    `AccountAppointmentListView`'s `select_related("payment", "specialist",
    "service")`, which exists specifically because that view serializes
    many rows per request.
    """

    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        serializer = MeSerializer(request.user)
        return Response(serializer.data)


class MeCustomerView(APIView):
    """
    ``PATCH auth/me/customer/``: edit the linked Customer's name/phone
    (docs/DECISIONS.md § "Item 10 design details (edit name and phone)").
    Project-default authentication/permissions, so CSRF stays enforced by
    `AccountJWTCookieAuthentication`.

    The Customer comes only from `request.user.customer_id`, never from the
    URL or body. No linked Customer raises the same bare `NotFound()` as
    `booking.views.AccountAppointmentCancelView`/`AccountAppointmentPayView`,
    so the 404 body is identical to theirs (`get_object_or_404`'s `Http404`
    renders a different body). The lookup goes through the tenant-scoped
    `Customer.objects`, so a linked Customer in another salon is a 404 too.
    """

    def patch(self, request: Request, *args: object, **kwargs: object) -> Response:
        customer_id = request.user.customer_id  # type: ignore[union-attr]
        customer = (
            Customer.objects.filter(pk=customer_id).first() if customer_id is not None else None
        )
        if customer is None:
            raise NotFound()

        serializer = MeCustomerUpdateSerializer(customer, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class MeEmailChangeView(APIView):
    """
    ``POST auth/me/email-change/``: request an email change
    (docs/DECISIONS.md § "Item 8 decisions (change email)"). Same
    authentication/permissions as `MeCustomerView` (project defaults, CSRF
    enforced by `AccountJWTCookieAuthentication`); throttled per account at
    the ``email_change`` scope, so wrong-password attempts count too.

    A wrong password or the current email is a coded 400 from the
    serializer, before any lookup or mail. After that the response is always
    the same empty 202: a free address gets the confirmation link, a taken
    one (`accounts.services.is_email_taken`) a letter without a link, so
    only the owner of the new mailbox learns which. The notice to the
    current address is sent in both cases. Nothing in the database changes
    here; the new address lives only in the token.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email_change"

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        account: Account = request.user  # type: ignore[assignment]
        serializer = MeEmailChangeSerializer(data=request.data, context={"account": account})
        serializer.is_valid(raise_exception=True)
        new_email = serializer.validated_data["new_email"]

        salon = get_object_or_404(Salon, pk=get_current_salon_id())
        salon_name = resolve_translation(salon.name, "uk")

        if is_email_taken(account=account, email=new_email):
            send_email_change_unavailable_email.delay(new_email, salon_name)
        else:
            token = generate_email_change_token(account, new_email)
            link = build_salon_frontend_url(salon.slug, "/confirm-email-change") + f"#token={token}"
            send_email_change_confirmation_email.delay(new_email, link, salon_name)
        send_email_change_notice_email.delay(account.email, new_email, salon_name)

        return Response(status=status.HTTP_202_ACCEPTED)


class MePasswordChangeView(APIView):
    """
    ``POST auth/me/password-change/`` (docs/DECISIONS.md § "Item 9 decisions
    (change password): ending sessions", "Endpoint and page, decided
    06.10.2026"). Same authentication/permissions as `MeEmailChangeView`
    (project defaults, CSRF enforced by `AccountJWTCookieAuthentication`);
    throttled per account at the ``password_change`` scope, every request
    counted.

    The serializer checks the current password, the same-password case and
    the strength, in that order. The change itself runs in one transaction
    on the locked Account row, re-checking the current password there:
    `set_password`, `save` and the `session_version` increment, which ends
    every session of the account, this one included. The notice email is
    queued with `transaction.on_commit`, so a rolled-back change sends
    nothing. Success is an empty 204 that also clears the session cookies.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_change"

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        account: Account = request.user  # type: ignore[assignment]
        serializer = MePasswordChangeSerializer(data=request.data, context={"account": account})
        serializer.is_valid(raise_exception=True)

        salon = get_object_or_404(Salon, pk=get_current_salon_id())
        salon_name = resolve_translation(salon.name, "uk")

        with transaction.atomic():
            locked = Account.objects.select_for_update().get(pk=account.pk)
            if not locked.check_password(serializer.validated_data["current_password"]):
                raise InvalidPasswordError()
            locked.set_password(serializer.validated_data["new_password"])
            locked.save(update_fields=["password"])
            Account.objects.filter(pk=locked.pk).update(session_version=F("session_version") + 1)
            recipient = locked.email
            transaction.on_commit(lambda: send_password_changed_email.delay(recipient, salon_name))

        response = Response(status=status.HTTP_204_NO_CONTENT)
        clear_auth_cookies(response, salon_slug=str(kwargs["slug"]))
        return response


class EmailChangeConfirmView(PublicEndpointMixin, APIView):
    """
    ``POST auth/email-change/confirm/`` (body: ``token``): confirm an email
    change from the emailed link (docs/DECISIONS.md § "Item 8 decisions
    (change email)", including "Cycle 2 additions"). Public, like
    `VerifyEmailView`, so a stale access cookie cannot turn it into a 401.

    One transaction with the Account row locked: every token failure
    collapses to `InvalidOrExpiredTokenError`; the address is re-checked
    with `is_email_taken`; then `Account.email`, `email_verified_at` and the
    linked Customer's email change together. The saves run in an inner
    atomic block, so a unique-constraint race at save becomes
    `EmailUnavailableError` with nothing changed. Sessions are not ended.
    """

    permission_classes = [AllowAny]

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        # One clock read per request, same discipline as VerifyEmailView.
        now = timezone.now()
        with transaction.atomic():
            try:
                account, new_email = read_email_change_token(_token_from_body(request))
            except (signing.SignatureExpired, signing.BadSignature) as exc:
                raise InvalidOrExpiredTokenError() from exc

            if is_email_taken(account=account, email=new_email):
                raise EmailUnavailableError()

            try:
                with transaction.atomic():
                    account.email = new_email
                    account.email_verified_at = now
                    account.save(update_fields=["email", "email_verified_at", "updated_at"])
                    if account.customer_id is not None:
                        customer = Customer.objects.get(pk=account.customer_id)
                        customer.email = new_email
                        customer.save(update_fields=["email", "updated_at"])
            except IntegrityError as exc:
                raise EmailUnavailableError() from exc

        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutView(APIView):
    """
    Always clears the session cookies and answers 205; blacklists the refresh
    token when one is present and valid (docs/DECISIONS.md, Session renewal
    and session lifetime, decided 21.09.2026, decision 3). Authentication is
    off, so an expired access token cannot block logout; the view-level CSRF
    check below is therefore the only CSRF guard and must stay first.
    """

    authentication_classes = ()
    permission_classes = [AllowAny]

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        enforce_csrf_on_unsafe(request)
        raw_refresh = request.COOKIES.get(REFRESH_COOKIE)
        if raw_refresh:
            try:
                RefreshToken(raw_refresh).blacklist()
            except TokenError:
                # Invalid, expired or already blacklisted: nothing to revoke,
                # and the caller learns nothing about the token.
                pass
        response = Response(status=status.HTTP_205_RESET_CONTENT)
        clear_auth_cookies(response, salon_slug=str(kwargs["slug"]))
        return response
