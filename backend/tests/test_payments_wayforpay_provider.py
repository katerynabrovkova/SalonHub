"""
WayForPayProvider.start_payment() and verify_signature() (docs/ARCHITECTURE.md
§ 8; docs/DECISIONS.md § "Stage 14 payment step: WayForPayProvider
architecture" and § "WayForPayProvider: first-time technical conventions").
refund() is a separate future step, not covered here.

Pure unit tests using `responses` to mock the real outbound HTTP call
(docs/DECISIONS.md's chosen library, over patching the call site directly)
— every test asserts on the actual request WayForPayProvider sent (URL,
method, body fields, computed signature), not merely that some request was
made. No @pytest.mark.django_db: this layer never touches the database.

WAYFORPAY_MERCHANT_ACCOUNT/WAYFORPAY_SECRET_KEY/WAYFORPAY_DOMAIN_NAME are
overridden per-test via pytest-django's `settings` fixture (same pattern as
test_account_password_reset.py's FRONTEND_URL override) to known values, so
the expected HMAC signature can be hand-computed and asserted exactly. The
verify_signature tests use this same fake SECRET_KEY, never a real WayForPay
merchant secret — the field shape/order is what's under test, not any real
credential.
"""

import hashlib
import hmac
import json
from decimal import Decimal
from urllib.parse import urlencode

import pytest
import requests
import responses
from rest_framework.test import APIClient

from payments.providers.base import PaymentIntent
from payments.providers.wayforpay import _CREATE_INVOICE_URL, WayForPayProvider
from payments.views import PaymentWebhookView

MERCHANT_ACCOUNT = "test_merchant"
DOMAIN_NAME = "test.salonhub.example"
SECRET_KEY = "test_secret_key"


@pytest.fixture(autouse=True)
def _wayforpay_credentials(settings):
    settings.WAYFORPAY_MERCHANT_ACCOUNT = MERCHANT_ACCOUNT
    settings.WAYFORPAY_DOMAIN_NAME = DOMAIN_NAME
    settings.WAYFORPAY_SECRET_KEY = SECRET_KEY


def _expected_signature(*, order_reference: str, order_date: int, amount: str, currency: str):
    fields = [
        MERCHANT_ACCOUNT,
        DOMAIN_NAME,
        order_reference,
        str(order_date),
        amount,
        currency,
        "Appointment deposit",
        "1",
        amount,
    ]
    return hmac.new(
        SECRET_KEY.encode("utf-8"), ";".join(fields).encode("utf-8"), hashlib.md5
    ).hexdigest()


def _mock_success(
    *, invoice_url="https://secure.wayforpay.com/pay/abc123", reason="Ok", reason_code=1100
):
    # reasonCode is numeric and reason is the separate string field, matching
    # WayForPay's actual Create Invoice response shape (reasonCode=1100,
    # reason="Ok" on success) rather than both fields sharing one value.
    responses.add(
        responses.POST,
        _CREATE_INVOICE_URL,
        json={"reasonCode": reason_code, "reason": reason, "invoiceUrl": invoice_url},
        status=200,
    )


@responses.activate
def test_start_payment_sends_correct_request_shape():
    _mock_success()
    provider = WayForPayProvider()

    provider.start_payment(amount=Decimal("83.00"), currency="UAH", reference="123")

    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call.request.url == _CREATE_INVOICE_URL
    assert call.request.method == "POST"
    body = json.loads(call.request.body)

    assert body["transactionType"] == "CREATE_INVOICE"
    assert body["merchantAccount"] == MERCHANT_ACCOUNT
    assert body["merchantDomainName"] == DOMAIN_NAME
    assert body["orderReference"].startswith("123-")
    assert body["orderReference"] != "123"
    assert isinstance(body["orderDate"], int)
    assert body["amount"] == "83.00"
    assert body["currency"] == "UAH"
    assert body["productName"] == ["Appointment deposit"]
    assert body["productCount"] == [1]
    assert body["productPrice"] == ["83.00"]
    assert body["apiVersion"] == 1
    assert "merchantSignature" in body


@responses.activate
def test_start_payment_computes_correct_signature():
    _mock_success()
    provider = WayForPayProvider()

    provider.start_payment(amount=Decimal("83.00"), currency="UAH", reference="123")

    body = json.loads(responses.calls[0].request.body)
    expected = _expected_signature(
        order_reference=body["orderReference"],
        order_date=body["orderDate"],
        amount="83.00",
        currency="UAH",
    )
    assert body["merchantSignature"] == expected


@responses.activate
def test_start_payment_quantizes_a_non_normalized_amount_before_signing():
    """Decimal("83") vs Decimal("83.00") stringify differently unless
    quantized first (docs/DECISIONS.md's rationale for this convention) —
    proves the provider always signs "83.00", regardless of input precision.
    """
    _mock_success()
    provider = WayForPayProvider()

    provider.start_payment(amount=Decimal("83"), currency="UAH", reference="123")

    body = json.loads(responses.calls[0].request.body)
    assert body["amount"] == "83.00"
    assert body["productPrice"] == ["83.00"]


@responses.activate
def test_start_payment_parses_successful_response_into_payment_intent():
    _mock_success(invoice_url="https://secure.wayforpay.com/pay/xyz789")
    provider = WayForPayProvider()

    result = provider.start_payment(amount=Decimal("50.00"), currency="UAH", reference="456")

    body = json.loads(responses.calls[0].request.body)
    assert result == PaymentIntent(
        provider_reference_id=body["orderReference"],
        provider_data="https://secure.wayforpay.com/pay/xyz789",
    )


@responses.activate
def test_start_payment_raises_on_non_2xx_response():
    responses.add(responses.POST, _CREATE_INVOICE_URL, json={"error": "server error"}, status=500)
    provider = WayForPayProvider()

    with pytest.raises(requests.exceptions.HTTPError):
        provider.start_payment(amount=Decimal("50.00"), currency="UAH", reference="789")


@responses.activate
def test_start_payment_raises_on_non_ok_reason():
    _mock_success(reason="Declined", reason_code=1101)
    provider = WayForPayProvider()

    with pytest.raises(RuntimeError):
        provider.start_payment(amount=Decimal("50.00"), currency="UAH", reference="789")


@responses.activate
def test_start_payment_raises_on_missing_invoice_url():
    responses.add(
        responses.POST,
        _CREATE_INVOICE_URL,
        json={"reasonCode": 1100, "reason": "Ok"},
        status=200,
    )
    provider = WayForPayProvider()

    with pytest.raises(RuntimeError):
        provider.start_payment(amount=Decimal("50.00"), currency="UAH", reference="789")


@responses.activate
def test_start_payment_treats_numeric_reason_code_1100_with_reason_ok_as_success():
    """Regression test for the exact real WayForPay response shape that was
    incorrectly treated as a failure: numeric reasonCode=1100 (not the
    string "Ok") alongside reason="Ok". The old check compared reasonCode
    against the string "Ok", which is never true for this field, so every
    successful Create Invoice call raised RuntimeError instead of returning
    the invoiceUrl.
    """
    responses.add(
        responses.POST,
        _CREATE_INVOICE_URL,
        json={
            "invoiceUrl": "https://secure.wayforpay.com/invoice/abc123",
            "reason": "Ok",
            "reasonCode": 1100,
        },
        status=200,
    )
    provider = WayForPayProvider()

    result = provider.start_payment(amount=Decimal("50.00"), currency="UAH", reference="789")

    body = json.loads(responses.calls[0].request.body)
    assert result == PaymentIntent(
        provider_reference_id=body["orderReference"],
        provider_data="https://secure.wayforpay.com/invoice/abc123",
    )


# --- verify_signature: webhook callback signature -------------------------
#
# Confirmed field order (WayForPay support, wiki.wayforpay.com/view/608996852):
# merchantAccount;orderReference;amount;currency;authCode;cardPan;
# transactionStatus;reasonCode, HMAC_MD5. Non-signature fields below
# (fee, paymentSystem, reason) mirror a real captured payload's shape
# without carrying any real PII — they exist only to prove verify_signature
# ignores fields outside the 8 that matter.
_WEBHOOK_FIELDS = {
    "merchantAccount": MERCHANT_ACCOUNT,
    "orderReference": "73-f58fee78",
    "amount": "24",
    "currency": "USD",
    "authCode": "",
    "cardPan": "41****1111",
    "transactionStatus": "Declined",
    "reasonCode": "1105",
}
_WEBHOOK_SIGNATURE_FIELD_ORDER = (
    "merchantAccount",
    "orderReference",
    "amount",
    "currency",
    "authCode",
    "cardPan",
    "transactionStatus",
    "reasonCode",
)


def _expected_webhook_signature(fields: dict[str, str]) -> str:
    ordered = [fields.get(name, "") for name in _WEBHOOK_SIGNATURE_FIELD_ORDER]
    return hmac.new(
        SECRET_KEY.encode("utf-8"), ";".join(ordered).encode("utf-8"), hashlib.md5
    ).hexdigest()


def _webhook_body(fields: dict[str, str], signature: str) -> bytes:
    # application/x-www-form-urlencoded, matching WayForPay's real callback
    # Content-Type — not JSON. Includes a couple of incidental extra fields
    # (fee, paymentSystem, reason) alongside merchantSignature, same as a
    # real payload, none of which are part of the signed field set.
    payload = {
        **fields,
        "merchantSignature": signature,
        "fee": "0",
        "paymentSystem": "card",
        "reason": "Invalid card number",
    }
    return urlencode(payload).encode("utf-8")


def test_verify_signature_accepts_a_correctly_signed_real_shaped_payload():
    """Regression test for the exact real WayForPay webhook shape captured
    in production: a declined-card notification, form-urlencoded body,
    signature computed over merchantAccount;orderReference;amount;currency;
    authCode;cardPan;transactionStatus;reasonCode.
    """
    signature = _expected_webhook_signature(_WEBHOOK_FIELDS)
    payload = _webhook_body(_WEBHOOK_FIELDS, signature)
    provider = WayForPayProvider()

    assert provider.verify_signature(payload=payload, signature=signature) is True


def _webhook_body_json(fields: dict[str, str], signature: str) -> bytes:
    # Regression shape: a real captured WayForPay callback had
    # Content-Type: application/x-www-form-urlencoded but a JSON-encoded
    # body — this constructs that exact mismatch (JSON bytes, whatever the
    # header claims) to prove verify_signature detects it from the payload
    # itself rather than trusting a header it isn't even given here.
    payload = {
        **fields,
        "merchantSignature": signature,
        "fee": 0,
        "paymentSystem": "card",
        "reason": "Invalid card number",
    }
    return json.dumps(payload).encode("utf-8")


def test_verify_signature_accepts_a_json_encoded_body_despite_form_urlencoded_content_type():
    """Regression test for the real captured mismatch: WayForPay's callback
    arrived with Content-Type: application/x-www-form-urlencoded but a
    JSON-encoded body. parse_qs on that body returns {} (every field would
    sign as ""), so verify_signature must detect JSON from the bytes
    themselves, not assume form-encoding from the (unreliable) header.
    """
    signature = _expected_webhook_signature(_WEBHOOK_FIELDS)
    payload = _webhook_body_json(_WEBHOOK_FIELDS, signature)
    provider = WayForPayProvider()

    assert provider.verify_signature(payload=payload, signature=signature) is True


def test_verify_signature_rejects_a_tampered_field():
    signature = _expected_webhook_signature(_WEBHOOK_FIELDS)
    tampered_fields = {**_WEBHOOK_FIELDS, "amount": "999"}
    payload = _webhook_body(tampered_fields, signature)
    provider = WayForPayProvider()

    assert provider.verify_signature(payload=payload, signature=signature) is False


def test_verify_signature_rejects_the_wrong_signature():
    payload = _webhook_body(_WEBHOOK_FIELDS, "not-the-real-signature")
    provider = WayForPayProvider()

    assert provider.verify_signature(payload=payload, signature="not-the-real-signature") is False


def test_verify_signature_is_sensitive_to_field_order():
    """Same field values, wrong order (currency/amount swapped) — proves
    the field order matters and isn't accidentally order-independent (e.g.
    via a dict/set instead of the confirmed tuple order).
    """
    wrong_order = (
        "merchantAccount",
        "orderReference",
        "currency",
        "amount",
        "authCode",
        "cardPan",
        "transactionStatus",
        "reasonCode",
    )
    wrong_signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        ";".join(_WEBHOOK_FIELDS[name] for name in wrong_order).encode("utf-8"),
        hashlib.md5,
    ).hexdigest()
    payload = _webhook_body(_WEBHOOK_FIELDS, wrong_signature)
    provider = WayForPayProvider()

    assert provider.verify_signature(payload=payload, signature=wrong_signature) is False


def test_verify_signature_rejects_an_unparseable_payload_without_raising():
    provider = WayForPayProvider()
    garbage = b"not json and not form-encoded either \x00\xff"

    assert provider.verify_signature(payload=garbage, signature="anything") is False


# --- parse_webhook_event: WayForPay webhook -> generic envelope mapping ----
#
# docs/DECISIONS.md § "Stage 14 payment step: WayForPay webhook ->
# PaymentWebhookView mapping". Reuses _WEBHOOK_FIELDS/_webhook_body from the
# verify_signature section above — parse_webhook_event doesn't itself check
# the signature, so the signature value passed to _webhook_body is
# arbitrary in these tests.


def test_parse_webhook_event_maps_approved_to_payment_succeeded():
    fields = {**_WEBHOOK_FIELDS, "transactionStatus": "Approved"}
    payload = _webhook_body(fields, "irrelevant-signature")
    provider = WayForPayProvider()

    event = provider.parse_webhook_event(payload=payload)

    assert event["event_type"] == "payment_succeeded"
    assert event["provider_reference_id"] == fields["orderReference"]


def test_parse_webhook_event_maps_declined_to_payment_failed():
    fields = {**_WEBHOOK_FIELDS, "transactionStatus": "Declined"}
    payload = _webhook_body(fields, "irrelevant-signature")
    provider = WayForPayProvider()

    event = provider.parse_webhook_event(payload=payload)

    assert event["event_type"] == "payment_failed"


def test_parse_webhook_event_maps_refunded_to_refund_succeeded():
    fields = {**_WEBHOOK_FIELDS, "transactionStatus": "Refunded"}
    payload = _webhook_body(fields, "irrelevant-signature")
    provider = WayForPayProvider()

    event = provider.parse_webhook_event(payload=payload)

    assert event["event_type"] == "refund_succeeded"


def test_parse_webhook_event_maps_voided_to_refund_succeeded():
    fields = {**_WEBHOOK_FIELDS, "transactionStatus": "Voided"}
    payload = _webhook_body(fields, "irrelevant-signature")
    provider = WayForPayProvider()

    event = provider.parse_webhook_event(payload=payload)

    assert event["event_type"] == "refund_succeeded"


def test_parse_webhook_event_unmapped_status_falls_through_as_raw_status():
    fields = {**_WEBHOOK_FIELDS, "transactionStatus": "Pending"}
    payload = _webhook_body(fields, "irrelevant-signature")
    provider = WayForPayProvider()

    event = provider.parse_webhook_event(payload=payload)

    # Not one of the three handled event_type strings -> PaymentWebhookView's
    # existing unrecognized-event_type no-op path, unchanged.
    assert event["event_type"] == "Pending"


def test_parse_webhook_event_composite_event_id_format():
    fields = {
        **_WEBHOOK_FIELDS,
        "orderReference": "73-f58fee78",
        "transactionStatus": "Declined",
        "reasonCode": "1105",
    }
    payload = _webhook_body(fields, "irrelevant-signature")
    provider = WayForPayProvider()

    event = provider.parse_webhook_event(payload=payload)

    assert event["event_id"] == "73-f58fee78;Declined;1105"
    assert event["provider_reference_id"] == "73-f58fee78"


def test_parse_webhook_event_distinguishes_retries_from_status_progression():
    """A bare orderReference would collide across a Declined-then-Approved
    progression on the same order while still (correctly) deduping a true
    delivery retry of the identical status — the composite event_id must
    do both."""
    declined = {**_WEBHOOK_FIELDS, "transactionStatus": "Declined", "reasonCode": "1105"}
    approved = {**_WEBHOOK_FIELDS, "transactionStatus": "Approved", "reasonCode": "1100"}
    provider = WayForPayProvider()

    declined_event = provider.parse_webhook_event(
        payload=_webhook_body(declined, "irrelevant-signature")
    )
    declined_retry_event = provider.parse_webhook_event(
        payload=_webhook_body(declined, "irrelevant-signature")
    )
    approved_event = provider.parse_webhook_event(
        payload=_webhook_body(approved, "irrelevant-signature")
    )

    assert declined_event["event_id"] == declined_retry_event["event_id"]
    assert declined_event["event_id"] != approved_event["event_id"]


@pytest.mark.django_db
def test_missing_order_reference_is_400_at_the_view_level(monkeypatch):
    """The one test in this file that crosses into PaymentWebhookView
    rather than exercising WayForPayProvider in isolation (hence the
    django_db marker this file otherwise has no use for) — proving the
    mapping's blank-field behavior actually produces the existing
    malformed-body 400 contract end to end, not just at the provider's own
    return value."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", WayForPayProvider)
    fields = {k: v for k, v in _WEBHOOK_FIELDS.items() if k != "orderReference"}
    signature = _expected_webhook_signature(fields)
    payload = _webhook_body(fields, signature)
    client = APIClient()

    response = client.post(
        "/api/v1/webhooks/payments/",
        data=payload,
        content_type="application/x-www-form-urlencoded",
        HTTP_X_SIGNATURE=signature,
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_missing_transaction_status_is_400_at_the_view_level(monkeypatch):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", WayForPayProvider)
    fields = {k: v for k, v in _WEBHOOK_FIELDS.items() if k != "transactionStatus"}
    signature = _expected_webhook_signature(fields)
    payload = _webhook_body(fields, signature)
    client = APIClient()

    response = client.post(
        "/api/v1/webhooks/payments/",
        data=payload,
        content_type="application/x-www-form-urlencoded",
        HTTP_X_SIGNATURE=signature,
    )

    assert response.status_code == 400
