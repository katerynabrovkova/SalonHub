"""
Stage 16 sub-step 6c — the conversation orchestrator (docs/DECISIONS.md §
"Stage 16 planning", "Request flow", "Sub-step 6 details", 6c).

`run_turn` handles one customer message: it sends the stored history, the
new message, the system instructions and the tool specs to the provider,
runs the model/tool loop (at most 5 provider calls), and yields client
events: {"type": "text", "text"}, {"type": "proposal", "card"},
{"type": "error", "code"} and {"type": "done"}. It needs a bound tenant
context; the endpoint's generator binds it (sub-step 7).

Text deltas are forwarded as they arrive. The proposal, if any, is sent
after all text, right before `done`. The customer message and the full
reply text are stored only on success. One log line per message carries the
salon id, summed token counts, latency and outcome, never any text.
"""

import datetime as dt
import json
import logging
import time
from collections.abc import Generator
from typing import Any, Literal, cast

from ai_assistant import tools
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

logger = logging.getLogger(__name__)

MAX_PROVIDER_CALLS = 5

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


def run_turn(
    *,
    session_id: str,
    user_text: str,
    lang: str,
    now: dt.datetime,
    provider: AssistantProvider,
) -> Generator[dict[str, Any]]:
    salon = tools._current_salon()
    system = build_system_instructions(salon=salon, now=now, lang=lang)
    specs = _tool_specs()
    messages = [*_history_messages(session_id), ChatMessage(role="user", content=user_text)]

    started = time.monotonic()
    input_tokens = 0
    output_tokens = 0
    provider_calls = 0
    outcome = "error"
    reply_parts: list[str] = []
    card: dict[str, Any] | None = None
    try:
        while True:
            provider_calls += 1
            call_text: list[str] = []
            tool_calls: list[ToolCall] = []
            for event in provider.stream(system=system, messages=messages, tools=specs):
                if isinstance(event, TextDelta):
                    call_text.append(event.text)
                    reply_parts.append(event.text)
                    yield {"type": "text", "text": event.text}
                elif isinstance(event, ToolCall):
                    tool_calls.append(event)
                elif isinstance(event, Finish):
                    input_tokens += event.input_tokens
                    output_tokens += event.output_tokens

            if not tool_calls:
                if card is not None:
                    yield {"type": "proposal", "card": card}
                append_turn(session_id, user_text, "".join(reply_parts))
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
                    role="assistant", content="".join(call_text), tool_calls=tuple(tool_calls)
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
            (time.monotonic() - started) * 1000,
        )
