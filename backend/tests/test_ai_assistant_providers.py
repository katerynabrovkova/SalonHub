"""
Stage 16 sub-step 3 — AssistantProvider interface + MockAssistantProvider
(docs/DECISIONS.md § "Stage 16 planning", "Sub-step 3 details").

Pure unit tests: this layer never touches the database or the network, so
nothing here is @pytest.mark.django_db or uses fixtures from conftest.py.

Signature under test:

    AssistantProvider.stream(*, system: str, messages: Sequence[ChatMessage],
                             tools: Sequence[ToolSpec]) -> Iterator[ProviderEvent]

Provider events are TextDelta(text), ToolCall(id, name, arguments) and
Finish(input_tokens, output_tokens). The mock is built with a script, a
list of turns; each stream() call plays the next turn, and an exception in
a turn is raised at that point. A call after the last turn raises
MockScriptExhaustedError.
"""

import dataclasses
import inspect
import re
import socket

import pytest
from django.apps import apps

from ai_assistant.providers import get_assistant_provider
from ai_assistant.providers.base import (
    AssistantProvider,
    ChatMessage,
    Finish,
    TextDelta,
    ToolCall,
    ToolSpec,
)
from ai_assistant.providers.mock import MockAssistantProvider, MockScriptExhaustedError
from core.exceptions import AssistantProviderError, DomainError

SYSTEM = "You are the salon assistant."
MESSAGES = [ChatMessage(role="user", content="Do you do pedicure?")]
TOOLS = [
    ToolSpec(
        name="list_services",
        description="Lists the salon's services.",
        parameters={"type": "object", "properties": {}},
    )
]

TURN_1 = [
    ToolCall(id="call_1", name="list_services", arguments={}),
    Finish(input_tokens=10, output_tokens=5),
]
TURN_2 = [
    TextDelta(text="Yes, "),
    TextDelta(text="we do."),
    Finish(input_tokens=20, output_tokens=7),
]


class _Boom(Exception):
    """Stand-in for a provider failure in the middle of a stream."""


def _stream(provider, *, system=SYSTEM, messages=MESSAGES, tools=TOOLS):
    return list(provider.stream(system=system, messages=messages, tools=tools))


# --- Interface shape -------------------------------------------------------


def test_assistant_provider_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        AssistantProvider()


def test_assistant_provider_abstract_methods_is_exactly_stream():
    assert AssistantProvider.__abstractmethods__ == frozenset({"stream"})


def test_stream_signature_is_keyword_only():
    params = inspect.signature(AssistantProvider.stream).parameters
    assert set(params) - {"self"} == {"system", "messages", "tools"}
    for name in ("system", "messages", "tools"):
        assert params[name].kind == inspect.Parameter.KEYWORD_ONLY


def test_dataclasses_are_frozen():
    event = TextDelta(text="hi")
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.text = "changed"

    message = ChatMessage(role="user", content="hi")
    with pytest.raises(dataclasses.FrozenInstanceError):
        message.content = "changed"


# --- MockAssistantProvider: scripted turns ---------------------------------


def test_mock_plays_turns_in_order():
    provider = MockAssistantProvider(script=[TURN_1, TURN_2])

    assert _stream(provider) == TURN_1
    assert _stream(provider) == TURN_2


def test_mock_records_each_call():
    provider = MockAssistantProvider(script=[TURN_1, TURN_2])
    second_messages = [
        *MESSAGES,
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=(ToolCall(id="call_1", name="list_services", arguments={}),),
        ),
        ChatMessage(role="tool", content='["Pedicure"]', tool_call_id="call_1"),
    ]

    _stream(provider)
    _stream(provider, system="Second system", messages=second_messages, tools=[])

    assert provider.calls == [
        {"system": SYSTEM, "messages": tuple(MESSAGES), "tools": tuple(TOOLS)},
        {"system": "Second system", "messages": tuple(second_messages), "tools": ()},
    ]


def test_mock_records_a_snapshot_of_messages():
    messages = list(MESSAGES)
    provider = MockAssistantProvider(script=[TURN_1])

    _stream(provider, messages=messages)
    messages.append(ChatMessage(role="user", content="And manicure?"))

    assert provider.calls[0]["messages"] == tuple(MESSAGES)


def test_mock_raises_scripted_exception_mid_stream():
    boom = _Boom("provider failed")
    provider = MockAssistantProvider(script=[[TextDelta(text="Yes"), boom, Finish(1, 1)]])

    received = []
    with pytest.raises(_Boom) as excinfo:
        for event in provider.stream(system=SYSTEM, messages=MESSAGES, tools=TOOLS):
            received.append(event)

    assert excinfo.value is boom
    assert received == [TextDelta(text="Yes")]


def test_mock_raises_clear_error_when_script_is_exhausted():
    provider = MockAssistantProvider(script=[TURN_1])
    _stream(provider)

    expected = (
        "MockAssistantProvider script exhausted: stream() call 2 but the script has 1 turn(s)."
    )
    with pytest.raises(MockScriptExhaustedError, match=re.escape(expected)):
        _stream(provider)


# --- MockAssistantProvider: default reply ----------------------------------


def test_mock_without_script_gives_one_text_delta_then_finish():
    events = _stream(MockAssistantProvider())

    assert len(events) == 2
    assert isinstance(events[0], TextDelta)
    assert events[0].text
    assert isinstance(events[1], Finish)


# --- MockAssistantProvider: no network -------------------------------------


def test_mock_makes_no_network_call(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("MockAssistantProvider must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", _fail)
    monkeypatch.setattr(socket, "create_connection", _fail)

    assert _stream(MockAssistantProvider(script=[TURN_2])) == TURN_2
    assert isinstance(_stream(MockAssistantProvider())[-1], Finish)


# --- Factory, error, app ----------------------------------------------------


def test_get_assistant_provider_returns_mock():
    assert isinstance(get_assistant_provider(), MockAssistantProvider)


def test_assistant_provider_error_shape():
    assert issubclass(AssistantProviderError, DomainError)
    assert AssistantProviderError.code == "assistant_provider_error"
    assert AssistantProviderError.status_code == 502


def test_ai_assistant_app_is_registered():
    assert apps.get_app_config("ai_assistant").name == "ai_assistant"
