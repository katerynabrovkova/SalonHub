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
import json
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import pytest

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
    assert events == [{"type": "text", "text": "We do manicures."}, {"type": "done"}]


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

    assert [event["type"] for event in events] == ["text", "text", "proposal", "done"]
    card = events[2]["card"]
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


def test_turn_limit_after_five_provider_calls(tbilisi_salon):
    script: list[ScriptTurn] = [
        [ToolCall(id=f"call-{i}", name="get_faq", arguments={}), _finish()] for i in range(5)
    ]

    events, provider = _run(tbilisi_salon, script)

    assert len(provider.calls) == 5
    assert events[-1] == {"type": "error", "code": "assistant_turn_limit"}
    assert {"type": "done"} not in events
    assert _history(tbilisi_salon) == []


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
    assert "2026-08-17" in system
    assert "Monday" in system
    assert "2026-08-16" not in system
    assert "Sunday" not in system
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
    assert events == [{"type": "text", "text": "Checking. "}, PROVIDER_ERROR]
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

    assert events == [{"type": "text", "text": "Just in time."}, {"type": "done"}]
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
    assert events == [{"type": "text", "text": "Just in time."}, {"type": "done"}]


def test_deadline_passed_after_a_calls_last_event_starts_no_further_call(tbilisi_salon, clock):
    provider = _ClockedProvider(
        clock,
        [
            [_faq_call("call-1"), _finish(), _Advance(81)],
            [TextDelta(text="late text"), _finish()],
        ],
    )

    events = _run_provider(tbilisi_salon, provider)

    assert events == [PROVIDER_ERROR]
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

    assert events == [PROVIDER_ERROR]
    assert recorded == []
    assert _history(tbilisi_salon) == []
