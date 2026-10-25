"""
Stage 16 sub-step 7 — the assistant endpoints (docs/DECISIONS.md §
"Sub-step 7 details").

    POST /api/v1/salons/<slug>/assistant/messages/
    GET  /api/v1/salons/<slug>/assistant/history/

Both are public and never authenticate (PublicEndpointMixin), and both
answer 404 when the salon has the assistant switched off. The provider comes
from get_assistant_provider(), looked up here so tests can patch it in this
module.

POST validates the message before anything streams, then answers an NDJSON
stream of the orchestrator's client events. The session id travels in the
X-Assistant-Session header both ways; an id with no history in this salon
(unknown, expired or another salon's) silently starts a new conversation.

The response body is iterated after TenantResolutionMiddleware has already
unbound the tenant, so the generator binds tenant_context itself. Any
unexpected exception becomes an `internal_error` event; the details go to
the log only. GeneratorExit (the customer closed the tab) passes through,
so the orchestrator logs the turn as cancelled.
"""

import datetime as dt
import json
import logging
from collections.abc import Iterator
from contextlib import closing
from typing import Any

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.views import PublicEndpointMixin
from ai_assistant.orchestrator import run_turn
from ai_assistant.providers import get_assistant_provider
from ai_assistant.providers.base import AssistantProvider
from ai_assistant.serializers import AssistantMessageSerializer
from ai_assistant.session_store import load_history, new_session_id
from core.i18n import resolve_language_code
from core.tenancy import get_current_salon_id, tenant_context
from tenants.models import Salon

logger = logging.getLogger(__name__)

SESSION_HEADER = "X-Assistant-Session"
INTERNAL_ERROR_EVENT: dict[str, Any] = {"type": "error", "code": "internal_error"}


def _enabled_salon() -> Salon:
    salon = get_object_or_404(Salon, pk=get_current_salon_id())
    if not salon.assistant_enabled:
        raise NotFound()
    return salon


def _line(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def _stream(
    *,
    salon_id: int,
    session_id: str,
    user_text: str,
    lang: str,
    provider: AssistantProvider,
    now: dt.datetime,
) -> Iterator[str]:
    with tenant_context(salon_id):
        try:
            with closing(
                run_turn(
                    session_id=session_id,
                    user_text=user_text,
                    lang=lang,
                    now=now,
                    provider=provider,
                )
            ) as events:
                for event in events:
                    yield _line(event)
        except Exception:
            # Never the customer text: only the salon and the traceback.
            logger.exception("assistant stream failed: salon_id=%s", salon_id)
            yield _line(INTERNAL_ERROR_EVENT)


class AssistantMessageCreateView(PublicEndpointMixin, APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request, *args: object, **kwargs: object) -> StreamingHttpResponse:
        salon = _enabled_salon()
        serializer = AssistantMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session_id = request.headers.get(SESSION_HEADER)
        if not session_id or not load_history(session_id):
            session_id = new_session_id()

        response = StreamingHttpResponse(
            _stream(
                salon_id=salon.id,
                session_id=session_id,
                user_text=serializer.validated_data["message"],
                lang=resolve_language_code(request.query_params.get("lang")),
                provider=get_assistant_provider(),
                now=timezone.now(),
            ),
            content_type="application/x-ndjson",
        )
        response[SESSION_HEADER] = session_id
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


class AssistantHistoryView(PublicEndpointMixin, APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        _enabled_salon()
        session_id = request.headers.get(SESSION_HEADER)
        messages = load_history(session_id) if session_id else []
        return Response({"messages": messages})
