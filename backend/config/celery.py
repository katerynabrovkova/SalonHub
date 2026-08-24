import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("bella")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# First entry: Stage 7.F's appointment-expiry sweep (docs/DECISIONS.md §
# Stage 7.F decisions). Second: Stage 8.G's stuck-refund flagging sweep
# (docs/DECISIONS.md § Stage 8.G decisions) — 1800s (30 min), not 60s: its
# 72-hour staleness threshold makes minute-level granularity pointless, and
# a 30-minute cadence still catches a newly-stuck refund well within the
# same hour it crosses the threshold. Reminder tasks (24h/2h) land in the
# notifications stage.
app.conf.beat_schedule = {
    "expire-pending-payment-appointments": {
        "task": "booking.tasks.expire_pending_payment_appointments",
        "schedule": 60.0,
    },
    "flag-stuck-refund-payments": {
        "task": "payments.tasks.flag_stuck_refund_payments",
        "schedule": 1800.0,
    },
}
