"""
Stage 9 step (b.2) — EmailChannel (docs/ARCHITECTURE.md § 9;
docs/DECISIONS.md § Stage 9 decisions). The first concrete
NotificationChannel: it wraps django.core.mail.send_mail, the same call
accounts/tasks.py's credential emails already use.

`from_email` is read from settings.DEFAULT_FROM_EMAIL inside the adapter,
not passed to send() — it is a platform constant and a delivery detail
specific to email (a Telegram channel has no from_email), so it belongs
here rather than in the universal NotificationChannel.send signature.

send_mail raises on a delivery failure (SMTP error, etc.); this adapter
does not catch it. Per the dumb-pipe contract in base.py, success returns
None and failure propagates — the retry policy is the service's job
(step b.4).
"""

from django.conf import settings
from django.core.mail import send_mail

from notifications.channels.base import NotificationChannel


class EmailChannel(NotificationChannel):
    def send(self, *, recipient: str, subject: str, body: str) -> None:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
        )
