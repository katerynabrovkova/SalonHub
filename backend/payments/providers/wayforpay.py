"""
WayForPayProvider — first real payment adapter alongside
MockPaymentProvider (docs/DECISIONS.md § "Stage 14 payment step:
WayForPayProvider architecture" and § "WayForPayProvider: first-time
technical conventions"). This step implements start_payment() only.
refund() stays a NotImplementedError stub (separate future step, not in
scope here) and verify_signature() is deliberately left unoverridden,
inheriting the base class's NotImplementedError — its exact HMAC field
order for WayForPay's Verify endpoint isn't confirmed from documentation
alone and is its own future step.

Uses WayForPay's server-to-server "Create Invoice" API
(api.wayforpay.com/api), not the client-rendered signed-form "Purchase"
flow — see the Stage 14 architecture entry for why.

Not wired as any view's default provider_class yet; MockPaymentProvider
remains the default everywhere.
"""

import hashlib
import hmac
import time
from decimal import Decimal
from uuid import uuid4

import requests
from django.conf import settings

from payments.providers.base import PaymentIntent, PaymentProvider, RefundIntent

_CREATE_INVOICE_URL = "https://api.wayforpay.com/api"
# No existing convention in this codebase to match (first outbound HTTP
# call) — 10s picked as a reasonable ceiling for a synchronous
# server-to-server call inside a request/response cycle.
_REQUEST_TIMEOUT_SECONDS = 10


def _format_amount(amount: Decimal) -> str:
    # docs/DECISIONS.md § "WayForPayProvider: first-time technical
    # conventions" — quantize first, then convert, rather than relying on
    # whatever precision the Decimal already happens to carry, so the HMAC
    # input matches WayForPay's own computation regardless of how the
    # caller's Decimal was constructed.
    return str(amount.quantize(Decimal("0.01")))


class WayForPayProvider(PaymentProvider):
    def start_payment(self, *, amount: Decimal, currency: str, reference: str) -> PaymentIntent:
        # Own unique orderReference, never the bare `reference` argument —
        # WayForPay rejects a reused orderReference with a "(1112)
        # Duplicate Order ID" error (Stage 14 architecture entry).
        # `reference` stays the stable appointment-derived identifier at
        # the interface level; this suffix is purely internal to this call.
        order_reference = f"{reference}-{uuid4().hex[:8]}"
        order_date = int(time.time())
        amount_str = _format_amount(amount)
        product_name = "Appointment deposit"
        product_count = 1
        product_price = amount_str

        merchant_account = settings.WAYFORPAY_MERCHANT_ACCOUNT
        merchant_domain_name = settings.WAYFORPAY_DOMAIN_NAME

        signature_fields = [
            merchant_account,
            merchant_domain_name,
            order_reference,
            str(order_date),
            amount_str,
            currency,
            product_name,
            str(product_count),
            product_price,
        ]
        merchant_signature = hmac.new(
            settings.WAYFORPAY_SECRET_KEY.encode("utf-8"),
            ";".join(signature_fields).encode("utf-8"),
            hashlib.md5,
        ).hexdigest()

        body = {
            "transactionType": "CREATE_INVOICE",
            "merchantAccount": merchant_account,
            "merchantDomainName": merchant_domain_name,
            "orderReference": order_reference,
            "orderDate": order_date,
            "amount": amount_str,
            "currency": currency,
            "productName": [product_name],
            "productCount": [product_count],
            "productPrice": [product_price],
            "merchantSignature": merchant_signature,
            "apiVersion": 1,
        }

        response = requests.post(_CREATE_INVOICE_URL, json=body, timeout=_REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()

        if data.get("reasonCode") != "Ok":
            raise RuntimeError(
                f"WayForPay Create Invoice failed: reasonCode={data.get('reasonCode')!r}, "
                f"reason={data.get('reason')!r}"
            )

        invoice_url = data.get("invoiceUrl")
        if not invoice_url:
            raise RuntimeError("WayForPay Create Invoice response is missing invoiceUrl")

        return PaymentIntent(provider_reference_id=order_reference, provider_data=invoice_url)

    def refund(
        self, *, provider_reference_id: str, reference: str, amount: Decimal
    ) -> RefundIntent:
        raise NotImplementedError("WayForPayProvider.refund is a separate future step")
