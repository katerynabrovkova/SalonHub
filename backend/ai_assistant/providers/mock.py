"""
Stage 16 sub-step 3 — MockAssistantProvider (docs/DECISIONS.md § "Stage 16
planning", "Sub-step 3 details"). Built first, before any real adapter.
Never touches the network and logs nothing.

Built with a script: a list of turns. Each stream() call plays the next
turn; a turn is a list of provider events and may contain an exception,
raised at that point (a mid-stream failure). Every call is recorded for
assertions. Without a script it gives one neutral reply, so local dev
endpoints work before the real provider exists.
"""

from collections.abc import Iterator, Sequence

from ai_assistant.providers.base import (
    AssistantProvider,
    ChatMessage,
    Finish,
    ProviderEvent,
    TextDelta,
    ToolSpec,
)

ScriptTurn = Sequence[ProviderEvent | Exception]

_DEFAULT_REPLY = "This is a test reply from the mock assistant."


class MockScriptExhaustedError(Exception):
    """stream() was called after every scripted turn had been played."""


class MockAssistantProvider(AssistantProvider):
    def __init__(self, script: Sequence[ScriptTurn] | None = None) -> None:
        self._script = script
        self._next_turn = 0
        self.calls: list[dict[str, object]] = []

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
    ) -> Iterator[ProviderEvent]:
        # Snapshots, not references: the caller keeps appending to its own
        # message list between turns, and the record must show what this
        # call actually received.
        self.calls.append({"system": system, "messages": tuple(messages), "tools": tuple(tools)})

        if self._script is None:
            yield TextDelta(text=_DEFAULT_REPLY)
            yield Finish(input_tokens=0, output_tokens=0)
            return

        if self._next_turn >= len(self._script):
            raise MockScriptExhaustedError(
                f"MockAssistantProvider script exhausted: stream() call {len(self.calls)} "
                f"but the script has {len(self._script)} turn(s)."
            )
        turn = self._script[self._next_turn]
        self._next_turn += 1

        for item in turn:
            if isinstance(item, Exception):
                raise item
            yield item
