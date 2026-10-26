"""
Stage 16 sub-step 10 — the `safety_identifier` sent to the model provider
(docs/DECISIONS.md § "Sub-step 10 details", "`safety_identifier`").

HMAC-SHA256 of "<salon id>:<client IP>", keyed with
ASSISTANT_SAFETY_IDENTIFIER_SECRET (its own setting, never SECRET_KEY);
only the hex digest leaves the backend. Per customer (salon + IP), not per
salon: OpenAI cannot unblock a blocked identifier, so one customer must not
get a whole salon blocked. The salon id is digits only, so the separator
keeps salon and IP apart. The caller computes it; providers only forward
it.
"""

import hashlib
import hmac

from django.conf import settings


def compute_safety_identifier(*, salon_id: int, client_ip: str) -> str:
    key = settings.ASSISTANT_SAFETY_IDENTIFIER_SECRET.encode()
    message = f"{salon_id}:{client_ip}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()
