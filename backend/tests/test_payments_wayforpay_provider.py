"""
WayForPayProvider.start_payment() (docs/ARCHITECTURE.md § 8;
docs/DECISIONS.md § "Stage 14 payment step: WayForPayProvider architecture"
and § "WayForPayProvider: first-time technical conventions"). start_payment
only — refund() and verify_signature() are separate future steps, not
covered here.

Pure unit tests using `responses` to mock the real outbound HTTP call
(docs/DECISIONS.md's chosen library, over patching the call site directly)
— every test asserts on the actual request WayForPayProvider sent (URL,
method, body fields, computed signature), not merely that some request was
made. No @pytest.mark.django_db: this layer never touches the database.

WAYFORPAY_MERCHANT_ACCOUNT/WAYFORPAY_SECRET_KEY/WAYFORPAY_DOMAIN_NAME are
overridden per-test via pytest-django's `settings` fixture (same pattern as
test_account_password_reset.py's FRONTEND_URL override) to known values, so
the expected HMAC signature can be hand-computed and asserted exactly.
"""

import hashlib
import hmac
import json
from decimal import Decimal

import pytest
import requests
import responses

from payments.providers.base import PaymentIntent
from payments.providers.wayforpay import _CREATE_INVOICE_URL, WayForPayProvider

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


def _mock_success(*, invoice_url="https://secure.wayforpay.com/pay/abc123", reason_code="Ok"):
    responses.add(
        responses.POST,
        _CREATE_INVOICE_URL,
        json={"reasonCode": reason_code, "reason": reason_code, "invoiceUrl": invoice_url},
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
def test_start_payment_raises_on_non_ok_reason_code():
    _mock_success(reason_code="Declined")
    provider = WayForPayProvider()

    with pytest.raises(RuntimeError):
        provider.start_payment(amount=Decimal("50.00"), currency="UAH", reference="789")


@responses.activate
def test_start_payment_raises_on_missing_invoice_url():
    responses.add(
        responses.POST,
        _CREATE_INVOICE_URL,
        json={"reasonCode": "Ok", "reason": "Ok"},
        status=200,
    )
    provider = WayForPayProvider()

    with pytest.raises(RuntimeError):
        provider.start_payment(amount=Decimal("50.00"), currency="UAH", reference="789")
