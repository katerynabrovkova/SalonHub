"""
Stage 9 step (b.1) — NotificationChannel interface + MockNotificationChannel
(docs/ARCHITECTURE.md § 9; docs/DECISIONS.md § Stage 9 decisions). Tests
only — written before notifications/channels/ exists, expected to fail on
collection (ModuleNotFoundError: No module named 'notifications.channels')
until those files are added — the same two-step red shape
test_payments_providers.py established for the payment provider interface.

Pure unit tests: this layer never touches the database or the network, so
nothing here is @pytest.mark.django_db or uses fixtures from conftest.py.

Signature under test:

    NotificationChannel.send(*, recipient: str, subject: str,
                              body: str) -> None

A channel is a dumb pipe: it takes an already-resolved recipient address
and finished subject/body text and delivers it. It knows nothing about the
Notification model, recipient resolution, dedup, retry, or trigger types —
all of that is the service layer's job (step b.4). Mirrors PaymentProvider
taking primitives rather than a model row.
"""

import pytest

from notifications.channels.base import NotificationChannel
from notifications.channels.mock import MockNotificationChannel

# --- Interface shape -------------------------------------------------------


def test_notification_channel_abstract_methods_is_exactly_send():
    assert NotificationChannel.__abstractmethods__ == frozenset({"send"})


def test_notification_channel_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        NotificationChannel()


def test_incomplete_subclass_without_send_cannot_be_instantiated():
    class IncompleteChannel(NotificationChannel):
        pass

    with pytest.raises(TypeError):
        IncompleteChannel()


# --- MockNotificationChannel: records the call, never touches the network -


def test_mock_channel_send_returns_none_and_records_the_call():
    channel = MockNotificationChannel()

    result = channel.send(
        recipient="owner@bella-demo.example",
        subject="Booking confirmed",
        body="Your appointment is confirmed.",
    )

    assert result is None
    assert channel.sent == [
        ("owner@bella-demo.example", "Booking confirmed", "Your appointment is confirmed.")
    ]
