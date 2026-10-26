"""
Stage 16 sub-step 10b — OpenAIAssistantProvider, the Responses API adapter
(docs/DECISIONS.md § "Stage 16 planning", "Sub-step 10 details").

One `stream()` call is one Responses request with `store=False`, streamed.
Our plain values are translated here and nowhere else:

- Input: a user message as is; an assistant message as its replayed
  `provider_items` (reasoning items), then its text, then one
  `function_call` per tool call; a tool message as `function_call_output`.
- Tools: every ToolSpec as a function with `strict: False`, so optional
  arguments are not forced to arrive as null.
- Events: text and refusal deltas → TextDelta; a finished `function_call`
  item → ToolCall with its `call_id`; `response.completed` → Finish with
  the usage and the reasoning items as plain dicts.

Every vendor failure becomes AssistantProviderError: an `openai.OpenAIError`
at the start or mid-stream, the `error` event, `response.failed`,
`response.incomplete`, tool arguments that are not a JSON object, and a
stream that ends without `response.completed`. Nothing else is wrapped.
The SDK stream is always closed, including when the caller closes this
generator (deadline or customer gone). Nothing is logged.

Imported only by get_assistant_provider() when ASSISTANT_PROVIDER is
"openai", so the SDK is never loaded with the mock.
"""

import json
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, cast

import openai
from django.conf import settings
from openai import OpenAI
from openai.types.responses import (
    ResponseCompletedEvent,
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseFunctionToolCall,
    ResponseIncompleteEvent,
    ResponseInputParam,
    ResponseOutputItemDoneEvent,
    ResponseReasoningItem,
    ResponseRefusalDeltaEvent,
    ResponseStreamEvent,
    ResponseTextDeltaEvent,
    ToolParam,
)
from openai.types.shared_params import Reasoning

from ai_assistant.providers.base import (
    AssistantProvider,
    ChatMessage,
    Finish,
    ProviderEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
)
from core.exceptions import AssistantProviderError

# Constants, not settings: deadline (80 s) + TIMEOUT_SECONDS must stay under
# the stream cap's 120-second stale cutoff (docs/DECISIONS.md § "Sub-step
# 10 details", "Timeouts"). The SDK's stream timeout applies between chunks.
TIMEOUT_SECONDS = 30
MAX_RETRIES = 0
MAX_OUTPUT_TOKENS = 2048


def _input_items(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "user":
            items.append({"role": "user", "content": message.content})
        elif message.role == "assistant":
            items.extend(message.provider_items)
            if message.content or not message.tool_calls:
                items.append({"role": "assistant", "content": message.content})
            items.extend(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                }
                for call in message.tool_calls
            )
        else:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                }
            )
    return items


def _tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "name": spec.name,
        "description": spec.description,
        "parameters": spec.parameters,
        "strict": False,
    }


def _arguments(raw: str) -> dict[str, Any]:
    """The model's arguments JSON; anything but an object is a provider
    failure. The raw text is never put into the error."""
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise AssistantProviderError() from exc
    if not isinstance(value, dict):
        raise AssistantProviderError()
    return value


def _finish(event: ResponseCompletedEvent) -> Finish:
    response = event.response
    usage = response.usage
    return Finish(
        input_tokens=usage.input_tokens if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
        provider_items=tuple(
            item.model_dump(exclude_none=True)
            for item in response.output
            if isinstance(item, ResponseReasoningItem)
        ),
    )


def _translate(sdk_events: Iterable[ResponseStreamEvent]) -> Iterator[ProviderEvent]:
    for event in sdk_events:
        if isinstance(event, ResponseTextDeltaEvent | ResponseRefusalDeltaEvent):
            yield TextDelta(text=event.delta)
        elif isinstance(event, ResponseOutputItemDoneEvent) and isinstance(
            event.item, ResponseFunctionToolCall
        ):
            yield ToolCall(
                id=event.item.call_id,
                name=event.item.name,
                arguments=_arguments(event.item.arguments),
            )
        elif isinstance(event, ResponseCompletedEvent):
            yield _finish(event)
            return
        elif isinstance(event, ResponseErrorEvent | ResponseFailedEvent | ResponseIncompleteEvent):
            raise AssistantProviderError()
    # The turn must end with Finish.
    raise AssistantProviderError()


class OpenAIAssistantProvider(AssistantProvider):
    def __init__(self, *, client: OpenAI | None = None) -> None:
        self._client = client or OpenAI(
            api_key=settings.ASSISTANT_OPENAI_API_KEY,
            max_retries=MAX_RETRIES,
            timeout=TIMEOUT_SECONDS,
        )

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        safety_identifier: str,
    ) -> Iterator[ProviderEvent]:
        # Our items and tools are plain dicts in the documented shapes; the
        # casts only meet the SDK's TypedDict parameter types.
        try:
            sdk_stream = self._client.responses.create(
                model=settings.ASSISTANT_OPENAI_MODEL,
                reasoning=cast(Reasoning, {"effort": settings.ASSISTANT_OPENAI_REASONING_EFFORT}),
                instructions=system,
                input=cast(ResponseInputParam, _input_items(messages)),
                tools=cast(list[ToolParam], [_tool(spec) for spec in tools]),
                store=False,
                stream=True,
                # Legacy per OpenAI's reasoning guide, still described as
                # needed by the API reference; keeps encrypted_content.
                include=["reasoning.encrypted_content"],
                max_output_tokens=MAX_OUTPUT_TOKENS,
                safety_identifier=safety_identifier,
            )
        except openai.OpenAIError as exc:
            raise AssistantProviderError() from exc
        try:
            yield from _translate(sdk_stream)
        except openai.OpenAIError as exc:
            raise AssistantProviderError() from exc
        finally:
            # Also runs on GeneratorExit, so the vendor stops generating.
            sdk_stream.close()
