from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# TEMPORARY — lets a local ngrok tunnel through Django's ALLOWED_HOSTS check
# so WayForPay's real sandbox webhook can reach the local backend. Empty
# default: no effect unless explicitly set. Development-only by
# construction (this file, not base.py/production.py) — never touches
# production's ALLOWED_HOSTS. Remove this and the env var once WayForPay
# integration no longer needs a local tunnel.
_ngrok_allowed_host = env("NGROK_ALLOWED_HOST", default="")
if _ngrok_allowed_host:
    ALLOWED_HOSTS.append(_ngrok_allowed_host)

# Prints the full outgoing message to stdout instead of sending it — no
# mail-catcher container in docker-compose.yml. View it with:
#   docker compose logs celery_worker
# (email is always sent via a Celery task, never inline in the request — see
# docs/DECISIONS.md § Stage 3 decisions).
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
