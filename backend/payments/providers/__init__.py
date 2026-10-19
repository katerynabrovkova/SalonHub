from payments.providers.base import PaymentProvider
from payments.providers.mock import MockPaymentProvider


def get_payment_provider() -> PaymentProvider:
    """
    The one place background code gets its payment provider
    (docs/DECISIONS.md § "Refund retry sweep", point 6). Today it returns
    MockPaymentProvider(); tests patch it. Views still use their own
    provider_class attribute; the stage that wires a real provider switches
    views and the refund retry sweep to this function together, so payments
    and sweep refunds never go through different providers.
    """
    return MockPaymentProvider()
