"""
Stage 8.A — PaymentProvider interface (docs/ARCHITECTURE.md § 8;
docs/DECISIONS.md § Stage 8 decisions). Exactly two methods: the two actions
the platform initiates toward the provider. A webhook is not a method here —
it's the provider calling back, the opposite direction, handled by a
separate inbound endpoint (a later Stage 8 sub-step).

Unit conversion (currency -> the provider's smallest unit, e.g. UAH ->
kopiykas) is provider-specific and belongs in a future real adapter, not
here — this interface and the mock work in Decimal currency units
throughout.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PaymentIntent:
    """Returned by start_payment. No money has moved yet — the outcome
    (PENDING -> SUCCEEDED) arrives asynchronously via webhook.

    provider_data is deliberately provider-neutral (docs/DECISIONS.md §
    Stage 8.C decisions) — whatever the frontend needs from the provider to
    continue payment (redirect URL, token, etc.), or None. Not named
    client_secret: that's Stripe-specific, and Stripe does not permit
    merchant accounts for Ukraine/Georgia residents, so the real provider
    will be a local acquirer whose frontend hand-off differs."""

    provider_reference_id: str
    provider_data: str | None


@dataclass(frozen=True)
class RefundIntent:
    """
    Returned by refund. provider_reference_id here is the provider's own id
    for the refund transaction itself — distinct from the original
    payment's provider_reference_id it reverses, mirroring Stripe's Refund
    object having its own id separate from the PaymentIntent id.
    """

    provider_reference_id: str


class PaymentProvider(ABC):
    @abstractmethod
    def start_payment(self, *, amount: Decimal, currency: str, reference: str) -> PaymentIntent:
        """
        reference is str(appointment.id) — the booking's id, not the Payment
        id — because no Payment row exists yet at the moment start_payment is
        called. Payment.appointment_id is unique (OneToOneField), so the
        appointment id still lets the later webhook resolve unambiguously
        which Payment to update.
        """

    @abstractmethod
    def refund(
        self, *, provider_reference_id: str, reference: str, amount: Decimal
    ) -> RefundIntent:
        """
        Reverses the specific existing transaction identified by
        provider_reference_id. `amount` mirrors start_payment's own `amount`
        param. docs/DECISIONS.md § "Stage 8 refund decision, revisited"
        widened the interface to this explicit parameter for a real
        provider's API requirement (an explicit amount even for a full
        refund) — Stage 8's underlying business rule, always the full
        deposit or nothing, is unchanged; callers always pass the original
        payment's own amount, never a partial or separately computed value.
        """

    def verify_signature(self, *, payload: bytes, signature: str) -> bool:
        """
        Verifies an inbound webhook's authenticity (docs/DECISIONS.md §
        Stage 8.E decisions) — the provider-calls-us direction, the mirror
        of start_payment/refund's platform-calls-provider direction.
        payload is the raw, unparsed request body: a real adapter computes
        an HMAC (or similar) over exactly those bytes and compares it
        against signature, so verification must happen before anything
        parses or mutates them.

        Deliberately NOT @abstractmethod: test_payments_providers.py's
        test_payment_provider_abstract_methods_are_start_payment_and_refund
        pins PaymentProvider.__abstractmethods__ to exactly
        {"start_payment", "refund"}. Every concrete provider must still
        override this — the base implementation raises rather than silently
        accepting, so a future adapter that forgets to override it fails
        loudly instead of leaving the webhook endpoint an open door.
        """
        raise NotImplementedError

    def parse_webhook_event(self, *, payload: bytes) -> dict[str, str]:
        """
        Normalizes a provider's raw webhook body into the provider-neutral
        {event_id, event_type, provider_reference_id} envelope that
        payments.serializers.PaymentWebhookEventSerializer validates
        (docs/DECISIONS.md § "Stage 14 payment step: WayForPay webhook ->
        PaymentWebhookView mapping"). Called by PaymentWebhookView.post
        after verify_signature succeeds, against the same raw `payload`
        bytes — not the parsed request.data, for the same
        Content-Type-can't-be-trusted reason verify_signature already
        works from raw bytes.

        A missing/unmappable field should come back as a missing or blank
        string in the returned dict, not raise — PaymentWebhookEventSerializer
        already turns a blank/missing required field into the existing
        400 "malformed body" response; this method doesn't need to
        duplicate that check.

        Deliberately NOT @abstractmethod, same reasoning as
        verify_signature above: every concrete provider must still
        override it, and the base implementation raises loudly rather than
        silently returning an empty envelope.
        """
        raise NotImplementedError
