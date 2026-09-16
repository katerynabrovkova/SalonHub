"""
Stage 8.A — MockPaymentProvider (docs/DECISIONS.md § Stage 8 decisions).
Built first, before any real adapter. Never touches the network and moves
no money; start_payment does not synchronously simulate success — a
Payment stays PENDING after the call. Driving it to SUCCEEDED only happens
via a fake webhook in tests for the state-transition layer, exactly as the
real provider would trigger it, so tests exercise the real asynchronous
shape rather than a self-completing shortcut.
"""

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
