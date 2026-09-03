"""
Stage 9 step (b.1) — NotificationChannel interface (docs/ARCHITECTURE.md § 9;
docs/DECISIONS.md § Stage 9 decisions). The direct mirror of
payments/providers/base.py: one abstract method, the single action the
platform initiates toward a delivery channel.

A channel is a dumb pipe. `send` takes an already-resolved recipient
address and finished subject/body text and delivers it — nothing more. It
does not know about the `Notification` model, recipient resolution
(customer vs. salon), dedup, retry, or trigger types; all of that is the
service layer's job (step b.4). Success is a plain return (None); failure
is an exception, which the caller's retry policy (step b.4) handles. This
mirrors PaymentProvider taking primitives rather than a model row.
"""

from abc import ABC, abstractmethod


class NotificationChannel(ABC):
    @abstractmethod
    def send(self, *, recipient: str, subject: str, body: str) -> None:
        """
        Deliver `body` under `subject` to `recipient` — a resolved address
        for this channel (an email address for the email adapter). Returns
        None on success; raises on any delivery failure.
        """
