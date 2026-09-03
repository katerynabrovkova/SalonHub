"""
Stage 9 step (b.1) — MockNotificationChannel (docs/DECISIONS.md § Stage 9
decisions). Built first, before any real adapter. Never touches the
network and delivers nothing: `send` only records the call on the instance
so a test can assert it happened, then returns None — the same
"never touches the network" discipline as MockPaymentProvider.
"""

from notifications.channels.base import NotificationChannel


class MockNotificationChannel(NotificationChannel):
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        self.sent.append((recipient, subject, body))
