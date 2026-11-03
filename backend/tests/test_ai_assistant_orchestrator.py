"""
Stage 16 sub-step 6c — the conversation orchestrator (docs/DECISIONS.md §
"Stage 16 planning", "Request flow", "Session", "Safety and privacy", and
"Sub-step 6 details", 6c).

`run_turn` is driven by scripted MockAssistantProvider turns; what the model
received is read back from `provider.calls` snapshots. The salon runs on
Asia/Tbilisi (UTC+04:00 all year) and `now` is a fixed tz-aware literal,
never `timezone.now()`. Session history goes to Redis DB 15 through
conftest's autouse `_assistant_redis` fixture.

Sub-step 10a (docs/DECISIONS.md § "Sub-step 10 details"): `run_turn` takes
a `safety_identifier` and passes it to every provider call; `provider_items`
from a call's Finish are replayed on that call's assistant message, within
one customer message only; and an 80-second deadline per customer message
is checked against `orchestrator.monotonic`, which the deadline tests
replace with a fake clock (never real sleeps). Exceeded means more than 80
seconds; exactly 80 is still in time.
"""

import datetime as dt
import importlib
import json
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import httpx2
import openai
import pytest
from openai.types.responses import ResponseErrorEvent

from ai_assistant import orchestrator as orchestrator_module
from ai_assistant import tools as tools_module
from ai_assistant.instructions import build_system_instructions
from ai_assistant.orchestrator import run_turn
from ai_assistant.proposals import PROPOSE_BOOKING_SPEC
from ai_assistant.providers.base import (
    AssistantProvider,
    ChatMessage,
    Finish,
    ProviderEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
)
from ai_assistant.providers.mock import MockAssistantProvider, ScriptTurn
from ai_assistant.providers.openai import OpenAIAssistantProvider
from ai_assistant.session_store import append_turn, load_history
from catalog.models import Service, ServiceCategory
from core.exceptions import AssistantProviderError
from core.tenancy import TenantContextMissingError, tenant_context
from tests.conftest import make_working_hours

pytestmark = pytest.mark.django_db

SESSION_ID = "orchestrator-test-session-0123456789abcdefghijklmnop"

# A Monday; 09:00-18:00 Tbilisi working hours == 05:00-14:00 UTC.
MONDAY = dt.date(2026, 8, 17)
NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=dt.UTC)
SLOT_0900 = "2026-08-17T09:00:00+04:00"
SAFETY_IDENTIFIER = "5a" * 32


@pytest.fixture
def tbilisi_salon(salon):
    salon.timezone = "Asia/Tbilisi"
    salon.save(update_fields=["timezone"])
    return salon


def _monday_hours(salon, specialist) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )


def _finish(input_tokens: int = 10, output_tokens: int = 5) -> Finish:
    return Finish(input_tokens=input_tokens, output_tokens=output_tokens)


def _run(
    salon,
    script: list[ScriptTurn],
    *,
    user_text: str = "Hello",
    lang: str = "en",
    now: dt.datetime = NOW,
) -> tuple[list[dict[str, Any]], MockAssistantProvider]:
    provider = MockAssistantProvider(script)
    events = _run_provider(salon, provider, user_text=user_text, lang=lang, now=now)
    return events, provider


def _run_provider(
    salon,
    provider: AssistantProvider,
    *,
    user_text: str = "Hello",
    lang: str = "en",
    now: dt.datetime = NOW,
    safety_identifier: str = SAFETY_IDENTIFIER,
) -> list[dict[str, Any]]:
    with tenant_context(salon.id):
        return list(
            run_turn(
                session_id=SESSION_ID,
                user_text=user_text,
                lang=lang,
                now=now,
                provider=provider,
                safety_identifier=safety_identifier,
            )
        )


def _history(salon) -> list[dict[str, str]]:
    with tenant_context(salon.id):
        return load_history(SESSION_ID)


def _tool_messages(provider: MockAssistantProvider, call_index: int) -> list[ChatMessage]:
    messages = provider.calls[call_index]["messages"]
    assert isinstance(messages, tuple)
    return [message for message in messages if message.role == "tool"]


def _tool_results(provider: MockAssistantProvider, call_index: int) -> list[Any]:
    return [json.loads(message.content) for message in _tool_messages(provider, call_index)]


def _status(step: str) -> dict[str, str]:
    return {"type": "status", "step": step}


def test_plain_text_reply_streams_text_then_done_and_stores_the_turn(tbilisi_salon):
    events, _provider = _run(
        tbilisi_salon,
        [[TextDelta(text="Hi! "), TextDelta(text="How can I help?"), _finish()]],
        user_text="Hello",
    )

    assert events == [
        {"type": "text", "text": "Hi! "},
        {"type": "text", "text": "How can I help?"},
        {"type": "done"},
    ]
    assert _history(tbilisi_salon) == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi! How can I help?"},
    ]


def test_tool_round_sees_only_the_bound_salons_data(tbilisi_salon, other_salon, service_category):
    with tenant_context(tbilisi_salon.id):
        Service.objects.create(
            salon=tbilisi_salon,
            category=service_category,
            name={"en": "Own manicure"},
            duration_minutes=60,
            price="500.00",
        )
    with tenant_context(other_salon.id):
        other_category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Nails"})
        Service.objects.create(
            salon=other_salon,
            category=other_category,
            name={"en": "Foreign manicure"},
            duration_minutes=60,
            price="500.00",
        )

    events, provider = _run(
        tbilisi_salon,
        [
            [ToolCall(id="call-1", name="list_services", arguments={}), _finish()],
            [TextDelta(text="We do manicures."), _finish()],
        ],
    )

    [tool_message] = _tool_messages(provider, 1)
    assert tool_message.tool_call_id == "call-1"
    assert "Own manicure" in tool_message.content
    assert "Foreign manicure" not in tool_message.content
    assert events == [
        _status("list_services"),
        {"type": "text", "text": "We do manicures."},
        {"type": "done"},
    ]


def test_check_availability_gets_the_turns_now_and_lang(
    monkeypatch, tbilisi_salon, service, specialist
):
    _monday_hours(tbilisi_salon, specialist)
    received: dict[str, Any] = {}
    spec, real_check_availability = tools_module.TOOLS["check_availability"]

    def _spy(**kwargs: Any) -> dict[str, Any]:
        received.update(kwargs)
        return real_check_availability(**kwargs)

    monkeypatch.setitem(tools_module.TOOLS, "check_availability", (spec, _spy))
    arguments = {
        "service_id": service.id,
        "specialist_id": specialist.id,
        "date_from": "2026-08-17",
        "date_to": "2026-08-17",
    }

    _events, provider = _run(
        tbilisi_salon,
        [
            [ToolCall(id="call-1", name="check_availability", arguments=arguments), _finish()],
            [TextDelta(text="Monday at nine is free."), _finish()],
        ],
        lang="uk",
    )

    assert received["now"] == NOW
    assert received["lang"] == "uk"
    # Monday 17 August is in the past for the real clock, so slots on it
    # can only come from the `now` passed to run_turn.
    [result] = _tool_results(provider, 1)
    assert result["slots"][0] == SLOT_0900


def test_unknown_tool_name_is_an_invalid_tool_call(tbilisi_salon):
    events, provider = _run(
        tbilisi_salon,
        [
            [ToolCall(id="call-1", name="book_now", arguments={}), _finish()],
            [TextDelta(text="Sorry, let me try again."), _finish()],
        ],
    )

    assert _tool_results(provider, 1) == [{"error": "invalid_tool_call"}]
    assert events[-1] == {"type": "done"}


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_salon_info", {"unexpected": 1}),
        (
            "check_availability",
            {"service_id": 1, "specialist_id": "any", "date_from": "2026-08-17"},
        ),
    ],
    ids=["unknown_key", "missing_required_key"],
)
def test_arguments_not_matching_the_schema_are_an_invalid_tool_call(tbilisi_salon, name, arguments):
    events, provider = _run(
        tbilisi_salon,
        [
            [ToolCall(id="call-1", name=name, arguments=arguments), _finish()],
            [TextDelta(text="Let me rephrase."), _finish()],
        ],
    )

    assert _tool_results(provider, 1) == [{"error": "invalid_tool_call"}]
    assert events[-1] == {"type": "done"}


def _propose_call(call_id: str, service, specialist) -> ToolCall:
    return ToolCall(
        id=call_id,
        name="propose_booking",
        arguments={
            "service_id": service.id,
            "specialist_id": specialist.id,
            "slots": [SLOT_0900],
        },
    )


def test_propose_booking_sends_one_proposal_after_all_text_right_before_done(
    tbilisi_salon, service, specialist
):
    _monday_hours(tbilisi_salon, specialist)

    events, provider = _run(
        tbilisi_salon,
        [
            [TextDelta(text="Here is "), _propose_call("call-1", service, specialist), _finish()],
            [TextDelta(text="a card for you."), _finish()],
        ],
    )

    assert [event["type"] for event in events] == ["text", "status", "text", "proposal", "done"]
    card = events[3]["card"]
    assert card["service"]["id"] == service.id
    assert card["specialist"]["id"] == specialist.id
    assert card["slots"] == [SLOT_0900]
    assert _tool_results(provider, 1) == [{"shown": True, "slots": [SLOT_0900], "dropped": []}]


def test_second_propose_booking_in_one_message_is_refused(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)

    events, provider = _run(
        tbilisi_salon,
        [
            [_propose_call("call-1", service, specialist), _finish()],
            [_propose_call("call-2", service, specialist), _finish()],
            [TextDelta(text="Here you go."), _finish()],
        ],
    )

    assert _tool_results(provider, 2)[-1] == {"error": "one_proposal_per_reply"}
    assert [event["type"] for event in events].count("proposal") == 1
    assert events[-1] == {"type": "done"}


def _faq_rounds(count: int) -> list[ScriptTurn]:
    return [
        [ToolCall(id=f"call-{i}", name="get_faq", arguments={}), _finish()] for i in range(count)
    ]


def test_turn_limit_is_ten_provider_calls():
    """Raised from 5 in sub-step 10e (docs/DECISIONS.md § "Sub-step 10e details")."""
    assert orchestrator_module.MAX_PROVIDER_CALLS == 10


def test_turn_limit_after_ten_provider_calls(tbilisi_salon):
    """Was test_turn_limit_after_five_provider_calls; the limit is 10 since 10e.
    The 10th call still asks for tools, so the turn stops there."""
    events, provider = _run(tbilisi_salon, _faq_rounds(10))

    assert len(provider.calls) == 10
    assert events[-1] == {"type": "error", "code": "assistant_turn_limit"}
    assert {"type": "done"} not in events
    assert _history(tbilisi_salon) == []


@pytest.mark.parametrize("tool_rounds", [5, 9], ids=["six_calls", "ten_calls"])
def test_turn_with_room_left_finishes(tbilisi_salon, tool_rounds):
    """Five tool rounds plus the final text is the normal turn that hit the
    old limit; nine plus the final text is the most the limit allows."""
    script = [*_faq_rounds(tool_rounds), [TextDelta(text="Here you go."), _finish()]]

    events, provider = _run(tbilisi_salon, script)

    assert len(provider.calls) == tool_rounds + 1
    assert events[-1] == {"type": "done"}
    assert _history(tbilisi_salon)[-1] == {"role": "assistant", "content": "Here you go."}


def test_provider_error_mid_stream_keeps_the_text_and_stores_nothing(tbilisi_salon):
    events, _provider = _run(
        tbilisi_salon,
        [[TextDelta(text="Let me "), AssistantProviderError()]],
    )

    assert events == [
        {"type": "text", "text": "Let me "},
        {"type": "error", "code": "assistant_provider_error"},
    ]
    assert _history(tbilisi_salon) == []


def test_stored_history_is_sent_before_the_new_message(tbilisi_salon):
    with tenant_context(tbilisi_salon.id):
        append_turn(SESSION_ID, "Do you do brows?", "Yes, we do.")

    _events, provider = _run(
        tbilisi_salon,
        [[TextDelta(text="From 300 UAH."), _finish()]],
        user_text="How much?",
    )

    assert provider.calls[0]["messages"] == (
        ChatMessage(role="user", content="Do you do brows?"),
        ChatMessage(role="assistant", content="Yes, we do."),
        ChatMessage(role="user", content="How much?"),
    )


def test_system_instructions_use_the_salon_local_date(tbilisi_salon):
    # 21:30 UTC on Sunday 16 August is 01:30 on Monday 17 August in Tbilisi.
    late_now = dt.datetime(2026, 8, 16, 21, 30, tzinfo=dt.UTC)

    _events, provider = _run(
        tbilisi_salon, [[TextDelta(text="Hi"), _finish()]], lang="uk", now=late_now
    )

    system = provider.calls[0]["system"]
    assert isinstance(system, str)
    assert system == build_system_instructions(salon=tbilisi_salon, now=late_now, lang="uk")
    assert "Bella Demo Salon" in system
    # Checked on the "Today is" line: since sub-step 10e the rules themselves
    # mention "Sunday" in an example.
    assert "Today is Monday, 2026-08-17," in system
    assert "2026-08-16" not in system
    assert "Today is Sunday" not in system
    assert "uk" in system


def test_provider_gets_the_five_tools_and_propose_booking(tbilisi_salon):
    _events, provider = _run(tbilisi_salon, [[TextDelta(text="Hi"), _finish()]])

    tools = provider.calls[0]["tools"]
    assert isinstance(tools, tuple)
    expected = [spec for spec, _function in tools_module.TOOLS.values()] + [PROPOSE_BOOKING_SPEC]
    assert len(tools) == 6
    assert list(tools) == expected


def test_stored_reply_joins_text_from_every_provider_call(tbilisi_salon):
    _events, _provider = _run(
        tbilisi_salon,
        [
            [
                TextDelta(text="Let me check. "),
                ToolCall(id="call-1", name="get_faq", arguments={}),
                _finish(),
            ],
            [TextDelta(text="Here "), TextDelta(text="it is."), _finish()],
        ],
        user_text="Is there parking?",
    )

    assert _history(tbilisi_salon) == [
        {"role": "user", "content": "Is there parking?"},
        {"role": "assistant", "content": "Let me check. Here it is."},
    ]


def test_one_log_line_with_salon_and_summed_tokens_and_no_text(caplog, tbilisi_salon):
    user_text = "Secret question about my allergy"
    reply_parts = ["Private ", "reply text"]

    with caplog.at_level(logging.DEBUG):
        _run(
            tbilisi_salon,
            [
                [
                    TextDelta(text=reply_parts[0]),
                    ToolCall(id="call-1", name="get_faq", arguments={}),
                    _finish(input_tokens=101, output_tokens=17),
                ],
                [TextDelta(text=reply_parts[1]), _finish(input_tokens=202, output_tokens=28)],
            ],
            user_text=user_text,
        )

    orchestrator_records = [r for r in caplog.records if r.name == "ai_assistant.orchestrator"]
    assert len(orchestrator_records) == 1
    line = orchestrator_records[0].getMessage()
    assert str(tbilisi_salon.id) in line
    assert "303" in line
    assert "45" in line
    for text in [user_text, *reply_parts]:
        assert text not in caplog.text


def test_without_tenant_context_raises_before_calling_the_provider():
    provider = MockAssistantProvider([[TextDelta(text="Hi"), _finish()]])

    with pytest.raises(TenantContextMissingError):
        list(
            run_turn(
                session_id=SESSION_ID,
                user_text="Hello",
                lang="en",
                now=NOW,
                provider=provider,
                safety_identifier=SAFETY_IDENTIFIER,
            )
        )

    assert provider.calls == []


def test_tool_results_keep_non_ascii_characters(tbilisi_salon, service_category):
    with tenant_context(tbilisi_salon.id):
        Service.objects.create(
            salon=tbilisi_salon,
            category=service_category,
            name={"en": "Manicure", "uk": "Манікюр"},
            duration_minutes=60,
            price="500.00",
        )

    _events, provider = _run(
        tbilisi_salon,
        [
            [ToolCall(id="call-1", name="list_services", arguments={}), _finish()],
            [TextDelta(text="Так."), _finish()],
        ],
        lang="uk",
    )

    [tool_message] = _tool_messages(provider, 1)
    assert "Манікюр" in tool_message.content
    assert "\\u" not in tool_message.content


# --- Sub-step 10a: safety_identifier ---------------------------------------


def test_safety_identifier_reaches_every_provider_call(tbilisi_salon):
    provider = MockAssistantProvider(
        [
            [ToolCall(id="call-1", name="get_faq", arguments={}), _finish()],
            [TextDelta(text="No FAQ yet."), _finish()],
        ]
    )

    _run_provider(tbilisi_salon, provider, safety_identifier="c3" * 32)

    assert [call["safety_identifier"] for call in provider.calls] == ["c3" * 32, "c3" * 32]


# --- Sub-step 10a: provider_items ------------------------------------------

REASONING_MARKER = "opaque-reasoning-7f3a"
ITEMS_1 = ({"type": "reasoning", "id": "rs_1", "encrypted_content": REASONING_MARKER},)
ITEMS_2 = ({"type": "reasoning", "id": "rs_2", "encrypted_content": REASONING_MARKER + "-2"},)


def _assistant_messages(provider: MockAssistantProvider, call_index: int) -> list[ChatMessage]:
    messages = provider.calls[call_index]["messages"]
    assert isinstance(messages, tuple)
    return [message for message in messages if message.role == "assistant"]


def _faq_call(call_id: str) -> ToolCall:
    return ToolCall(id=call_id, name="get_faq", arguments={})


def test_provider_items_from_finish_reach_the_next_call(tbilisi_salon):
    _events, provider = _run(
        tbilisi_salon,
        [
            [_faq_call("call-1"), Finish(input_tokens=10, output_tokens=5, provider_items=ITEMS_1)],
            [TextDelta(text="Here it is."), _finish()],
        ],
    )

    [assistant] = _assistant_messages(provider, 1)
    assert assistant.tool_calls == (_faq_call("call-1"),)
    assert assistant.provider_items == ITEMS_1


def test_provider_items_of_each_call_stay_on_its_own_assistant_message(tbilisi_salon):
    _events, provider = _run(
        tbilisi_salon,
        [
            [_faq_call("call-1"), Finish(input_tokens=1, output_tokens=1, provider_items=ITEMS_1)],
            [_faq_call("call-2"), Finish(input_tokens=1, output_tokens=1, provider_items=ITEMS_2)],
            [TextDelta(text="Done."), _finish()],
        ],
    )

    assert [m.provider_items for m in _assistant_messages(provider, 2)] == [ITEMS_1, ITEMS_2]


@pytest.mark.parametrize(
    "first_turn",
    [
        [_faq_call("call-1"), Finish(input_tokens=1, output_tokens=1)],
        [_faq_call("call-1")],
    ],
    ids=["finish_without_items", "no_finish"],
)
def test_nothing_is_replayed_without_provider_items(tbilisi_salon, first_turn):
    _events, provider = _run(tbilisi_salon, [first_turn, [TextDelta(text="Ok."), _finish()]])

    [assistant] = _assistant_messages(provider, 1)
    assert assistant.provider_items == ()


def test_provider_items_are_never_stored_in_the_session(tbilisi_salon, _assistant_redis):
    events, _provider = _run(
        tbilisi_salon,
        [
            [_faq_call("call-1"), Finish(input_tokens=1, output_tokens=1, provider_items=ITEMS_1)],
            [
                TextDelta(text="Here it is."),
                Finish(input_tokens=1, output_tokens=1, provider_items=ITEMS_2),
            ],
        ],
        user_text="Is there parking?",
    )

    assert events[-1] == {"type": "done"}
    assert _history(tbilisi_salon) == [
        {"role": "user", "content": "Is there parking?"},
        {"role": "assistant", "content": "Here it is."},
    ]
    stored = b"".join(
        item for key in _assistant_redis.keys("*") for item in _assistant_redis.lrange(key, 0, -1)
    )
    assert stored
    assert REASONING_MARKER.encode() not in stored


def test_provider_items_are_not_carried_into_the_next_customer_message(tbilisi_salon):
    _run(
        tbilisi_salon,
        [
            [_faq_call("call-1"), Finish(input_tokens=1, output_tokens=1, provider_items=ITEMS_1)],
            [TextDelta(text="Here it is."), _finish()],
        ],
        user_text="Is there parking?",
    )

    _events, provider = _run(
        tbilisi_salon, [[TextDelta(text="Yes."), _finish()]], user_text="And at night?"
    )

    messages = provider.calls[0]["messages"]
    assert isinstance(messages, tuple)
    assert all(message.provider_items == () for message in messages)
    assert REASONING_MARKER not in repr(messages)


# --- Sub-step 10a: 80-second deadline per customer message -----------------


@dataclass(frozen=True)
class _Advance:
    """A script step that moves the fake clock instead of yielding."""

    seconds: float


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _ClockedProvider(AssistantProvider):
    """Plays scripted turns like the mock, but an _Advance step moves the
    fake clock, and a call whose generator is closed early is recorded."""

    def __init__(self, clock: _FakeClock, script: list[list[ProviderEvent | _Advance]]) -> None:
        self._clock = clock
        self._script = script
        self.calls: list[dict[str, Any]] = []
        self.closed_calls: list[int] = []

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        safety_identifier: str,
    ) -> Iterator[ProviderEvent]:
        self.calls.append({"messages": tuple(messages), "safety_identifier": safety_identifier})
        index = len(self.calls) - 1
        try:
            for item in self._script[index]:
                if isinstance(item, _Advance):
                    self._clock.now += item.seconds
                else:
                    yield item
        except GeneratorExit:
            self.closed_calls.append(index)
            raise


@pytest.fixture
def clock(monkeypatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr(orchestrator_module, "monotonic", fake)
    return fake


PROVIDER_ERROR = {"type": "error", "code": "assistant_provider_error"}


def test_deadline_exceeded_mid_stream_stops_and_stores_nothing(tbilisi_salon, clock):
    provider = _ClockedProvider(
        clock,
        [[TextDelta(text="Let me "), _Advance(81), TextDelta(text="late text"), _finish()]],
    )

    events = _run_provider(tbilisi_salon, provider)

    assert events == [{"type": "text", "text": "Let me "}, PROVIDER_ERROR]
    assert _history(tbilisi_salon) == []


def test_deadline_counts_across_all_provider_calls_of_one_message(tbilisi_salon, clock):
    provider = _ClockedProvider(
        clock,
        [
            [TextDelta(text="Checking. "), _faq_call("call-1"), _Advance(50), _finish()],
            [_Advance(31), TextDelta(text="late text"), _finish()],
        ],
    )

    events = _run_provider(tbilisi_salon, provider)

    assert len(provider.calls) == 2
    assert events == [{"type": "text", "text": "Checking. "}, _status("get_faq"), PROVIDER_ERROR]
    assert _history(tbilisi_salon) == []


def test_deadline_closes_the_provider_stream_before_the_error_event(tbilisi_salon, clock):
    provider = _ClockedProvider(
        clock, [[TextDelta(text="Let me "), _Advance(81), TextDelta(text="late"), _finish()]]
    )

    with tenant_context(tbilisi_salon.id):
        events = run_turn(
            session_id=SESSION_ID,
            user_text="Hello",
            lang="en",
            now=NOW,
            provider=provider,
            safety_identifier=SAFETY_IDENTIFIER,
        )
        for event in events:
            if event["type"] == "error":
                # run_turn is paused at the error yield: the provider's
                # stream must already be closed, not left for garbage
                # collection.
                assert provider.closed_calls == [0]
                break
        else:
            pytest.fail("no error event")
        events.close()


def test_exactly_80_seconds_is_still_in_time(tbilisi_salon, clock):
    provider = _ClockedProvider(
        clock,
        [
            [_faq_call("call-1"), _Advance(40), _finish()],
            [_Advance(40), TextDelta(text="Just in time."), _finish()],
        ],
    )

    events = _run_provider(tbilisi_salon, provider, user_text="Parking?")

    assert events == [
        _status("get_faq"),
        {"type": "text", "text": "Just in time."},
        {"type": "done"},
    ]
    assert _history(tbilisi_salon) == [
        {"role": "user", "content": "Parking?"},
        {"role": "assistant", "content": "Just in time."},
    ]


def test_exactly_80_seconds_before_the_next_call_still_starts_it(tbilisi_salon, clock):
    provider = _ClockedProvider(
        clock,
        [
            [_faq_call("call-1"), _finish(), _Advance(80)],
            [TextDelta(text="Just in time."), _finish()],
        ],
    )

    events = _run_provider(tbilisi_salon, provider)

    assert len(provider.calls) == 2
    assert events == [
        _status("get_faq"),
        {"type": "text", "text": "Just in time."},
        {"type": "done"},
    ]


def test_deadline_passed_after_a_calls_last_event_starts_no_further_call(tbilisi_salon, clock):
    provider = _ClockedProvider(
        clock,
        [
            [_faq_call("call-1"), _finish(), _Advance(81)],
            [TextDelta(text="late text"), _finish()],
        ],
    )

    events = _run_provider(tbilisi_salon, provider)

    assert events == [_status("get_faq"), PROVIDER_ERROR]
    assert len(provider.calls) == 1
    assert _history(tbilisi_salon) == []


def test_deadline_is_logged_as_deadline_exceeded_not_provider_error(caplog, tbilisi_salon, clock):
    provider = _ClockedProvider(
        clock, [[TextDelta(text="Let me "), _Advance(81), TextDelta(text="late"), _finish()]]
    )

    with caplog.at_level(logging.INFO):
        events = _run_provider(tbilisi_salon, provider)

    assert events[-1] == PROVIDER_ERROR
    [record] = [r for r in caplog.records if r.name == "ai_assistant.orchestrator"]
    assert "outcome=deadline_exceeded " in record.getMessage()


def test_deadline_drops_a_pending_proposal_and_counts_nothing(
    monkeypatch, tbilisi_salon, service, specialist, clock
):
    _monday_hours(tbilisi_salon, specialist)
    recorded: list[Any] = []
    monkeypatch.setattr(
        orchestrator_module, "record_proposal", lambda **kwargs: recorded.append(kwargs)
    )
    provider = _ClockedProvider(
        clock,
        [
            [_propose_call("call-1", service, specialist), _finish()],
            [_Advance(81), TextDelta(text="Here is a card."), _finish()],
        ],
    )

    events = _run_provider(tbilisi_salon, provider)

    assert events == [_status("propose_booking"), PROVIDER_ERROR]
    assert recorded == []
    assert _history(tbilisi_salon) == []


# --- Stage 17.2a: status events --------------------------------------------


def _tool_arguments(name: str, service, specialist) -> dict[str, Any]:
    if name == "check_availability":
        return {
            "service_id": service.id,
            "specialist_id": specialist.id,
            "date_from": "2026-08-17",
            "date_to": "2026-08-17",
        }
    if name == "propose_booking":
        return {"service_id": service.id, "specialist_id": specialist.id}
    return {}


@pytest.mark.parametrize(
    "name",
    [
        "list_services",
        "check_availability",
        "get_specialist_info",
        "get_faq",
        "get_salon_info",
        "propose_booking",
    ],
)
def test_each_tool_emits_its_step_key(tbilisi_salon, service, specialist, name):
    arguments = _tool_arguments(name, service, specialist)

    events, _provider = _run(
        tbilisi_salon,
        [
            [ToolCall(id="call-1", name=name, arguments=arguments), _finish()],
            [TextDelta(text="Done."), _finish()],
        ],
    )

    assert [event for event in events if event["type"] == "status"] == [_status(name)]


def test_status_arrives_before_its_tool_runs(monkeypatch, tbilisi_salon):
    runs: list[dict[str, Any]] = []
    runs_at_status: list[int] = []
    spec, real_get_faq = tools_module.TOOLS["get_faq"]

    def _spy(**kwargs: Any) -> dict[str, Any]:
        runs.append(kwargs)
        return real_get_faq(**kwargs)

    monkeypatch.setitem(tools_module.TOOLS, "get_faq", (spec, _spy))
    provider = MockAssistantProvider(
        [[_faq_call("call-1"), _finish()], [TextDelta(text="Done."), _finish()]]
    )

    with tenant_context(tbilisi_salon.id):
        for event in run_turn(
            session_id=SESSION_ID,
            user_text="Hello",
            lang="en",
            now=NOW,
            provider=provider,
            safety_identifier=SAFETY_IDENTIFIER,
        ):
            if event["type"] == "status":
                runs_at_status.append(len(runs))

    assert runs_at_status == [0]
    assert len(runs) == 1


def test_two_tools_in_one_call_give_two_statuses_in_order(tbilisi_salon):
    events, _provider = _run(
        tbilisi_salon,
        [
            [
                _faq_call("call-1"),
                ToolCall(id="call-2", name="get_salon_info", arguments={}),
                _finish(),
            ],
            [TextDelta(text="Done."), _finish()],
        ],
    )

    assert [event for event in events if event["type"] == "status"] == [
        _status("get_faq"),
        _status("get_salon_info"),
    ]


def test_turn_limit_round_emits_no_status(tbilisi_salon):
    """The 10th call's tools never run, so nine statuses for ten calls."""
    events, _provider = _run(tbilisi_salon, _faq_rounds(10))

    assert [event for event in events if event["type"] == "status"] == [_status("get_faq")] * 9
    assert events[-1] == {"type": "error", "code": "assistant_turn_limit"}


def test_unknown_tool_name_emits_no_status(tbilisi_salon):
    events, _provider = _run(
        tbilisi_salon,
        [
            [ToolCall(id="call-1", name="book_now", arguments={}), _finish()],
            [TextDelta(text="Sorry."), _finish()],
        ],
    )

    assert [event for event in events if event["type"] == "status"] == []
    assert "book_now" not in json.dumps(events)


# --- Stage 17.2b: cards in history -----------------------------------------

OTHER_SESSION_ID = "orchestrator-other-session-0123456789abcdefghijklmn"


def _run_in_session(
    salon, session_id: str, script: list[ScriptTurn], *, user_text: str = "Hello"
) -> tuple[list[dict[str, Any]], MockAssistantProvider]:
    provider = MockAssistantProvider(script)
    with tenant_context(salon.id):
        events = list(
            run_turn(
                session_id=session_id,
                user_text=user_text,
                lang="en",
                now=NOW,
                provider=provider,
                safety_identifier=SAFETY_IDENTIFIER,
            )
        )
    return events, provider


def _proposal_script(service, specialist) -> list[ScriptTurn]:
    return [
        [_propose_call("call-1", service, specialist), _finish()],
        [TextDelta(text="Here is a card for you."), _finish()],
    ]


def _proposal_card(events: list[dict[str, Any]]) -> dict[str, Any]:
    [card] = [event["card"] for event in events if event["type"] == "proposal"]
    return card


def test_proposal_turn_stores_the_card_on_its_assistant_message(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)

    events, _provider = _run(
        tbilisi_salon, _proposal_script(service, specialist), user_text="Book me in"
    )

    assert _history(tbilisi_salon) == [
        {"role": "user", "content": "Book me in"},
        {"role": "assistant", "content": "Here is a card for you.", "card": _proposal_card(events)},
    ]


def test_next_turn_replays_role_and_content_only_while_the_card_stays_stored(
    tbilisi_salon, service, specialist
):
    _monday_hours(tbilisi_salon, specialist)
    first_events, _provider = _run(
        tbilisi_salon, _proposal_script(service, specialist), user_text="Book me in"
    )

    _events, provider = _run(
        tbilisi_salon, [[TextDelta(text="You're welcome."), _finish()]], user_text="Thanks"
    )

    assert provider.calls[0]["messages"] == (
        ChatMessage(role="user", content="Book me in"),
        ChatMessage(role="assistant", content="Here is a card for you."),
        ChatMessage(role="user", content="Thanks"),
    )
    assert _history(tbilisi_salon)[1]["card"] == _proposal_card(first_events)


def test_card_stays_on_its_own_message_across_two_turns(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)
    events, _provider = _run(
        tbilisi_salon, _proposal_script(service, specialist), user_text="Book me in"
    )
    _run(tbilisi_salon, [[TextDelta(text="You're welcome."), _finish()]], user_text="Thanks")

    assert _history(tbilisi_salon) == [
        {"role": "user", "content": "Book me in"},
        {"role": "assistant", "content": "Here is a card for you.", "card": _proposal_card(events)},
        {"role": "user", "content": "Thanks"},
        {"role": "assistant", "content": "You're welcome."},
    ]


def test_two_sessions_in_one_salon_keep_their_own_cards(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)
    any_call = ToolCall(
        id="call-1",
        name="propose_booking",
        arguments={"service_id": service.id, "specialist_id": "any", "slots": [SLOT_0900]},
    )
    first_events, _provider = _run_in_session(
        tbilisi_salon, SESSION_ID, _proposal_script(service, specialist)
    )
    second_events, _provider = _run_in_session(
        tbilisi_salon,
        OTHER_SESSION_ID,
        [[any_call, _finish()], [TextDelta(text="Any specialist works."), _finish()]],
    )
    first_card = _proposal_card(first_events)
    second_card = _proposal_card(second_events)
    assert first_card["specialist"]["id"] == specialist.id
    assert second_card["specialist"] == "any"

    with tenant_context(tbilisi_salon.id):
        assert load_history(SESSION_ID)[1]["card"] == first_card
        assert load_history(OTHER_SESSION_ID)[1]["card"] == second_card


def test_failed_turn_stores_no_card(tbilisi_salon, service, specialist, _assistant_redis):
    _monday_hours(tbilisi_salon, specialist)
    with tenant_context(tbilisi_salon.id):
        append_turn(SESSION_ID, "Do you do manicures?", "Yes, we do.")

    events, _provider = _run(
        tbilisi_salon,
        [
            [_propose_call("call-1", service, specialist), _finish()],
            [TextDelta(text="Here is "), AssistantProviderError()],
        ],
        user_text="Book me in",
    )

    assert events[-1] == PROVIDER_ERROR
    assert _history(tbilisi_salon) == [
        {"role": "user", "content": "Do you do manicures?"},
        {"role": "assistant", "content": "Yes, we do."},
    ]
    stored = b"".join(
        item for key in _assistant_redis.keys("*") for item in _assistant_redis.lrange(key, 0, -1)
    )
    assert b'"card"' not in stored


# --- Stage 17.2c: blocked safety_identifier ---------------------------------

PROVIDER_MESSAGE = "provider-secret-message-4b1e"
_OPENAI_REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")


def _blocked_error() -> AssistantProviderError:
    """Looked up per test, so a missing class fails each test on its own."""
    return importlib.import_module("core.exceptions").AssistantIdentifierBlockedError()


def _orchestrator_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "ai_assistant.orchestrator"]


def test_blocked_identifier_ends_with_its_own_code_logs_its_outcome_and_stores_nothing(
    caplog, tbilisi_salon, _assistant_redis
):
    with caplog.at_level(logging.INFO):
        events, _provider = _run(tbilisi_salon, [[TextDelta(text="Let me "), _blocked_error()]])

    assert events == [
        {"type": "text", "text": "Let me "},
        {"type": "error", "code": "assistant_identifier_blocked"},
    ]
    [record] = _orchestrator_records(caplog)
    assert "outcome=identifier_blocked " in record.getMessage()
    assert _history(tbilisi_salon) == []
    assert list(_assistant_redis.scan_iter()) == []


class _FakeSdkStream:
    def __init__(self, events: Sequence[Any]) -> None:
        self._events = events

    def __iter__(self) -> Iterator[Any]:
        return iter(self._events)

    def close(self) -> None:
        pass


class _FakeSdkResponses:
    def __init__(self, *, raise_on_create: BaseException | None, events: Sequence[Any]) -> None:
        self._raise_on_create = raise_on_create
        self._events = events

    def create(self, **kwargs: Any) -> _FakeSdkStream:
        if self._raise_on_create is not None:
            raise self._raise_on_create
        return _FakeSdkStream(self._events)


class _FakeSdkClient:
    def __init__(
        self, *, raise_on_create: BaseException | None = None, events: Sequence[Any] = ()
    ) -> None:
        self.responses = _FakeSdkResponses(raise_on_create=raise_on_create, events=events)


def _status_error(status: int, code: str) -> openai.APIStatusError:
    response = httpx2.Response(status, request=_OPENAI_REQUEST)
    body = {"message": PROVIDER_MESSAGE, "type": "invalid_request_error", "code": code}
    if status == 429:
        return openai.RateLimitError(PROVIDER_MESSAGE, response=response, body=body)
    return openai.BadRequestError(PROVIDER_MESSAGE, response=response, body=body)


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        pytest.param(
            _FakeSdkClient(raise_on_create=_status_error(400, "invalid_value")),
            ["http_status=400", "error_code=invalid_value"],
            id="create_400",
        ),
        pytest.param(
            _FakeSdkClient(raise_on_create=_status_error(429, "rate_limit_exceeded")),
            ["http_status=429", "error_code=rate_limit_exceeded"],
            id="create_429",
        ),
        pytest.param(
            _FakeSdkClient(
                events=[
                    ResponseErrorEvent(
                        type="error",
                        message=PROVIDER_MESSAGE,
                        code="server_error",
                        param=None,
                        sequence_number=1,
                    )
                ]
            ),
            ["error_code=server_error"],
            id="error_event",
        ),
    ],
)
def test_provider_error_logs_status_and_code_but_never_the_message(
    caplog, tbilisi_salon, client, expected
):
    provider = OpenAIAssistantProvider(client=client)

    with caplog.at_level(logging.DEBUG):
        events = _run_provider(tbilisi_salon, provider)

    assert events[-1] == PROVIDER_ERROR
    [record] = _orchestrator_records(caplog)
    line = record.getMessage()
    assert "outcome=provider_error " in line
    for part in expected:
        assert f" {part}" in line
    assert PROVIDER_MESSAGE not in caplog.text
