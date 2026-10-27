"""
Stage 16 sub-step 10d — one INFO log line per tool call (docs/DECISIONS.md §
"Sub-step 10d details").

Logger `ai_assistant.orchestrator.tools`. Line: "assistant tool:
salon_id=<id> tool=<name> outcome=<ok or error code>", then for
check_availability `slots=<n>` and, when empty, `reason=<reason>`, and for
propose_booking `passed=<n> dropped=<n> shown=<n>`. An unknown tool name is
logged as `tool=unknown`. Never message text, argument values or the session
id (docs/DECISIONS.md § "Safety and privacy").

The salon runs on Asia/Tbilisi; the specialist works Monday 2026-08-17
09:00-18:00 and `now` is a fixed literal before it.
"""

import datetime as dt
import logging
from typing import Any

import pytest

from ai_assistant.orchestrator import run_turn
from ai_assistant.providers.base import Finish, TextDelta, ToolCall
from ai_assistant.providers.mock import MockAssistantProvider, ScriptTurn
from core.tenancy import tenant_context
from tests.conftest import make_working_hours

pytestmark = pytest.mark.django_db

LOGGER = "ai_assistant.orchestrator.tools"
SESSION_ID = "tool-logging-test-session-0123456789abcdefghijklmnop"
NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=dt.UTC)
MONDAY = dt.date(2026, 8, 17)
SLOT_0900 = "2026-08-17T09:00:00+04:00"


@pytest.fixture
def tbilisi_salon(salon):
    salon.timezone = "Asia/Tbilisi"
    salon.save(update_fields=["timezone"])
    return salon


@pytest.fixture
def monday_specialist(tbilisi_salon, service, specialist):
    make_working_hours(
        salon=tbilisi_salon,
        specialist=specialist,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9),
        end_time=dt.time(18),
    )
    return specialist


def _finish() -> Finish:
    return Finish(input_tokens=10, output_tokens=5)


def _tool_turn(*calls: ToolCall) -> ScriptTurn:
    return [*calls, _finish()]


def _run(salon, script: list[ScriptTurn], *, user_text: str = "Hello") -> None:
    provider = MockAssistantProvider(script)
    with tenant_context(salon.id):
        list(
            run_turn(
                session_id=SESSION_ID,
                user_text=user_text,
                lang="en",
                now=NOW,
                provider=provider,
                safety_identifier="5a" * 32,
            )
        )


def _lines(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == LOGGER]


def _availability(service, specialist, **overrides: Any) -> dict[str, Any]:
    return {
        "service_id": service.id,
        "specialist_id": specialist.id,
        "date_from": MONDAY.isoformat(),
        "date_to": MONDAY.isoformat(),
        **overrides,
    }


def test_one_line_per_tool_call_with_salon_name_and_outcome(caplog, tbilisi_salon):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        _run(
            tbilisi_salon,
            [
                _tool_turn(
                    ToolCall(id="call-1", name="get_faq", arguments={}),
                    ToolCall(id="call-2", name="list_services", arguments={}),
                ),
                [TextDelta(text="Done."), _finish()],
            ],
        )

    lines = _lines(caplog)
    assert len(lines) == 2
    assert all(record.levelno == logging.INFO for record in caplog.records if record.name == LOGGER)
    for line, tool in zip(lines, ["get_faq", "list_services"], strict=True):
        assert line.startswith("assistant tool: ")
        assert f"salon_id={tbilisi_salon.id} " in line
        assert f"tool={tool} " in line
        assert line.endswith("outcome=ok")


def test_check_availability_logs_the_slot_count(caplog, tbilisi_salon, service, monday_specialist):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        _run(
            tbilisi_salon,
            [
                _tool_turn(
                    ToolCall(
                        id="call-1",
                        name="check_availability",
                        arguments=_availability(service, monday_specialist),
                    )
                ),
                [TextDelta(text="Monday is free."), _finish()],
            ],
        )

    [line] = _lines(caplog)
    assert "tool=check_availability outcome=ok slots=3" in line
    assert "reason=" not in line


def test_empty_check_availability_logs_the_reason(
    caplog, tbilisi_salon, service, monday_specialist
):
    tuesday = (MONDAY + dt.timedelta(days=1)).isoformat()

    with caplog.at_level(logging.INFO, logger=LOGGER):
        _run(
            tbilisi_salon,
            [
                _tool_turn(
                    ToolCall(
                        id="call-1",
                        name="check_availability",
                        arguments=_availability(
                            service, monday_specialist, date_from=tuesday, date_to=tuesday
                        ),
                    )
                ),
                [TextDelta(text="Not on Tuesday."), _finish()],
            ],
        )

    [line] = _lines(caplog)
    assert line.endswith("tool=check_availability outcome=ok slots=0 reason=day_off")


def test_outside_hours_check_availability_logs_that_reason(
    caplog, tbilisi_salon, service, monday_specialist
):
    """Sub-step 10e: the specialist works Monday until 18:00; 18:00-20:00 fits nowhere."""
    with caplog.at_level(logging.INFO, logger=LOGGER):
        _run(
            tbilisi_salon,
            [
                _tool_turn(
                    ToolCall(
                        id="call-1",
                        name="check_availability",
                        arguments=_availability(
                            service, monday_specialist, time_from="18:00", time_to="20:00"
                        ),
                    )
                ),
                [TextDelta(text="Too late on Monday."), _finish()],
            ],
        )

    [line] = _lines(caplog)
    assert line.endswith("tool=check_availability outcome=ok slots=0 reason=outside_hours")


def test_propose_booking_logs_passed_dropped_and_shown(
    caplog, tbilisi_salon, service, monday_specialist
):
    arguments = {
        "service_id": service.id,
        "specialist_id": monday_specialist.id,
        "slots": [SLOT_0900, "not-a-time"],
    }

    with caplog.at_level(logging.INFO, logger=LOGGER):
        _run(
            tbilisi_salon,
            [
                _tool_turn(ToolCall(id="call-1", name="propose_booking", arguments=arguments)),
                [TextDelta(text="Here is a card."), _finish()],
            ],
        )

    [line] = _lines(caplog)
    assert line.endswith("tool=propose_booking outcome=ok passed=2 dropped=1 shown=1")


@pytest.mark.parametrize(
    ("call", "expected_end", "absent"),
    [
        (
            ToolCall(id="call-1", name="fetch_secret_data", arguments={}),
            "tool=unknown outcome=invalid_tool_call",
            "fetch_secret_data",
        ),
        (
            ToolCall(
                id="call-1",
                name="check_availability",
                arguments={
                    "service_id": 1,
                    "specialist_id": "any",
                    "date_from": "2026-08-20",
                    "date_to": "2026-08-17",
                },
            ),
            "tool=check_availability outcome=invalid_range",
            "2026-08-20",
        ),
    ],
    ids=["unknown_tool", "tool_error"],
)
def test_error_outcome_is_logged_without_counts(caplog, tbilisi_salon, call, expected_end, absent):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        _run(tbilisi_salon, [_tool_turn(call), [TextDelta(text="Sorry."), _finish()]])

    [line] = _lines(caplog)
    assert line.endswith(expected_end)
    assert absent not in caplog.text


def test_no_message_text_argument_values_or_session_id(
    caplog, tbilisi_salon, service, monday_specialist
):
    user_text = "Secret question about my allergy"
    reply_text = "Private reply text"
    availability = _availability(service, monday_specialist, time_from="09:00", time_to="12:00")
    proposal = {
        "service_id": service.id,
        "specialist_id": monday_specialist.id,
        "slots": [SLOT_0900],
    }

    with caplog.at_level(logging.DEBUG):
        _run(
            tbilisi_salon,
            [
                _tool_turn(
                    ToolCall(id="call-1", name="check_availability", arguments=availability)
                ),
                _tool_turn(ToolCall(id="call-2", name="propose_booking", arguments=proposal)),
                [TextDelta(text=reply_text), _finish()],
            ],
            user_text=user_text,
        )

    assert len(_lines(caplog)) == 2
    for secret in [user_text, reply_text, SESSION_ID, MONDAY.isoformat(), "09:00", "12:00"]:
        assert secret not in caplog.text
