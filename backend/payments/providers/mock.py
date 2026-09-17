"""
Stage 8.A — MockPaymentProvider (docs/DECISIONS.md § Stage 8 decisions).
Built first, before any real adapter. Never touches the network and moves
no money; start_payment does not synchronously simulate success — a
Payment stays PENDING after the call. Driving it to SUCCEEDED only happens
via a fake webhook in tests for the state-transition layer, exactly as the
real provider would trigger it, so tests exercise the real asynchronous
shape rather than a self-completing shortcut.
"""

import json
from decimal import Decimal
from uuid import uuid4

from payments.providers.base import PaymentIntent, PaymentProvider, RefundIntent


class MockPaymentProvider(PaymentProvider):
    def start_payment(self, *, amount: Decimal, currency: str, reference: str) -> PaymentIntent:
        # provider_data=None per docs/DECISIONS.md § Stage 8.C decisions:
        # "The mock returns null."
        return PaymentIntent(
            provider_reference_id=f"mock_{uuid4().hex}",
            provider_data=None,
        )

    def refund(
        self, *, provider_reference_id: str, reference: str, amount: Decimal
    ) -> RefundIntent:
        return RefundIntent(provider_reference_id=f"mock_refund_{uuid4().hex}")

    def verify_signature(self, *, payload: bytes, signature: str) -> bool:
        # docs/DECISIONS.md § Stage 8.E decisions: "the mock does not verify
        # a real signature (stub returns True)". It never touches the
        # network and has no real secret to check against; a real adapter
        # computes an HMAC over `payload` and compares it to `signature`.
        return True

    def parse_webhook_event(self, *, payload: bytes) -> dict[str, str]:
        # The mock's webhook body already *is* the generic envelope
        # (tests build {event_id, event_type, provider_reference_id}
        # directly) — read straight from raw `payload` bytes rather than
        # request.data, for the same reason WayForPayProvider does: this
        # method runs against the same bytes verify_signature already saw,
        # not a Content-Type-dependent parse. Malformed/non-dict JSON comes
        # back as an empty envelope rather than raising — every field then
        # arrives blank/missing, which PaymentWebhookEventSerializer already
        # turns into the existing 400 "malformed body" response.
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        return {
            "event_id": str(data.get("event_id", "")),
            "event_type": str(data.get("event_type", "")),
            "provider_reference_id": str(data.get("provider_reference_id", "")),
        }
