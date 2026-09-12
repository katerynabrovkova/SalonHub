"""
Booking endpoints: guest booking creation, and guest-token-authenticated
appointment access (docs/ARCHITECTURE.md § 3, § 4).

GuestAppointmentDetailView/GuestAppointmentCancelView are single-object: a
guest views or cancels exactly the appointment named in the URL,
authenticated only by the X-Guest-Token header, never JWT. Both are DRF
generics, not plain APIView, specifically so check_object_permissions() is
guaranteed to run (DRF generics call it automatically inside get_object())
— see CLAUDE.md's "DRF object-level permissions" rule and
core/permissions.py's HasValidGuestToken docstring. The full
concurrency-safe cancellation service layer (notifications, refund
triggering) is Stage 8 work — this only flips status on an already-existing
Appointment.

GuestBookingCreateView (§ Stage 7.D decisions) is the one write path with no
object to guard yet — it creates the Appointment — so it's a plain APIView,
not a generic.
"""

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from booking.models import Appointment, CancelledBy
from booking.serializers import (
    AppointmentCreatedSerializer,
    AppointmentGuestSerializer,
    GuestBookingRequestSerializer,
)
from booking.services import cancel_appointment, create_guest_appointment
from core.exceptions import InvalidOrExpiredTokenError
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
    permission_classes = [HasValidGuestToken]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "guest_token"
    guest_token_action = "cancel"
    serializer_class = AppointmentGuestSerializer

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
        )

        # Refund eligibility (deposit refunded/forfeited depending on
        # cancelled_by/timing, docs/DECISIONS.md § Business rules) is Stage 8
        # work — hook it in here once Payment exists.
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
