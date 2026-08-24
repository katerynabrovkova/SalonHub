"""
Inbound payment-provider webhook (docs/ARCHITECTURE.md § 8;
docs/DECISIONS.md § Stage 8 decisions, § Stage 8.E decisions).
"""

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from booking.models import Appointment, AppointmentStatus
from core.tenancy import tenant_context
from payments.models import Payment, PaymentStatus, ProcessedWebhookEvent
from payments.providers.base import PaymentProvider
from payments.providers.mock import MockPaymentProvider
from payments.serializers import PaymentWebhookEventSerializer
from payments.services import initiate_refund
from tenants.models import Salon

logger = logging.getLogger(__name__)

_PAYMENT_SUCCEEDED = "payment_succeeded"
_PAYMENT_FAILED = "payment_failed"
_REFUND_SUCCEEDED = "refund_succeeded"
_HANDLED_EVENT_TYPES = frozenset({_PAYMENT_SUCCEEDED, _PAYMENT_FAILED, _REFUND_SUCCEEDED})

# Per event_type, the Payment statuses a redelivery/race can legitimately
# still find the row in — the event's own target status, plus (for
# payment_succeeded only) states reachable by moving further past it. Any
# OTHER status is not a duplicate — it's a status this transition can never
# legally come from, i.e. a desync between us and the provider.
_TOLERATED_STATUSES_BY_EVENT_TYPE = {
    _PAYMENT_SUCCEEDED: frozenset(
        {PaymentStatus.SUCCEEDED, PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED}
    ),
    _PAYMENT_FAILED: frozenset({PaymentStatus.FAILED}),
    _REFUND_SUCCEEDED: frozenset({PaymentStatus.REFUNDED}),
}


def _warn_if_impossible_transition(
    *, event_id: str, event_type: str, provider_reference_id: str, payment_row: Payment
) -> None:
    if payment_row.status in _TOLERATED_STATUSES_BY_EVENT_TYPE[event_type]:
        return  # already at (or past) this event's target — an expected duplicate/race.
    logger.warning(
        "Payment webhook event %s (event_type=%s, provider_reference_id=%r) is not a "
        "legal transition for Payment %s, currently in status %s",
        event_id,
        event_type,
        provider_reference_id,
        payment_row.id,
        payment_row.status,
    )


class PaymentWebhookView(APIView):
    """
    POST /api/v1/webhooks/payments/ — a top-level route outside
    /api/v1/salons/<slug>/: the provider has no notion of a salon slug.
    AllowAny; the real gate is verify_signature, not tenant or user auth.

    All orchestration (signature check, idempotency, the coupled
    Payment/Appointment transition, and deciding *whether* to call
    initiate_refund) lives here rather than in a service, because this view
    is the single, HTTP-bound caller of all of it. Only the refund action
    itself is delegated to payments.services.initiate_refund (§ Stage 8.F)
    — it already owns the correct locking + provider-call-outside-
    transaction discipline for a refund, so it's reused, not duplicated.

    provider_class is a class attribute, not a module-level instance, so a
    test can substitute a fake/rejecting provider via
    monkeypatch.setattr(PaymentWebhookView, "provider_class", ...) — same
    injection pattern as booking/views.py's GuestAppointmentPayView.
    """

    permission_classes = [AllowAny]
    provider_class: type[PaymentProvider] = MockPaymentProvider

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        # Single clock read for the whole request (our standing rule) — reused
        # below as the `now=` passed to initiate_refund, so the refund's
        # timestamp reflects when this webhook was handled, not a second,
        # slightly-later read taken after the lock.
        now = timezone.now()
        provider = self.provider_class()

        # Step 1: signature check, against the RAW body, before anything
        # parses it — the signature is computed over exactly these bytes.
        signature = request.headers.get("X-Signature", "")
        if not provider.verify_signature(payload=request.body, signature=signature):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        # Step 2: parse/validate the body. request.data parses lazily and
        # raises DRF's ParseError on malformed JSON; is_valid(raise_exception=True)
        # raises ValidationError on a missing required field. Both propagate
        # uncaught to the existing core.exceptions.exception_handler (DRF's
        # global EXCEPTION_HANDLER), which already maps ordinary DRF
        # exceptions to 400 — no try/except needed here.
        serializer = PaymentWebhookEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event_id = serializer.validated_data["event_id"]
        event_type = serializer.validated_data["event_type"]
        provider_reference_id = serializer.validated_data["provider_reference_id"]

        # Step 3: resolve the Payment (and its salon) from the provider's
        # own key. unscoped_objects, deliberately: no tenant is bound yet —
        # that's the whole reason this row isn't looked up through the
        # ordinary tenant-scoped .objects manager.
        try:
            payment = Payment.unscoped_objects.get(provider_reference_id=provider_reference_id)
        except Payment.DoesNotExist:
            logger.warning(
                "Payment webhook event %s references unknown provider_reference_id %r",
                event_id,
                provider_reference_id,
            )
            return Response(status=status.HTTP_404_NOT_FOUND)

        # Step 4: an unrecognized event_type is a pure no-op — no
        # transition to make and nothing to protect against a redelivery,
        # so no atomic() and no ProcessedWebhookEvent row (§ Stage 8.E
        # decisions: the ledger insert only ever happens alongside a
        # transition, in the same atomic() as that transition).
        if event_type not in _HANDLED_EVENT_TYPES:
            return Response(status=status.HTTP_200_OK)

        should_initiate_refund = False
        with tenant_context(payment.salon_id):
            # Fetched deliberately, not via the payment.salon lazy FK
            # descriptor — payment came from unscoped_objects before any
            # tenant was bound, so a lazy .salon access there would cross
            # the tenant boundary implicitly. We already know the id and
            # are inside its own tenant_context, so fetch it explicitly.
            salon = Salon.objects.get(pk=payment.salon_id)

            if ProcessedWebhookEvent.objects.filter(provider_event_id=event_id).exists():
                return Response(status=status.HTTP_200_OK)

            # Step 5: lock, recheck under the lock, transition, and record
            # the ledger row — all in one short atomic() block, exactly the
            # "processed" and "marked processed" commit together guarantee
            # from § Stage 8.E decisions. Deliberately CLOSED before any
            # call to initiate_refund below: initiate_refund opens and
            # manages its OWN atomic() + select_for_update() + an
            # out-of-transaction provider.refund() network call. Calling it
            # from inside this block would make Django treat it as a nested
            # savepoint rather than a real commit — the SELECT FOR UPDATE
            # lock taken here would then still be held for the entire
            # duration of initiate_refund's network call, exactly the
            # "network call must not hold a row lock" mistake § Stage 8.F
            # decisions rules out for initiate_refund's own internals.
            # Sequencing the refund call after this block exits guarantees
            # this lock is released first; initiate_refund re-acquires its
            # own fresh lock on the (by-then-committed) SUCCEEDED row.
            with transaction.atomic():
                payment_row = Payment.objects.select_for_update().get(pk=payment.pk)

                if event_type == _PAYMENT_SUCCEEDED:
                    if payment_row.status == PaymentStatus.PENDING:
                        payment_row.status = PaymentStatus.SUCCEEDED
                        payment_row.save(update_fields=["status"])

                        appointment_row = Appointment.objects.select_for_update().get(
                            pk=payment_row.appointment_id
                        )
                        if appointment_row.status == AppointmentStatus.PENDING_PAYMENT:
                            appointment_row.status = AppointmentStatus.CONFIRMED
                            appointment_row.save(update_fields=["status"])
                        elif appointment_row.status == AppointmentStatus.EXPIRED:
                            # Sweep-vs-webhook rule 3 (§ Stage 8 decisions):
                            # the slot was already released by the § Stage
                            # 7.F sweep. Do not resurrect it to CONFIRMED —
                            # the payment still succeeded, so it's settled
                            # to SUCCEEDED above, and refunded once this
                            # transaction commits and releases its lock.
                            should_initiate_refund = True
                    else:
                        # Not PENDING: either an expected duplicate/race
                        # (already SUCCEEDED or further along) or a desync
                        # (e.g. FAILED) — the helper tells them apart and
                        # only warns on the latter.
                        _warn_if_impossible_transition(
                            event_id=event_id,
                            event_type=event_type,
                            provider_reference_id=provider_reference_id,
                            payment_row=payment_row,
                        )

                elif event_type == _PAYMENT_FAILED:
                    if payment_row.status == PaymentStatus.PENDING:
                        payment_row.status = PaymentStatus.FAILED
                        payment_row.save(update_fields=["status"])
                    else:
                        _warn_if_impossible_transition(
                            event_id=event_id,
                            event_type=event_type,
                            provider_reference_id=provider_reference_id,
                            payment_row=payment_row,
                        )

                elif event_type == _REFUND_SUCCEEDED:
                    if payment_row.status == PaymentStatus.REFUND_PENDING:
                        payment_row.status = PaymentStatus.REFUNDED
                        payment_row.save(update_fields=["status"])
                    else:
                        _warn_if_impossible_transition(
                            event_id=event_id,
                            event_type=event_type,
                            provider_reference_id=provider_reference_id,
                            payment_row=payment_row,
                        )

                ProcessedWebhookEvent.objects.create(provider_event_id=event_id)

            if should_initiate_refund:
                initiate_refund(payment_id=payment_row.id, salon=salon, provider=provider, now=now)

        return Response(status=status.HTTP_200_OK)
