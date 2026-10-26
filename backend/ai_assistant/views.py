"""
Stage 16 sub-step 7 — the assistant endpoints (docs/DECISIONS.md §
"Sub-step 7 details").

    POST /api/v1/salons/<slug>/assistant/messages/
    GET  /api/v1/salons/<slug>/assistant/history/

Both are public and never authenticate (PublicEndpointMixin), and both
answer 404 when the salon has the assistant switched off. The provider comes
from get_assistant_provider(), looked up here so tests can patch it in this
module. The view computes the provider's safety_identifier from the salon
and the client IP DRF's get_ident() returns (docs/DECISIONS.md § "Sub-step
10 details").

POST validates the message before anything streams, then answers an NDJSON
stream of the orchestrator's client events. The session id travels in the
X-Assistant-Session header both ways; an id with no history in this salon
(unknown, expired or another salon's) silently starts a new conversation.

Limits (docs/DECISIONS.md § "Sub-step 8 details", 8a) all answer before the
stream: the per-session and salon + IP throttles run before the view, then
the 404, the 400, the per-salon daily count, and the global stream cap
(8b). The stream slot is freed in the stream's finally, when the response
is closed (also for a body never iterated), and on any exception before the
response is returned. History is unthrottled.

The response body is iterated after TenantResolutionMiddleware has already
unbound the tenant, so the generator binds tenant_context itself. Any
unexpected exception becomes an `internal_error` event; the details go to
the log only. GeneratorExit (the customer closed the tab) passes through,
so the orchestrator logs the turn as cancelled.
"""

import datetime as dt
import json
import logging
from collections.abc import Generator, Iterator
from contextlib import closing
from typing import Any

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView

from accounts.views import PublicEndpointMixin
from ai_assistant.limits import (
    DAILY_MESSAGE_LIMIT,
    acquire_stream_slot,
    count_daily_message,
    release_stream_slot,
    seconds_until_local_midnight,
    undo_daily_message,
)
from ai_assistant.orchestrator import run_turn
from ai_assistant.providers import get_assistant_provider
from ai_assistant.providers.base import AssistantProvider
from ai_assistant.safety_identifier import compute_safety_identifier
from ai_assistant.serializers import AssistantMessageSerializer
from ai_assistant.session_store import load_history, new_session_id
from ai_assistant.throttles import (
    SESSION_HEADER,
    AssistantSalonIPThrottle,
    AssistantSessionThrottle,
)
from core.exceptions import AssistantBusyError, AssistantDailyLimitError
from core.i18n import resolve_language_code
from core.tenancy import get_current_salon_id, tenant_context
from tenants.models import Salon

logger = logging.getLogger(__name__)

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
    safety_identifier: str,
    now: dt.datetime,
    slot: str,
) -> Generator[str]:
    with tenant_context(salon_id):
        try:
            with closing(
                run_turn(
                    session_id=session_id,
                    user_text=user_text,
                    lang=lang,
                    now=now,
                    provider=provider,
                    safety_identifier=safety_identifier,
                )
            ) as events:
                for event in events:
                    yield _line(event)
        except Exception:
            # Never the customer text: only the salon and the traceback.
            logger.exception("assistant stream failed: salon_id=%s", salon_id)
            yield _line(INTERNAL_ERROR_EVENT)
        finally:
            release_stream_slot(slot)


class _SlotReleasingStream:
    """
    The stream body, plus a close() that frees the stream slot.
    StreamingHttpResponse calls close() when the response is closed, also
    when the body was never iterated; closing a generator that never started
    skips its finally. Release is idempotent, so when the generator's finally
    has already freed the slot, this second release is harmless.
    """

    def __init__(self, events: Generator[str], slot: str) -> None:
        self._events = events
        self._slot = slot

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        return next(self._events)

    def close(self) -> None:
        try:
            self._events.close()
        finally:
            release_stream_slot(self._slot)


class AssistantMessageCreateView(PublicEndpointMixin, APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AssistantSessionThrottle, AssistantSalonIPThrottle]

    def handle_exception(self, exc: Exception) -> Response:
        # Our exception handler builds DomainError responses without
        # headers, so the limits' Retry-After is set here.
        response = super().handle_exception(exc)
        if isinstance(exc, AssistantDailyLimitError | AssistantBusyError):
            response["Retry-After"] = str(exc.retry_after)
        return response

    def post(self, request: Request, *args: object, **kwargs: object) -> StreamingHttpResponse:
        salon = _enabled_salon()
        serializer = AssistantMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        now = timezone.now()
        if count_daily_message(salon=salon, now=now) > DAILY_MESSAGE_LIMIT:
            raise AssistantDailyLimitError(
                retry_after=seconds_until_local_midnight(salon=salon, now=now)
            )

        slot = acquire_stream_slot()
        if slot is None:
            # A refused message costs no provider call: it does not count.
            undo_daily_message(salon=salon, now=now)
            raise AssistantBusyError()
        try:
            return self._stream_response(request, salon, serializer, now, slot)
        except BaseException:
            release_stream_slot(slot)
            raise

    def _stream_response(
        self,
        request: Request,
        salon: Salon,
        serializer: AssistantMessageSerializer,
        now: dt.datetime,
        slot: str,
    ) -> StreamingHttpResponse:
        session_id = request.headers.get(SESSION_HEADER)
        if not session_id or not load_history(session_id):
            session_id = new_session_id()

        events = _stream(
            salon_id=salon.id,
            session_id=session_id,
            user_text=serializer.validated_data["message"],
            lang=resolve_language_code(request.query_params.get("lang")),
            provider=get_assistant_provider(),
            # The same IP the salon + IP throttle keys on (DRF's get_ident()).
            safety_identifier=compute_safety_identifier(
                salon_id=salon.id, client_ip=BaseThrottle().get_ident(request)
            ),
            now=now,
            slot=slot,
        )
        response = StreamingHttpResponse(
            _SlotReleasingStream(events, slot), content_type="application/x-ndjson"
        )
        response[SESSION_HEADER] = session_id
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


class AssistantHistoryView(PublicEndpointMixin, APIView):
    permission_classes = [AllowAny]
    # A cheap Redis read, deliberately unthrottled (docs/DECISIONS.md §
    # "Sub-step 8 details", 8a).
    throttle_classes: list[type] = []

    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        _enabled_salon()
        session_id = request.headers.get(SESSION_HEADER)
        messages = load_history(session_id) if session_id else []
        return Response({"messages": messages})
