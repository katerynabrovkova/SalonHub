"""
Stage 16 sub-step 10b — OpenAIAssistantProvider, the Responses API adapter
(docs/DECISIONS.md § "Stage 16 planning", "Sub-step 10 details").

Written before ai_assistant/providers/openai.py exists. The module is
imported through `_adapter()` inside each test, so every test fails on its
own with ModuleNotFoundError instead of the whole file failing to collect
(same shape as test_ai_assistant_analytics.py).

Pure unit tests: no database, no network. The adapter takes an injected
client; `_FakeClient.responses.create(**kwargs)` records the request and
returns a `_FakeStream` of real SDK event types (agreed 26.10.2026). The
real SDK stream has `close()`; so does the fake, recording that it ran.

What is pinned here:

- Request: model and reasoning effort from settings; `store=False`,
  `stream=True`, `include=["reasoning.encrypted_content"]`,
  `instructions`, `safety_identifier`, `max_output_tokens=2048`; every
  tool a function with `strict: False`; the client built with the API key,
  `max_retries=0` and a 30-second timeout.
- Input items: user → {"role": "user"}; assistant → its `provider_items`,
  then its text (if any), then one `function_call` per tool call; tool →
  `function_call_output`.
- Events: text and refusal deltas → TextDelta; a finished `function_call`
  item → ToolCall with the `call_id`; `response.completed` → Finish with
  usage and the reasoning items as plain dicts
  (`model_dump(exclude_none=True)`).
- Errors: every `openai.OpenAIError`, the `error` event, `response.failed`,
  `response.incomplete`, malformed or non-object tool arguments, and a
  stream with no `response.completed` → AssistantProviderError.
- The SDK stream is closed in every case, including when the caller
  closes the adapter's generator mid-stream.
"""

import importlib
import inspect
import json
import logging
import socket
from collections.abc import Iterator, Sequence
from types import ModuleType
from typing import Any

import httpx2
import openai
import pytest
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseCreatedEvent,
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionToolCall,
    ResponseIncompleteEvent,
    ResponseInProgressEvent,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseReasoningItem,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseRefusalDeltaEvent,
    ResponseTextDeltaEvent,
)

from ai_assistant.providers.base import (
    AssistantProvider,
    ChatMessage,
    Finish,
    TextDelta,
    ToolCall,
    ToolSpec,
)
from core.exceptions import AssistantProviderError

SYSTEM = "You are the salon assistant."
SAFETY_IDENTIFIER = "5a" * 32
USER_TEXT = "Secret customer words 7f3a"
MESSAGES = [ChatMessage(role="user", content=USER_TEXT)]
TOOLS = [
    ToolSpec(
        name="list_services",
        description="Lists the salon's services.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="get_specialist_info",
        description="One specialist.",
        parameters={
            "type": "object",
            "properties": {"specialist_id": {"type": "integer"}},
            "required": ["specialist_id"],
        },
    ),
]

REASONING_DICT = {
    "type": "reasoning",
    "id": "rs_1",
    "summary": [],
    "encrypted_content": "enc-opaque-1",
}


def _adapter() -> ModuleType:
    return importlib.import_module("ai_assistant.providers.openai")


# --- Fakes ---------------------------------------------------------------


class _FakeStream:
    """Iterates the given events; an exception in the list is raised at
    that point (a mid-stream failure). Records close()."""

    def __init__(self, events: Sequence[Any]) -> None:
        self._events = events
        self.closed = False

    def __iter__(self) -> Iterator[Any]:
        for event in self._events:
            if isinstance(event, BaseException):
                raise event
            yield event

    def close(self) -> None:
        self.closed = True


class _FakeResponses:
    def __init__(self, events: Sequence[Any], raise_on_create: BaseException | None) -> None:
        self._events = events
        self._raise_on_create = raise_on_create
        self.calls: list[dict[str, Any]] = []
        self.streams: list[_FakeStream] = []

    def create(self, **kwargs: Any) -> _FakeStream:
        self.calls.append(kwargs)
        if self._raise_on_create is not None:
            raise self._raise_on_create
        stream = _FakeStream(self._events)
        self.streams.append(stream)
        return stream


class _FakeClient:
    def __init__(
        self, events: Sequence[Any] = (), raise_on_create: BaseException | None = None
    ) -> None:
        self.responses = _FakeResponses(events, raise_on_create)


# --- SDK event builders ------------------------------------------------------


def _response(*, status: str = "completed", output: Sequence[Any] = ()) -> Response:
    return Response.model_validate(
        {
            "id": "resp_1",
            "object": "response",
            "created_at": 0.0,
            "model": "gpt-6-luna",
            "status": status,
            "output": [item.model_dump() for item in output],
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": 120,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "output_tokens": 45,
                "output_tokens_details": {"reasoning_tokens": 10},
                "total_tokens": 165,
            },
        }
    )


def _text(delta: str, seq: int = 1) -> ResponseTextDeltaEvent:
    return ResponseTextDeltaEvent(
        type="response.output_text.delta",
        delta=delta,
        item_id="msg_1",
        output_index=0,
        content_index=0,
        logprobs=[],
        sequence_number=seq,
    )


def _refusal(delta: str) -> ResponseRefusalDeltaEvent:
    return ResponseRefusalDeltaEvent(
        type="response.refusal.delta",
        delta=delta,
        item_id="msg_1",
        output_index=0,
        content_index=0,
        sequence_number=1,
    )


def _function_call(arguments: str = '{"specialist_id": 7}') -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        type="function_call",
        id="fc_item_1",
        call_id="call_abc",
        name="get_specialist_info",
        arguments=arguments,
        status="completed",
    )


def _reasoning() -> ResponseReasoningItem:
    return ResponseReasoningItem(
        type="reasoning", id="rs_1", summary=[], encrypted_content="enc-opaque-1"
    )


def _item_done(item: Any, seq: int = 2) -> ResponseOutputItemDoneEvent:
    return ResponseOutputItemDoneEvent(
        type="response.output_item.done", item=item, output_index=0, sequence_number=seq
    )


def _completed(output: Sequence[Any] = ()) -> ResponseCompletedEvent:
    return ResponseCompletedEvent(
        type="response.completed", response=_response(output=output), sequence_number=99
    )


def _error_event() -> ResponseErrorEvent:
    return ResponseErrorEvent(
        type="error", message="boom", code=None, param=None, sequence_number=3
    )


def _failed() -> ResponseFailedEvent:
    return ResponseFailedEvent(
        type="response.failed", response=_response(status="failed"), sequence_number=3
    )


def _incomplete() -> ResponseIncompleteEvent:
    return ResponseIncompleteEvent(
        type="response.incomplete", response=_response(status="incomplete"), sequence_number=3
    )


_REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")


def _sdk_error(kind: str) -> openai.OpenAIError:
    if kind == "connection":
        return openai.APIConnectionError(request=_REQUEST)
    if kind == "timeout":
        return openai.APITimeoutError(request=_REQUEST)
    if kind == "rate_limit":
        return openai.RateLimitError(
            "rate limited", response=httpx2.Response(429, request=_REQUEST), body=None
        )
    return openai.InternalServerError(
        "server error", response=httpx2.Response(500, request=_REQUEST), body=None
    )


# --- Helpers -------------------------------------------------------------------


def _provider(client: _FakeClient) -> AssistantProvider:
    return _adapter().OpenAIAssistantProvider(client=client)


def _stream(provider: AssistantProvider, messages: Sequence[ChatMessage] = MESSAGES):
    return provider.stream(
        system=SYSTEM, messages=messages, tools=TOOLS, safety_identifier=SAFETY_IDENTIFIER
    )


def _request(events: Sequence[Any] = (), **stream_kwargs: Any) -> dict[str, Any]:
    """Runs one full stream() and returns the kwargs sent to create()."""
    client = _FakeClient([*events, _completed()])
    list(_stream(_provider(client), **stream_kwargs))
    assert len(client.responses.calls) == 1
    return client.responses.calls[0]


def _collect(iterator: Iterator[Any]) -> tuple[list[Any], BaseException | None]:
    """The events yielded before a failure, and the failure itself."""
    events: list[Any] = []
    try:
        for event in iterator:
            events.append(event)
    except BaseException as exc:
        return events, exc
    return events, None


# --- Class shape -----------------------------------------------------------


def test_openai_provider_is_assistant_provider_with_keyword_only_stream():
    cls = _adapter().OpenAIAssistantProvider
    assert issubclass(cls, AssistantProvider)
    params = inspect.signature(cls.stream).parameters
    assert set(params) - {"self"} == {"system", "messages", "tools", "safety_identifier"}
    for name in ("system", "messages", "tools", "safety_identifier"):
        assert params[name].kind == inspect.Parameter.KEYWORD_ONLY


# --- Request shape ---------------------------------------------------------


def test_sends_model_and_reasoning_effort_from_settings(settings):
    settings.ASSISTANT_OPENAI_MODEL = "gpt-test-model"
    settings.ASSISTANT_OPENAI_REASONING_EFFORT = "medium"
    kwargs = _request()
    assert kwargs["model"] == "gpt-test-model"
    assert kwargs["reasoning"] == {"effort": "medium"}


def test_sends_store_false_and_stream_true():
    kwargs = _request()
    assert kwargs["store"] is False
    assert kwargs["stream"] is True


def test_sends_system_as_instructions():
    assert _request()["instructions"] == SYSTEM


def test_forwards_safety_identifier_unchanged():
    assert _request()["safety_identifier"] == SAFETY_IDENTIFIER


def test_sends_max_output_tokens_2048():
    assert _request()["max_output_tokens"] == 2048


def test_sends_include_reasoning_encrypted_content():
    assert _request()["include"] == ["reasoning.encrypted_content"]


def test_sends_every_tool_as_non_strict_function():
    assert _request()["tools"] == [
        {
            "type": "function",
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
            "strict": False,
        }
        for spec in TOOLS
    ]


def _decoded(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """function_call arguments are a JSON string; compare them decoded."""
    return [
        {**item, "arguments": json.loads(item["arguments"])}
        if item.get("type") == "function_call"
        else item
        for item in items
    ]


def test_translates_messages_into_input_items():
    messages = [
        ChatMessage(role="user", content="Hi"),
        ChatMessage(role="assistant", content="Hello! How can I help?"),
        ChatMessage(role="user", content="Who is Olena?"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=(
                ToolCall(id="call_1", name="get_specialist_info", arguments={"specialist_id": 7}),
            ),
        ),
        ChatMessage(role="tool", content='{"name": "Олена"}', tool_call_id="call_1"),
    ]
    kwargs = _request(messages=messages)
    assert _decoded(kwargs["input"]) == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello! How can I help?"},
        {"role": "user", "content": "Who is Olena?"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "get_specialist_info",
            "arguments": {"specialist_id": 7},
        },
        {"type": "function_call_output", "call_id": "call_1", "output": '{"name": "Олена"}'},
    ]


def test_replays_provider_items_before_the_assistant_text_and_function_calls():
    other_reasoning = {**REASONING_DICT, "id": "rs_2", "encrypted_content": "enc-opaque-2"}
    messages = [
        ChatMessage(role="user", content="Book me"),
        ChatMessage(
            role="assistant",
            content="Let me check.",
            tool_calls=(
                ToolCall(id="call_1", name="list_services", arguments={}),
                ToolCall(id="call_2", name="get_specialist_info", arguments={"specialist_id": 3}),
            ),
            provider_items=(REASONING_DICT, other_reasoning),
        ),
        ChatMessage(role="tool", content="[]", tool_call_id="call_1"),
        ChatMessage(role="tool", content="{}", tool_call_id="call_2"),
    ]
    kwargs = _request(messages=messages)
    assert _decoded(kwargs["input"]) == [
        {"role": "user", "content": "Book me"},
        REASONING_DICT,
        other_reasoning,
        {"role": "assistant", "content": "Let me check."},
        {"type": "function_call", "call_id": "call_1", "name": "list_services", "arguments": {}},
        {
            "type": "function_call",
            "call_id": "call_2",
            "name": "get_specialist_info",
            "arguments": {"specialist_id": 3},
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "[]"},
        {"type": "function_call_output", "call_id": "call_2", "output": "{}"},
    ]


def test_builds_client_with_api_key_no_retries_and_30_second_timeout(settings, monkeypatch):
    settings.ASSISTANT_OPENAI_API_KEY = "sk-test-key"
    module = _adapter()
    built: list[dict[str, Any]] = []

    def _fake_openai(**kwargs: Any) -> _FakeClient:
        built.append(kwargs)
        return _FakeClient()

    monkeypatch.setattr(module, "OpenAI", _fake_openai)
    module.OpenAIAssistantProvider()
    assert built == [{"api_key": "sk-test-key", "max_retries": 0, "timeout": 30}]


# --- Event translation -----------------------------------------------------


def test_output_text_delta_becomes_text_delta():
    client = _FakeClient([_text("Так, "), _text("робимо."), _completed()])
    events = list(_stream(_provider(client)))
    assert events[:2] == [TextDelta(text="Так, "), TextDelta(text="робимо.")]


def test_refusal_delta_becomes_text_delta():
    client = _FakeClient([_refusal("I can't help with that."), _completed()])
    events = list(_stream(_provider(client)))
    assert events[0] == TextDelta(text="I can't help with that.")


def test_finished_function_call_becomes_tool_call_with_call_id():
    call = _function_call()
    client = _FakeClient([_item_done(call), _completed(output=[call])])
    events = list(_stream(_provider(client)))
    # Exactly one ToolCall: the completed response's output must not repeat it.
    assert [e for e in events if isinstance(e, ToolCall)] == [
        ToolCall(id="call_abc", name="get_specialist_info", arguments={"specialist_id": 7})
    ]


def test_completed_becomes_finish_with_usage_and_reasoning_items():
    reasoning = _reasoning()
    call = _function_call()
    client = _FakeClient(
        [_item_done(reasoning, seq=1), _item_done(call), _completed(output=[reasoning, call])]
    )
    events = list(_stream(_provider(client)))
    assert events[-1] == Finish(
        input_tokens=120, output_tokens=45, provider_items=(REASONING_DICT,)
    )


def test_finish_has_no_provider_items_without_reasoning():
    client = _FakeClient([_text("Hi"), _completed()])
    events = list(_stream(_provider(client)))
    assert events[-1] == Finish(input_tokens=120, output_tokens=45, provider_items=())


def test_unrelated_events_are_ignored():
    client = _FakeClient(
        [
            ResponseCreatedEvent(
                type="response.created",
                response=_response(status="in_progress"),
                sequence_number=0,
            ),
            ResponseInProgressEvent(
                type="response.in_progress",
                response=_response(status="in_progress"),
                sequence_number=1,
            ),
            ResponseReasoningSummaryTextDeltaEvent(
                type="response.reasoning_summary_text.delta",
                delta="thinking",
                item_id="rs_1",
                output_index=0,
                summary_index=0,
                sequence_number=2,
            ),
            ResponseOutputItemAddedEvent(
                type="response.output_item.added",
                item=_function_call(arguments=""),
                output_index=1,
                sequence_number=3,
            ),
            ResponseFunctionCallArgumentsDeltaEvent(
                type="response.function_call_arguments.delta",
                delta='{"specialist_id"',
                item_id="fc_item_1",
                output_index=1,
                sequence_number=4,
            ),
            _text("Hi", seq=5),
            _completed(),
        ]
    )
    events = list(_stream(_provider(client)))
    assert events == [TextDelta(text="Hi"), Finish(input_tokens=120, output_tokens=45)]


# --- Errors ------------------------------------------------------------------


def test_malformed_tool_arguments_raise_provider_error():
    call = _function_call(arguments='{"specialist_id": 7')
    client = _FakeClient([_item_done(call), _completed(output=[call])])
    _events, exc = _collect(_stream(_provider(client)))
    assert isinstance(exc, AssistantProviderError)


def test_non_object_tool_arguments_raise_provider_error():
    call = _function_call(arguments="[7]")
    client = _FakeClient([_item_done(call), _completed(output=[call])])
    _events, exc = _collect(_stream(_provider(client)))
    assert isinstance(exc, AssistantProviderError)


@pytest.mark.parametrize("kind", ["connection", "timeout", "rate_limit", "server_error"])
def test_sdk_error_at_create_raises_provider_error(kind):
    client = _FakeClient(raise_on_create=_sdk_error(kind))
    _events, exc = _collect(_stream(_provider(client)))
    assert isinstance(exc, AssistantProviderError)


def test_sdk_error_mid_stream_raises_provider_error_after_streamed_text():
    client = _FakeClient([_text("Part"), _sdk_error("connection")])
    events, exc = _collect(_stream(_provider(client)))
    assert events == [TextDelta(text="Part")]
    assert isinstance(exc, AssistantProviderError)


def test_error_event_raises_provider_error():
    client = _FakeClient([_error_event()])
    _events, exc = _collect(_stream(_provider(client)))
    assert isinstance(exc, AssistantProviderError)


def test_response_failed_raises_provider_error():
    client = _FakeClient([_failed()])
    _events, exc = _collect(_stream(_provider(client)))
    assert isinstance(exc, AssistantProviderError)


def test_response_incomplete_after_text_raises_provider_error():
    client = _FakeClient([_text("Half a rep"), _incomplete()])
    events, exc = _collect(_stream(_provider(client)))
    assert events == [TextDelta(text="Half a rep")]
    assert isinstance(exc, AssistantProviderError)


def test_stream_ending_without_completed_raises_provider_error():
    client = _FakeClient([_text("Hi")])
    events, exc = _collect(_stream(_provider(client)))
    assert events == [TextDelta(text="Hi")]
    assert isinstance(exc, AssistantProviderError)


def test_adapter_logs_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    ok = _FakeClient([_text(USER_TEXT), _completed()])
    list(_stream(_provider(ok)))
    failing = _FakeClient([_text(USER_TEXT), _error_event()])
    _collect(_stream(_provider(failing)))
    assert caplog.records == []


# --- Stream closing ----------------------------------------------------------


def test_closing_the_generator_mid_stream_closes_the_sdk_stream():
    client = _FakeClient([_text("one"), _text("two"), _completed()])
    generator = _stream(_provider(client))
    assert next(generator) == TextDelta(text="one")
    generator.close()
    assert client.responses.streams[0].closed is True


def test_sdk_stream_is_closed_after_normal_completion():
    client = _FakeClient([_text("Hi"), _completed()])
    list(_stream(_provider(client)))
    assert client.responses.streams[0].closed is True


def test_sdk_stream_is_closed_after_error_event():
    client = _FakeClient([_text("Hi"), _error_event()])
    _collect(_stream(_provider(client)))
    assert client.responses.streams[0].closed is True


# --- Network -------------------------------------------------------------------


def test_stream_makes_no_network_call(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("OpenAIAssistantProvider must not touch the network in tests")

    monkeypatch.setattr(socket.socket, "connect", _fail)
    monkeypatch.setattr(socket, "create_connection", _fail)

    call = _function_call()
    client = _FakeClient([_text("Hi"), _item_done(call), _completed(output=[call])])
    events = list(_stream(_provider(client)))
    assert isinstance(events[-1], Finish)
