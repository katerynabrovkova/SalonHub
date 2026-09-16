"""
Stage 8.A — PaymentProvider interface + MockPaymentProvider
(docs/ARCHITECTURE.md § 8; docs/DECISIONS.md § Stage 8 decisions).
Tests only — written before payments/providers/base.py and
payments/providers/mock.py exist. Expected to fail on collection
(ModuleNotFoundError: No module named 'payments.providers') until those
files are added — same two-step red shape
test_booking_cancel_appointment.py established for cancel_appointment.

Pure unit tests: this layer never touches the database or the network, so
nothing here is @pytest.mark.django_db or uses tenant_context/fixtures from
conftest.py.

Signatures under test:

    PaymentProvider.start_payment(*, amount: Decimal, currency: str,
                                   reference: str) -> PaymentIntent
    PaymentProvider.refund(*, provider_reference_id: str,
                            reference: str, amount: Decimal) -> RefundIntent

PaymentIntent carries (provider_reference_id: str, provider_data: str | None);
RefundIntent carries (provider_reference_id: str) only — a new provider-side
id for the refund transaction itself, never an echo of the payment's own
provider_reference_id (mirrors Stripe's Refund object having its own id,
distinct from the PaymentIntent id it reverses).
"""

import inspect
import socket
from decimal import Decimal

import pytest

from payments.providers.base import PaymentIntent, PaymentProvider, RefundIntent
from payments.providers.mock import MockPaymentProvider

# --- Interface shape ---------------------------------------------------


def test_payment_provider_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        PaymentProvider()


def test_payment_provider_abstract_methods_are_start_payment_and_refund():
    assert PaymentProvider.__abstractmethods__ == frozenset({"start_payment", "refund"})


def test_start_payment_signature_is_keyword_only():
    params = inspect.signature(PaymentProvider.start_payment).parameters
    for name in ("amount", "currency", "reference"):
        assert params[name].kind == inspect.Parameter.KEYWORD_ONLY


def test_refund_signature_is_keyword_only_and_includes_amount():
    params = inspect.signature(PaymentProvider.refund).parameters
    assert set(params) - {"self"} == {"provider_reference_id", "reference", "amount"}
    for name in ("provider_reference_id", "reference", "amount"):
        assert params[name].kind == inspect.Parameter.KEYWORD_ONLY


# --- MockPaymentProvider: happy path ------------------------------------


def test_mock_start_payment_returns_payment_intent():
    provider = MockPaymentProvider()
    result = provider.start_payment(amount=Decimal("83.00"), currency="UAH", reference="123")
    assert isinstance(result, PaymentIntent)
    assert result.provider_reference_id.startswith("mock_")
    # docs/DECISIONS.md § Stage 8.C decisions: "The mock returns null."
    assert result.provider_data is None


def test_mock_refund_returns_refund_intent():
    provider = MockPaymentProvider()
    result = provider.refund(
        provider_reference_id="mock_abc", reference="123", amount=Decimal("83.00")
    )
    assert isinstance(result, RefundIntent)
    assert isinstance(result.provider_reference_id, str) and result.provider_reference_id
    # A new provider-side id for the refund itself, not an echo of the input.
    assert result.provider_reference_id != "mock_abc"


def test_mock_start_payment_reference_ids_are_unique_per_call():
    provider = MockPaymentProvider()
    first = provider.start_payment(amount=Decimal("10.00"), currency="UAH", reference="1")
    second = provider.start_payment(amount=Decimal("10.00"), currency="UAH", reference="1")
    assert first.provider_reference_id != second.provider_reference_id


def test_mock_refund_reference_ids_are_unique_per_call():
    provider = MockPaymentProvider()
    first = provider.refund(provider_reference_id="mock_x", reference="1", amount=Decimal("1"))
    second = provider.refund(provider_reference_id="mock_x", reference="1", amount=Decimal("1"))
    assert first.provider_reference_id != second.provider_reference_id


# --- MockPaymentProvider: honesty about being async/no self-completion -


def test_mock_start_payment_makes_no_network_call(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("MockPaymentProvider.start_payment must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", _fail)
    monkeypatch.setattr(socket, "create_connection", _fail)

    provider = MockPaymentProvider()
    provider.start_payment(amount=Decimal("5.00"), currency="UAH", reference="1")


def test_mock_refund_makes_no_network_call(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("MockPaymentProvider.refund must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", _fail)
    monkeypatch.setattr(socket, "create_connection", _fail)

    provider = MockPaymentProvider()
    provider.refund(provider_reference_id="mock_x", reference="1", amount=Decimal("1"))


def test_mock_provider_has_no_status_or_completion_state():
    """
    No method would let a caller drive or observe a PENDING -> SUCCEEDED
    transition synchronously — that only happens later, via a fake webhook
    in tests for the state-transition layer, not through this provider.
    """
    provider = MockPaymentProvider()
    for forbidden in ("complete_payment", "succeed", "mark_succeeded", "confirm", "settle"):
        assert not hasattr(provider, forbidden)


# --- Keyword-only enforcement on the concrete class ---------------------


def test_mock_start_payment_rejects_positional_args():
    provider = MockPaymentProvider()
    with pytest.raises(TypeError):
        provider.start_payment(Decimal("1"), "UAH", "1")


def test_mock_refund_rejects_positional_args():
    provider = MockPaymentProvider()
    with pytest.raises(TypeError):
        provider.refund("mock_x", "1")


def test_mock_refund_requires_amount_kwarg():
    provider = MockPaymentProvider()
    with pytest.raises(TypeError):
        provider.refund(provider_reference_id="mock_x", reference="1")
