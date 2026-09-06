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
# same hour it crosses the threshold. Third: Stage 9(e)'s day-before
# appointment-reminder sweep (docs/DECISIONS.md § Step (e) decisions) —
# 3600s (hourly), a single reminder per appointment, scanning for CONFIRMED
# appointments whose start_datetime falls in (now+24h, now+25h]; the 1-hour
# window width is matched to the 1-hour beat period.
app.conf.beat_schedule = {
    "expire-pending-payment-appointments": {
        "task": "booking.tasks.expire_pending_payment_appointments",
        "schedule": 60.0,
    },
    "flag-stuck-refund-payments": {
        "task": "payments.tasks.flag_stuck_refund_payments",
        "schedule": 1800.0,
    },
    "send-due-appointment-reminders": {
        "task": "notifications.tasks.send_due_appointment_reminders_task",
        "schedule": 3600.0,
    },
}
