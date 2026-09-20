"""
Booking endpoints: guest booking creation, and guest-token-authenticated
appointment access (docs/ARCHITECTURE.md § 3, § 4).

GuestAppointmentDetailView/GuestAppointmentCancelView are single-object: a
guest views or cancels exactly the appointment named in the URL,
authenticated only by the X-Guest-Token header, never JWT. Both are DRF
generics, not plain APIView, specifically so check_object_permissions() is
guaranteed to run (DRF generics call it automatically inside get_object())
— see CLAUDE.md's "DRF object-level permissions" rule and
core/permissions.py's HasValidGuestToken docstring. Refund triggering on
cancellation (eligibility + calling payments.services.initiate_refund)
lives inside booking.services.cancel_appointment itself (docs/DECISIONS.md
§ "Refund-eligibility gap (found 19.09.2026, closing Stage 8)") — this view
only supplies the provider and the guest-token-specific side effect below.

GuestBookingCreateView (§ Stage 7.D decisions) is the one write path with no
object to guard yet — it creates the Appointment — so it's a plain APIView,
not a generic.

AccountAppointmentListView (§ Stage 15 planning, item 1) and
AccountAppointmentCancelView (§ Stage 15 planning, item 4) are Account-JWT
authenticated, not guest-token authenticated — separate, additive endpoints
alongside the guest-token views above, not a replacement for any of them.
"""

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from booking.models import Appointment, CancelledBy
from booking.serializers import (
    AccountBookingRequestSerializer,
    AppointmentAccountSerializer,
    AppointmentCreatedSerializer,
    AppointmentGuestSerializer,
    GuestBookingRequestSerializer,
)
from booking.services import (
    cancel_appointment,
    create_account_appointment,
    create_guest_appointment,
)
from core.exceptions import EmailNotVerifiedError, InvalidOrExpiredTokenError
from core.permissions import HasValidGuestToken
from core.tenancy import get_current_salon_id
from payments.providers.base import PaymentProvider
from payments.providers.mock import MockPaymentProvider
from payments.serializers import PaymentGuestSerializer
from payments.services import initiate_payment
from tenants.models import Salon


class _GuestTokenAppointmentMixin:
    """
    Resolves the target Appointment from the validated guest token
    (request.guest_access_token, set by HasValidGuestToken.has_permission)
    — never from the URL. Even if check_object_permissions were somehow
    never invoked, a mismatched or tampered URL id can't reach the wrong
    appointment, because the wrong appointment is never fetched in the
    first place. The URL id is still compared and must match, purely so a
    stale or wrong link fails immediately rather than silently ignoring
    what's in the address bar (docs/DECISIONS.md § Stage 3 sub-step 3
    decisions).
    """

    lookup_url_kwarg = "appointment_id"

    def get_queryset(self) -> QuerySet[Appointment]:
        return Appointment.objects.all()

    def get_object(self) -> Appointment:
        token_row = self.request.guest_access_token  # type: ignore[attr-defined]
        url_appointment_id = self.kwargs[self.lookup_url_kwarg]  # type: ignore[attr-defined]
        if token_row.appointment_id != url_appointment_id:
            raise InvalidOrExpiredTokenError()

        appointment = get_object_or_404(self.get_queryset(), pk=token_row.appointment_id)
        self.check_object_permissions(self.request, appointment)  # type: ignore[attr-defined]
        return appointment


class GuestAppointmentDetailView(_GuestTokenAppointmentMixin, generics.RetrieveAPIView):
    permission_classes = [HasValidGuestToken]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "guest_token"
    guest_token_action = "view"
    serializer_class = AppointmentGuestSerializer


class GuestAppointmentCancelView(_GuestTokenAppointmentMixin, generics.GenericAPIView):
    """
    provider_class is a class attribute, not a module-level instance, so a
    test can substitute a fake/failing provider via
    monkeypatch.setattr(GuestAppointmentCancelView, "provider_class", ...) —
    same injection pattern as GuestAppointmentPayView. Only exercised when
    cancel_appointment actually finds a SUCCEEDED Payment to refund; most
    cancellations (no payment yet, or a still-PENDING one) never call it.
    """

    permission_classes = [HasValidGuestToken]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "guest_token"
    guest_token_action = "cancel"
    serializer_class = AppointmentGuestSerializer
    provider_class: type[PaymentProvider] = MockPaymentProvider

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        appointment = self.get_object()

        # The single now = timezone.now() call site for this request (§
        # Stage 6.F/6.I decisions), passed explicitly into the service and
        # reused below for the token update.
        now = timezone.now()

        # No try/except: InvalidStateTransitionError propagates to the
        # existing core.exceptions.exception_handler (409 + details).
        # cancel_appointment is role-agnostic and knows nothing about guest
        # tokens — the cancelled_via_token_at update stays here.
        appointment = cancel_appointment(
            appointment_id=appointment.id,
            salon=appointment.salon,
            cancelled_by=CancelledBy.GUEST,
            now=now,
            provider=self.provider_class(),
        )

        token_row = request.guest_access_token  # type: ignore[attr-defined]
        token_row.cancelled_via_token_at = now
        token_row.save(update_fields=["cancelled_via_token_at"])

        return Response(self.get_serializer(appointment).data)


class GuestAppointmentPayView(_GuestTokenAppointmentMixin, generics.GenericAPIView):
    """
    Guest pay endpoint (docs/DECISIONS.md § Stage 8.D decisions). Thin HTTP
    layer over payments.services.initiate_payment — no try/except: the
    service's InvalidStateTransitionError/PaymentProviderError, and the
    token errors from HasValidGuestToken/_GuestTokenAppointmentMixin, all
    propagate to the existing core.exceptions.exception_handler.

    provider_class is a class attribute, not a module-level instance, so a
    test can substitute a fake/failing provider via
    monkeypatch.setattr(GuestAppointmentPayView, "provider_class", ...) — the
    named contract from § Stage 8.D decisions. A fresh instance is built per
    request; no state survives across requests.
    """

    permission_classes = [HasValidGuestToken]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "guest_token"
    guest_token_action = "pay"
    serializer_class = PaymentGuestSerializer
    provider_class: type[PaymentProvider] = MockPaymentProvider

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        appointment = self.get_object()

        # The single now = timezone.now() call site for this request (§
        # Stage 6.F/6.I decisions), passed explicitly into the service.
        now = timezone.now()

        provider = self.provider_class()
        payment, provider_data, created = initiate_payment(
            appointment_id=appointment.id,
            salon=appointment.salon,
            provider=provider,
            now=now,
        )

        return Response(
            {"payment": self.get_serializer(payment).data, "provider_data": provider_data},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AccountAppointmentListView(generics.ListAPIView):
    """
    ``GET appointments/mine/`` — appointments belonging to the
    authenticated Account's linked Customer in the current salon
    (docs/DECISIONS.md § Stage 15 planning, item 1). Separate, additive
    endpoint: does not touch the guest-token views above.

    No explicit `authentication_classes`/`permission_classes` override:
    relies on the project-wide defaults (`AccountJWTCookieAuthentication` +
    `IsAuthenticated`), same posture as `accounts.views.MeView` — an
    unauthenticated request is already rejected with 401 before this view's
    code runs.

    `Appointment.objects` is tenant-scoped (`core.models.TenantScopedManager`),
    so a Customer belonging to another salon can never surface here even if
    its id were somehow guessed — the `customer_id` filter below narrows
    further, to the requesting Account's own linked Customer, the same
    "resolve from identity, never the URL" shape as
    `reviews.views.ReviewCreateView._resolve_owned_appointment`. An Account
    with no linked Customer yet (unverified, or verified but no matching
    guest Customer ever existed) legitimately has none, so the queryset is
    empty rather than an error.

    Ordered newest-appointment-first (`-start_datetime`, tie-broken by
    `-id`), the same newest-first convention `reviews.views.ReviewListView`
    already uses for a per-recipient list.

    `select_related("payment", "specialist", "service")` below joins the
    OneToOneField reverse relation `AppointmentAccountSerializer`'s
    `payment_status`/`payment_amount`/`amount_due_at_visit` fields read,
    plus the two plain FKs its nested `specialist`/`service` name fields
    read (docs/DECISIONS.md § Stage 15 planning, item 4) -- without it, each
    row's `appointment.payment`/`.specialist`/`.service` access would be a
    separate query, N+1 across a list. A OneToOneField's reverse side is
    select_related-able (unlike a plain reverse FK), same reasoning
    `reviews.views.ReviewListView` applies to
    `select_related("specialist", "appointment__service")`.
    """

    serializer_class = AppointmentAccountSerializer

    def get_queryset(self) -> QuerySet[Appointment]:
        customer_id = self.request.user.customer_id  # type: ignore[union-attr]
        if customer_id is None:
            return Appointment.objects.none()
        return (
            Appointment.objects.filter(customer_id=customer_id)
            .select_related("payment", "specialist", "service")
            .order_by("-start_datetime", "-id")
        )


class AccountAppointmentCancelView(generics.GenericAPIView):
    """
    ``POST appointments/<id>/cancel/`` — the authenticated Account cancels
    its own Customer's appointment (docs/DECISIONS.md § Stage 15 planning,
    item 4). Additive, alongside ``appointments/mine/`` above; no explicit
    `authentication_classes`/`permission_classes` override, same posture as
    `AccountAppointmentListView`/`accounts.views.MeView` — an
    unauthenticated request is already rejected with 401.

    Ownership is resolved the same "fetch by tenant-scoped pk, compare the
    owning Customer to the caller's identity, mismatch -> 404" shape as
    `reviews.views.ReviewCreateView._resolve_owned_appointment` — written
    out here rather than reused, since that helper also branches on
    guest-token identity, which doesn't apply on this Account-only path. An
    appointment belonging to another Customer, or an Account with no linked
    Customer at all, is indistinguishable from a nonexistent id: 404, never
    403, the same anti-enumeration posture.

    `cancelled_by=CancelledBy.CUSTOMER`, not `GUEST` — this is the client
    cancelling through their own account, a distinct enum member from the
    guest-token path even though `booking.services._CUSTOMER_INITIATED_CANCELLATIONS`
    already treats both the same for the 24h refund-eligibility cutoff. No
    other try/except: `InvalidStateTransitionError` (409) propagates to the
    existing `core.exceptions.exception_handler`, same as
    `GuestAppointmentCancelView`.

    `provider_class` is a class attribute, not a module-level instance, for
    the same test-substitution reason as `GuestAppointmentCancelView` and
    `GuestAppointmentPayView`.

    No `select_related("payment")` here (checked, docs/DECISIONS.md § Stage
    15 planning, item 4): this view handles exactly one appointment per
    request, so there's no N+1 to fix, and the object actually serialized
    below is `cancel_appointment`'s own return value -- a separate
    `select_for_update()` fetch inside the service, not
    `_resolve_owned_appointment`'s pre-cancel lookup -- so adding it to this
    view's `get_queryset()` wouldn't even reach the row that gets serialized.
    """

    serializer_class = AppointmentAccountSerializer
    provider_class: type[PaymentProvider] = MockPaymentProvider

    def get_queryset(self) -> QuerySet[Appointment]:
        return Appointment.objects.all()

    def _resolve_owned_appointment(self) -> Appointment:
        appointment = get_object_or_404(self.get_queryset(), pk=self.kwargs["appointment_id"])
        customer_id = self.request.user.customer_id  # type: ignore[union-attr]
        if customer_id is None or appointment.customer_id != customer_id:
            raise NotFound()
        return appointment

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        appointment = self._resolve_owned_appointment()

        # The single now = timezone.now() call site for this request (§
        # Stage 6.F/6.I decisions), passed explicitly into the service.
        now = timezone.now()

        appointment = cancel_appointment(
            appointment_id=appointment.id,
            salon=appointment.salon,
            cancelled_by=CancelledBy.CUSTOMER,
            now=now,
            provider=self.provider_class(),
        )

        return Response(self.get_serializer(appointment).data)


class GuestBookingCreateView(APIView):
    """
    Guest booking creation (docs/DECISIONS.md § Stage 7.D decisions). Public
    write (AllowAny), set explicitly — DEFAULT_PERMISSION_CLASSES is
    IsAuthenticated globally, so omitting this would silently block the
    unauthenticated guests this endpoint exists for.

    No view-level try/except: SlotNotOfferedError (400) and
    SlotUnavailableError (409), both raised by create_guest_appointment via
    the § Stage 7.C core it wraps, propagate to the existing
    core.exceptions.exception_handler, already wired as DRF's global
    EXCEPTION_HANDLER.
    """

    permission_classes = [AllowAny]

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        body = GuestBookingRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        # Salon is not a TenantScopedModel — it's the tenant root — so
        # Salon.objects is a plain manager (§ Stage 6.I decisions' same
        # reasoning).
        salon = get_object_or_404(Salon, pk=get_current_salon_id())

        # The single now = timezone.now() call site (§ Stage 6.F/6.I
        # decisions) — passed explicitly into the orchestrator, never re-read.
        now = timezone.now()

        appointment, raw_token = create_guest_appointment(
            salon=salon,
            specialist=body.validated_data["specialist"],
            service=body.validated_data["service"],
            start_datetime=body.validated_data["start_datetime"],
            now=now,
            customer_name=body.validated_data["customer_name"],
            customer_email=body.validated_data["customer_email"],
            customer_phone=body.validated_data["customer_phone"],
        )

        return Response(
            {
                "appointment": AppointmentCreatedSerializer(appointment).data,
                "guest_token": raw_token,
            },
            status=status.HTTP_201_CREATED,
        )


class AccountBookingCreateView(APIView):
    """
    ``POST appointments/`` — the authenticated Account books directly,
    without the guest contact-form/token flow (docs/DECISIONS.md § Stage 15
    planning, item 5, "Cycle B — account booking endpoint contract, decided
    20.09.2026"). No explicit `authentication_classes`/`permission_classes`
    override: relies on the project-wide `AccountJWTCookieAuthentication` +
    `IsAuthenticated` defaults, same posture as `AccountAppointmentListView`/
    `AccountAppointmentCancelView`/`accounts.views.MeView` — an
    unauthenticated request is already rejected with 401 before this view's
    code runs.

    Order of checks, in this exact sequence (docs/DECISIONS.md's "order of
    checks" bullet): (1) authentication (401, via the global default,
    above), (2) the unverified-Account check below (403
    `email_not_verified`), (3) `AccountBookingRequestSerializer` validation
    (400), (4) `create_account_appointment`'s own `transaction.atomic()`.
    The verification check is a plain `if` at the top of `post()`, not
    folded into the serializer or a permission class, specifically so it
    always runs before body validation — a validation error (e.g. missing
    `customer_name`) must never differ depending on whether the Account is
    verified, and an unverified Account must get the identical 403
    regardless of whether a Customer already exists for its email (the
    Refined 19.09.2026 entry's anti-enumeration reasoning).

    No view-level try/except: `SlotNotOfferedError` (400) and
    `SlotUnavailableError` (409), both raised by `create_account_appointment`
    via the same § Stage 7.C core `create_guest_appointment` wraps,
    propagate to the existing `core.exceptions.exception_handler` —
    identical mapping to `GuestBookingCreateView`, reused rather than
    reimplemented.
    """

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        account = request.user
        if account.email_verified_at is None:
            raise EmailNotVerifiedError()

        body = AccountBookingRequestSerializer(data=request.data, context={"request": request})
        body.is_valid(raise_exception=True)

        # Salon is not a TenantScopedModel — it's the tenant root — so
        # Salon.objects is a plain manager (§ Stage 6.I decisions' same
        # reasoning).
        salon = get_object_or_404(Salon, pk=get_current_salon_id())

        # The single now = timezone.now() call site (§ Stage 6.F/6.I
        # decisions) — passed explicitly into the orchestrator, never re-read.
        now = timezone.now()

        appointment = create_account_appointment(
            salon=salon,
            account=account,
            specialist=body.validated_data["specialist"],
            service=body.validated_data["service"],
            start_datetime=body.validated_data["start_datetime"],
            now=now,
            customer_name=body.validated_data.get("customer_name"),
            customer_phone=body.validated_data.get("customer_phone"),
        )

        return Response(
            {"appointment": AppointmentCreatedSerializer(appointment).data},
            status=status.HTTP_201_CREATED,
        )
