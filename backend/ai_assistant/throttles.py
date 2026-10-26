"""
Stage 16 sub-step 8a — the assistant's rate limits (docs/DECISIONS.md §
"Sub-step 8 details", 8a). Applied only to the message POST.

AssistantSessionThrottle: 10/min per session, keyed by the salon id and the
SHA-256 of the X-Assistant-Session header; without the header it does not
apply. AssistantSalonIPThrottle: 200/hour per salon + IP, keyed by the salon
id and DRF's get_ident(). Both count in DRF's default cache (DB 2) and
refuse with DRF's 429 `throttled` and Retry-After.
"""

import hashlib

from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from core.tenancy import get_current_salon_id

SESSION_HEADER = "X-Assistant-Session"


class AssistantSessionThrottle(SimpleRateThrottle):
    scope = "assistant_session"

    def get_cache_key(self, request: Request, view: APIView) -> str | None:
        session_id = request.headers.get(SESSION_HEADER)
        if not session_id:
            return None
        digest = hashlib.sha256(session_id.encode()).hexdigest()
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{get_current_salon_id()}_{digest}",
        }


class AssistantSalonIPThrottle(SimpleRateThrottle):
    scope = "assistant_salon_ip"

    def get_cache_key(self, request: Request, view: APIView) -> str | None:
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{get_current_salon_id()}_{self.get_ident(request)}",
        }
