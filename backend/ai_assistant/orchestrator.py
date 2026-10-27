"""
Stage 16 sub-step 6c — the conversation orchestrator (docs/DECISIONS.md §
"Stage 16 planning", "Request flow", "Sub-step 6 details", 6c).

`run_turn` handles one customer message: it sends the stored history, the
new message, the system instructions and the tool specs to the provider,
runs the model/tool loop (at most 10 provider calls), and yields client
events: {"type": "text", "text"}, {"type": "proposal", "card"},
{"type": "error", "code"} and {"type": "done"}. It needs a bound tenant
context; the endpoint's generator binds it (sub-step 7).

Text deltas are forwarded as they arrive. The proposal, if any, is sent
after all text, right before `done`. The customer message and the full
reply text are stored only on success. One log line per message carries the
salon id, summed token counts, latency and outcome, never any text.

Daily counters (sub-step 9): a conversation is counted on the first
successfully stored turn of a session, and a proposal for every emitted
`proposal` event. A counter failure is logged without text and swallowed.
`record_conversation_started` and `record_proposal` are imported as bare
names so tests can replace them here.

Sub-step 10 (docs/DECISIONS.md § "Sub-step 10 details"): every provider
call gets the caller's `safety_identifier`. The `provider_items` of a
call's Finish ride on that call's assistant message, so later calls of the
same customer message replay them; they are never stored. A deadline of
80 seconds per customer message is checked on every provider event and
before each further provider call; exceeded means more than 80 seconds.
Then the provider's stream is closed, nothing is stored, the client gets
`assistant_provider_error` and the log outcome is `deadline_exceeded`.
`monotonic` is imported as a bare name so tests can replace the clock.

Sub-step 10d (docs/DECISIONS.md § "Sub-step 10d details"): every tool call
also gets one INFO line on the `ai_assistant.orchestrator.tools` logger
with the salon id, tool name, outcome and counts, never text or argument
values.
"""

import datetime as dt
import json
import logging
from collections.abc import Callable, Generator
from time import monotonic
from typing import Any, Literal, cast

from django.db import transaction

from ai_assistant import tools
from ai_assistant.analytics import record_conversation_started, record_proposal
from ai_assistant.instructions import build_system_instructions
from ai_assistant.proposals import PROPOSE_BOOKING_SPEC, handle_propose_booking
from ai_assistant.providers.base import (
    AssistantProvider,
    ChatMessage,
    Finish,
    TextDelta,
    ToolCall,
    ToolSpec,
)
from ai_assistant.session_store import append_turn, load_history
from core.exceptions import AssistantProviderError
from tenants.models import Salon

logger = logging.getLogger(__name__)
# One line per tool call (sub-step 10d), on its own child logger.
tool_logger = logging.getLogger(f"{__name__}.tools")

# Raised from 5 in sub-step 10e: a normal turn already needs 5 calls; the
# 80-second deadline still bounds runaway turns (docs/DECISIONS.md §
# "Sub-step 10e details").
MAX_PROVIDER_CALLS = 10

# Deadline + the provider's per-call timeout must stay under the stream
# cap's 120-second stale cutoff (docs/DECISIONS.md § "Sub-step 10 details").
DEADLINE_SECONDS = 80

INVALID_TOOL_CALL: dict[str, Any] = {"error": "invalid_tool_call"}
ONE_PROPOSAL_PER_REPLY: dict[str, Any] = {"error": "one_proposal_per_reply"}

# Tools whose function takes the turn's `now` besides `lang`.
_TOOLS_TAKING_NOW = frozenset({"check_availability"})


def _tool_specs() -> list[ToolSpec]:
    return [spec for spec, _function in tools.TOOLS.values()] + [PROPOSE_BOOKING_SPEC]


def _matches_schema(spec: ToolSpec, arguments: dict[str, Any]) -> bool:
    """No unknown keys and every required key present. Value types are the
    tool's own concern: tools never raise on bad model values."""
    properties = spec.parameters.get("properties", {})
    required = spec.parameters.get("required", [])
    return set(arguments) <= set(properties) and all(key in arguments for key in required)


def _history_messages(session_id: str) -> list[ChatMessage]:
    return [
        ChatMessage(role=cast(Literal["user", "assistant"], item["role"]), content=item["content"])
        for item in load_history(session_id)
    ]


def _run_tool(call: ToolCall, *, lang: str, now: dt.datetime) -> dict[str, Any]:
    """One read-only tool call, looked up in TOOLS at call time."""
    entry = tools.TOOLS.get(call.name)
    if entry is None:
        return INVALID_TOOL_CALL
    spec, function = entry
    if not _matches_schema(spec, call.arguments):
        return INVALID_TOOL_CALL
    kwargs: dict[str, Any] = {**call.arguments, "lang": lang}
    if call.name in _TOOLS_TAKING_NOW:
        kwargs["now"] = now
    return function(**kwargs)


def _log_tool_call(salon: Salon, call: ToolCall, result: dict[str, Any]) -> None:
    """
    One INFO line per tool call: salon id, tool name, outcome and counts
    only (docs/DECISIONS.md § "Sub-step 10d details"). Never message text,
    argument values or the session id. A name that is not a known tool came
    from the model and is logged as "unknown".
    """
    known = call.name in tools.TOOLS or call.name == PROPOSE_BOOKING_SPEC.name
    name = call.name if known else "unknown"
    outcome = result.get("error", "ok")
    counts = ""
    if outcome == "ok" and name == "check_availability":
        counts = f" slots={len(result['slots'])}"
        if not result["slots"]:
            counts += f" reason={result['reason']}"
    elif outcome == "ok" and name == PROPOSE_BOOKING_SPEC.name:
        counts = (
            f" passed={len(call.arguments.get('slots', []))}"
            f" dropped={len(result['dropped'])} shown={len(result['slots'])}"
        )
    tool_logger.info(
        "assistant tool: salon_id=%s tool=%s outcome=%s%s", salon.id, name, outcome, counts
    )


class _DeadlineExceeded(Exception):
    """The customer message ran past DEADLINE_SECONDS."""


def _count(counter: str, record: Callable[..., None], *, salon: Salon, **kwargs: Any) -> None:
    """Runs one counter write; a failure is logged (never text) and
    swallowed. Its own atomic block, so a failed write cannot break an
    enclosing transaction."""
    try:
        with transaction.atomic():
            record(salon=salon, **kwargs)
    except Exception:
        logger.exception("assistant counter failed: salon_id=%s counter=%s", salon.id, counter)


def run_turn(
    *,
    session_id: str,
    user_text: str,
    lang: str,
    now: dt.datetime,
    provider: AssistantProvider,
    safety_identifier: str,
) -> Generator[dict[str, Any]]:
    salon = tools._current_salon()
    system = build_system_instructions(salon=salon, now=now, lang=lang)
    specs = _tool_specs()
    history = _history_messages(session_id)
    messages = [*history, ChatMessage(role="user", content=user_text)]

    started = monotonic()
    deadline = started + DEADLINE_SECONDS
    input_tokens = 0
    output_tokens = 0
    provider_calls = 0
    outcome = "error"
    reply_parts: list[str] = []
    card: dict[str, Any] | None = None
    try:
        while True:
            if monotonic() > deadline:
                # Time ran out after the last call's final event (e.g. in
                # tools): start no further provider call.
                raise _DeadlineExceeded
            provider_calls += 1
            call_text: list[str] = []
            tool_calls: list[ToolCall] = []
            provider_items: tuple[Any, ...] = ()
            stream = provider.stream(
                system=system, messages=messages, tools=specs, safety_identifier=safety_identifier
            )
            try:
                for event in stream:
                    if monotonic() > deadline:
                        raise _DeadlineExceeded
                    if isinstance(event, TextDelta):
                        call_text.append(event.text)
                        reply_parts.append(event.text)
                        yield {"type": "text", "text": event.text}
                    elif isinstance(event, ToolCall):
                        tool_calls.append(event)
                    elif isinstance(event, Finish):
                        input_tokens += event.input_tokens
                        output_tokens += event.output_tokens
                        provider_items = event.provider_items
            finally:
                # Closed now, not at garbage collection, so the vendor stops
                # generating (deadline, customer gone, or any failure).
                close = getattr(stream, "close", None)
                if callable(close):
                    close()

            if not tool_calls:
                if card is not None:
                    # Counted before the yield: if the stream is closed while
                    # paused at it, code after the yield never runs.
                    _count(
                        "proposal",
                        record_proposal,
                        salon=salon,
                        service_id=card["service"]["id"],
                        now=now,
                    )
                    yield {"type": "proposal", "card": card}
                append_turn(session_id, user_text, "".join(reply_parts))
                if not history:
                    _count("conversation", record_conversation_started, salon=salon, now=now)
                outcome = "ok"
                yield {"type": "done"}
                return

            if provider_calls >= MAX_PROVIDER_CALLS:
                # The tools of this last call are not executed.
                outcome = "turn_limit"
                yield {"type": "error", "code": "assistant_turn_limit"}
                return

            messages.append(
                ChatMessage(
                    role="assistant",
                    content="".join(call_text),
                    tool_calls=tuple(tool_calls),
                    provider_items=provider_items,
                )
            )
            for call in tool_calls:
                if call.name == PROPOSE_BOOKING_SPEC.name:
                    if not _matches_schema(PROPOSE_BOOKING_SPEC, call.arguments):
                        result = INVALID_TOOL_CALL
                    elif card is not None:
                        result = ONE_PROPOSAL_PER_REPLY
                    else:
                        result, card = handle_propose_booking(
                            lang=lang, now=now, arguments=call.arguments
                        )
                else:
                    result = _run_tool(call, lang=lang, now=now)
                _log_tool_call(salon, call, result)
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=json.dumps(result, ensure_ascii=False),
                        tool_call_id=call.id,
                    )
                )
    except AssistantProviderError:
        outcome = "provider_error"
        yield {"type": "error", "code": "assistant_provider_error"}
    except _DeadlineExceeded:
        # Its own log outcome; the client sees an ordinary provider error.
        outcome = "deadline_exceeded"
        yield {"type": "error", "code": "assistant_provider_error"}
    except GeneratorExit:
        # The customer closed the stream: nothing was stored.
        outcome = "cancelled"
        raise
    finally:
        logger.info(
            "assistant turn: salon_id=%s outcome=%s provider_calls=%d "
            "input_tokens=%d output_tokens=%d latency_ms=%d",
            salon.id,
            outcome,
            provider_calls,
            input_tokens,
            output_tokens,
            (monotonic() - started) * 1000,
        )
