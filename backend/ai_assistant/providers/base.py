"""
Stage 16 sub-step 3 — AssistantProvider interface (docs/DECISIONS.md §
"Stage 16 planning", "Sub-step 3 details"). Mirrors
payments/providers/base.py: one abstract method, plain values in and out.

A provider is a dumb pipe for one model turn. `stream` sends the system
instructions, the conversation so far and the tool specs to the model and
yields provider events (TextDelta, ToolCall, Finish) as they arrive. It
never executes tools, knows nothing about salons or tenant context, and
never logs request or response content. These events are internal to the
orchestrator; the widget only ever sees the client events (text,
proposal, error, done). Vendor formats are translated inside adapters
only.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class TextDelta:
    """A chunk of the model's reply text."""

    text: str


@dataclass(frozen=True)
class ToolCall:
    """
    The model asks for one tool call. `arguments` is the already-decoded
    JSON object; an adapter whose vendor sends a JSON string decodes it.
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Finish:
    """The model turn ended. Token counts are for logging only."""

    input_tokens: int
    output_tokens: int


ProviderEvent = TextDelta | ToolCall | Finish


@dataclass(frozen=True)
class ChatMessage:
    """
    One message of the conversation sent to the model. A "tool" message
    carries a tool result and the `tool_call_id` it answers; an "assistant"
    message carries the `tool_calls` the model made in that turn, if any.
    """

    role: Literal["user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. `parameters` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


class AssistantProvider(ABC):
    @abstractmethod
    def stream(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
    ) -> Iterator[ProviderEvent]:
        """
        Yields the events of one model turn, ending with Finish. A real
        adapter wraps any vendor failure, including one mid-stream, into
        core.exceptions.AssistantProviderError.
        """
