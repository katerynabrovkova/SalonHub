"""
WayForPayProvider — first real payment adapter alongside
MockPaymentProvider (docs/DECISIONS.md § "Stage 14 payment step:
WayForPayProvider architecture" and § "WayForPayProvider: first-time
technical conventions"). This step implements start_payment() only.
refund() stays a NotImplementedError stub (separate future step, not in
scope here). verify_signature() covers WayForPay's payment-status webhook
callback (transactionStatus/reasonCode notification, confirmed field order
per WayForPay support: merchantAccount;orderReference;amount;currency;
authCode;cardPan;transactionStatus;reasonCode, HMAC_MD5) — a different
signature scheme from Create Invoice's own (start_payment's
signature_fields, above). It carries its own signature in the
merchantSignature field rather than a header.

A real captured callback had Content-Type: application/x-www-form-urlencoded
with a JSON-encoded body — the header does not reliably describe the actual
bytes, so _parse_webhook_fields() detects the shape from payload itself
(valid JSON object wins, otherwise falls back to form-urlencoded) rather
than trusting a Content-Type the caller might pass in.

Uses WayForPay's server-to-server "Create Invoice" API
(api.wayforpay.com/api), not the client-rendered signed-form "Purchase"
flow — see the Stage 14 architecture entry for why.

Not wired as any view's default provider_class yet; MockPaymentProvider
remains the default everywhere.
"""

import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal
from urllib.parse import parse_qs
from uuid import uuid4

import requests
from django.conf import settings

from payments.providers.base import PaymentIntent, PaymentProvider, RefundIntent

logger = logging.getLogger(__name__)

_CREATE_INVOICE_URL = "https://api.wayforpay.com/api"
# No existing convention in this codebase to match (first outbound HTTP
# call) — 10s picked as a reasonable ceiling for a synchronous
# server-to-server call inside a request/response cycle.
_REQUEST_TIMEOUT_SECONDS = 10

# Confirmed by WayForPay support for the payment-status webhook callback
# (https://wiki.wayforpay.com/view/608996852) — distinct from
# start_payment's own Create Invoice signature_fields above.
_WEBHOOK_SIGNATURE_FIELD_NAMES = (
    "merchantAccount",
    "orderReference",
    "amount",
    "currency",
    "authCode",
    "cardPan",
    "transactionStatus",
    "reasonCode",
)


def parse_webhook_fields(payload: bytes) -> dict[str, str]:
    """
    Shared by verify_signature() and callers that need the same fields
    (e.g. to pull out merchantSignature before calling it) — one place
    deciding whether a webhook body is JSON or form-urlencoded, so that
    decision can't drift between call sites.

    Tries JSON first: a real captured callback had Content-Type:
    application/x-www-form-urlencoded but a JSON-encoded body, so trusting
    the header would silently fail to extract any field. Falls back to
    form-urlencoded parsing when the body isn't a JSON object (including
    when it's not valid JSON at all). All values are coerced to str —
    JSON has typed values (e.g. amount as an int); form-urlencoded values
    are already strings.
    """
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        data = None
    if isinstance(data, dict):
        return {key: ("" if value is None else str(value)) for key, value in data.items()}

    try:
        parsed = parse_qs(payload.decode("utf-8"))
    except UnicodeDecodeError:
        return {}
    return {key: values[0] for key, values in parsed.items()}


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
            "serviceUrl": settings.WAYFORPAY_SERVICE_URL,
        }

        # TEMPORARY diagnostic logging — see this call's failure paths. Logs
        # the real cause (WayForPay's own status/body, or the network-level
        # exception) before it gets translated into the generic
        # PaymentProviderError/502 the caller sees. Remove once the sandbox
        # failure has been root-caused.
        try:
            response = requests.post(
                _CREATE_INVOICE_URL, json=body, timeout=_REQUEST_TIMEOUT_SECONDS
            )
        except requests.exceptions.RequestException as exc:
            logger.error(
                "WayForPay Create Invoice request failed (network-level): %s: %s",
                type(exc).__name__,
                exc,
            )
            raise

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError:
            logger.error(
                "WayForPay Create Invoice returned an error status. status=%s body=%s",
                response.status_code,
                response.text,
            )
            raise

        data = response.json()

        # "reason" (a string) is WayForPay's canonical success indicator —
        # "Ok" on success. "reasonCode" is a numeric status code (1100 for
        # this endpoint's success case) that varies by API/transaction
        # type; comparing it against the string "Ok" was always false, so
        # every successful Create Invoice call was being treated as a
        # failure.
        if data.get("reason") != "Ok":
            logger.error(
                "WayForPay Create Invoice reported failure. status=%s body=%s",
                response.status_code,
                response.text,
            )
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

    def verify_signature(self, *, payload: bytes, signature: str) -> bool:
        # payload is the raw webhook body — parsed here, not by the
        # caller, because the 8 signed fields must come from these exact
        # bytes, same discipline as start_payment's own signature_fields.
        # A field absent from the payload signs as "", matching
        # start_payment's own empty-string fields (e.g. authCode on a
        # declined transaction).
        fields_by_name = parse_webhook_fields(payload)
        fields = [fields_by_name.get(name, "") for name in _WEBHOOK_SIGNATURE_FIELD_NAMES]
        expected_signature = hmac.new(
            settings.WAYFORPAY_SECRET_KEY.encode("utf-8"),
            ";".join(fields).encode("utf-8"),
            hashlib.md5,
        ).hexdigest()
        return hmac.compare_digest(expected_signature, signature)
