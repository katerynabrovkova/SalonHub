"""
Stage 16 sub-step 9 — the assistant's daily counters (docs/DECISIONS.md §
Stage 16 planning, "Sub-step 9 details").

Two tenant-scoped Postgres models in `ai_assistant`: conversations per
salon-local day, and proposals per salon-local day and service. Increments
are atomic F() updates with a savepoint create and one retry on
IntegrityError. The salon argument must be the bound tenant. The
orchestrator counts a conversation on the first successfully stored turn of
a session and a proposal for every emitted `proposal` event; a counter
failure is logged without text and swallowed.

The new modules are imported inside each test (`_models()`, `_analytics()`),
so before they exist every test fails on its own with a missing-module
error instead of the whole file failing to collect.

The salon runs on Asia/Tbilisi (UTC+04:00 all year) and `now` is a fixed
tz-aware literal, never `timezone.now()`.
"""

import datetime as dt
import importlib
import logging
import threading
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext

from ai_assistant import orchestrator as orchestrator_module
from ai_assistant.orchestrator import run_turn
from ai_assistant.providers.base import Finish, TextDelta, ToolCall
from ai_assistant.providers.mock import MockAssistantProvider, ScriptTurn
from ai_assistant.session_store import load_history
from catalog.models import Service, ServiceCategory
from core.exceptions import AssistantProviderError
from core.tenancy import TenantContextMismatchError, TenantContextMissingError, tenant_context
from tests.conftest import make_working_hours

pytestmark = pytest.mark.django_db

SESSION_ID = "analytics-test-session-0123456789abcdefghijklmnopqrs"
OTHER_SESSION_ID = "analytics-test-session-other-0123456789abcdefghijk"

# A Monday; 09:00-18:00 Tbilisi working hours == 05:00-14:00 UTC.
MONDAY = dt.date(2026, 8, 17)
NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=dt.UTC)
NOW_LOCAL_DATE = dt.date(2026, 8, 16)
SLOT_0900 = "2026-08-17T09:00:00+04:00"

# 21:30 UTC on the 16th is 01:30 on the 17th in Tbilisi.
LATE_UTC_NOW = dt.datetime(2026, 8, 16, 21, 30, tzinfo=dt.UTC)
LATE_UTC_LOCAL_DATE = dt.date(2026, 8, 17)

USER_TEXT = "Secret customer words 7f3a"
SAFETY_IDENTIFIER = "5a" * 32


def _models() -> ModuleType:
    return importlib.import_module("ai_assistant.models")


def _analytics() -> ModuleType:
    return importlib.import_module("ai_assistant.analytics")


@pytest.fixture
def tbilisi_salon(salon):
    salon.timezone = "Asia/Tbilisi"
    salon.save(update_fields=["timezone"])
    return salon


def _make_service(salon, category, name: str) -> Service:
    with tenant_context(salon.id):
        return Service.objects.create(
            salon=salon,
            category=category,
            name={"en": name},
            duration_minutes=60,
            price="500.00",
        )


def _other_salon_service(other_salon) -> Service:
    with tenant_context(other_salon.id):
        category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Nails"})
    return _make_service(other_salon, category, "Manicure")


def _conversation_counts(salon) -> dict[dt.date, int]:
    with tenant_context(salon.id):
        return {row.date: row.count for row in _models().DailyConversationCount.objects.all()}


def _proposal_counts(salon) -> dict[tuple[dt.date, int], int]:
    with tenant_context(salon.id):
        return {
            (row.date, row.service_id): row.count
            for row in _models().DailyProposalCount.objects.all()
        }


def _record_unbound(counter: str, salon, service_id: int, now: dt.datetime = NOW) -> None:
    """Calls one counter function without binding any tenant context."""
    if counter == "conversation":
        _analytics().record_conversation_started(salon=salon, now=now)
    else:
        _analytics().record_proposal(salon=salon, service_id=service_id, now=now)


def _record_conversation(salon, now: dt.datetime = NOW) -> None:
    with tenant_context(salon.id):
        _record_unbound("conversation", salon, 0, now)


def _record_proposal(salon, service_id: int, now: dt.datetime = NOW) -> None:
    with tenant_context(salon.id):
        _record_unbound("proposal", salon, service_id, now)


def _counter_model(counter: str) -> Any:
    models = _models()
    return models.DailyConversationCount if counter == "conversation" else models.DailyProposalCount


COUNTERS = ["conversation", "proposal"]


# --- Models ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model_name", "expected_fields"),
    [
        ("DailyConversationCount", {"id", "salon", "date", "count"}),
        ("DailyProposalCount", {"id", "salon", "date", "service", "count"}),
    ],
)
def test_counter_models_hold_only_aggregate_fields(model_name, expected_fields):
    model = getattr(_models(), model_name)

    assert {field.name for field in model._meta.concrete_fields} == expected_fields


# --- Increments ------------------------------------------------------------------


def test_conversation_counter_creates_then_increments_the_days_row(tbilisi_salon):
    _record_conversation(tbilisi_salon)
    _record_conversation(tbilisi_salon)

    assert _conversation_counts(tbilisi_salon) == {NOW_LOCAL_DATE: 2}


def test_proposal_counter_creates_then_increments_the_days_row(tbilisi_salon, service):
    _record_proposal(tbilisi_salon, service.id)
    _record_proposal(tbilisi_salon, service.id)

    assert _proposal_counts(tbilisi_salon) == {(NOW_LOCAL_DATE, service.id): 2}


def test_counter_day_uses_salon_local_date(tbilisi_salon, service):
    _record_conversation(tbilisi_salon, now=LATE_UTC_NOW)
    _record_proposal(tbilisi_salon, service.id, now=LATE_UTC_NOW)

    assert _conversation_counts(tbilisi_salon) == {LATE_UTC_LOCAL_DATE: 1}
    assert _proposal_counts(tbilisi_salon) == {(LATE_UTC_LOCAL_DATE, service.id): 1}


def test_two_services_same_salon_counted_separately_higher_pk(tbilisi_salon, service_category):
    lower = _make_service(tbilisi_salon, service_category, "Manicure")
    higher = _make_service(tbilisi_salon, service_category, "Pedicure")
    assert higher.pk > lower.pk

    _record_proposal(tbilisi_salon, higher.id)
    _record_proposal(tbilisi_salon, higher.id)
    _record_proposal(tbilisi_salon, lower.id)

    assert _proposal_counts(tbilisi_salon) == {
        (NOW_LOCAL_DATE, lower.id): 1,
        (NOW_LOCAL_DATE, higher.id): 2,
    }


def test_counters_isolated_between_two_salons(tbilisi_salon, other_salon, service_category):
    own_service = _make_service(tbilisi_salon, service_category, "Manicure")
    other_service = _other_salon_service(other_salon)

    _record_conversation(tbilisi_salon)
    _record_proposal(tbilisi_salon, own_service.id)
    _record_conversation(other_salon)
    _record_conversation(other_salon)
    _record_proposal(other_salon, other_service.id)

    other_local_date = NOW.astimezone(ZoneInfo(other_salon.timezone)).date()
    assert _conversation_counts(tbilisi_salon) == {NOW_LOCAL_DATE: 1}
    assert _proposal_counts(tbilisi_salon) == {(NOW_LOCAL_DATE, own_service.id): 1}
    assert _conversation_counts(other_salon) == {other_local_date: 2}
    assert _proposal_counts(other_salon) == {(other_local_date, other_service.id): 1}


def test_proposal_counter_rejects_service_from_another_salon(tbilisi_salon, other_salon):
    other_service = _other_salon_service(other_salon)

    with pytest.raises(ValueError, match="service"):
        _record_proposal(tbilisi_salon, other_service.id)

    assert _models().DailyProposalCount.unscoped_objects.count() == 0


def test_proposal_count_row_with_service_from_another_salon_is_rejected_by_the_database(
    tbilisi_salon, other_salon
):
    """Writes the row directly, past the analytics check: the composite
    tenant FK (core/db.py's composite_tenant_fk) on (service_id, salon_id)
    rejects it, as for Appointment.service."""
    other_service = _other_salon_service(other_salon)

    with pytest.raises(IntegrityError), transaction.atomic():
        _models().DailyProposalCount.unscoped_objects.create(
            salon=tbilisi_salon, date=NOW_LOCAL_DATE, service=other_service, count=1
        )

    assert _models().DailyProposalCount.unscoped_objects.count() == 0


@pytest.mark.parametrize("counter", COUNTERS)
def test_counter_rejects_salon_other_than_the_bound_tenant(
    tbilisi_salon, other_salon, service, counter
):
    """The salon argument and the bound tenant differ: the tenant-scoped
    UPDATE would match nothing while the create inserted a row for the
    other salon. `service` belongs to the salon argument, so the service
    check would pass; only the tenant check can refuse this call."""
    with tenant_context(other_salon.id), pytest.raises(TenantContextMismatchError):
        _record_unbound(counter, tbilisi_salon, service.id)

    assert _counter_model(counter).unscoped_objects.count() == 0


@pytest.mark.parametrize("counter", COUNTERS)
def test_counter_without_tenant_context_raises(tbilisi_salon, service, counter):
    with pytest.raises(TenantContextMissingError):
        _record_unbound(counter, tbilisi_salon, service.id)

    assert _counter_model(counter).unscoped_objects.count() == 0


@pytest.mark.parametrize("counter", COUNTERS)
def test_update_is_retried_once_after_integrity_error_then_raises(
    monkeypatch, tbilisi_salon, service, counter
):
    """A create that loses to a row the retry still cannot see: one UPDATE,
    the create, one retried UPDATE, then the IntegrityError is raised. No
    loop. The create goes through Model.save(), as QuerySet.create does."""
    model = _counter_model(counter)
    saves: list[object] = []

    def _losing_save(self: object, *args: object, **kwargs: object) -> None:
        saves.append(self)
        raise IntegrityError("simulated concurrent create")

    monkeypatch.setattr(model, "save", _losing_save)

    with (
        CaptureQueriesContext(connection) as queries,
        tenant_context(tbilisi_salon.id),
        pytest.raises(IntegrityError),
    ):
        _record_unbound(counter, tbilisi_salon, service.id)

    updates = [
        q for q in queries.captured_queries if q["sql"].lstrip().upper().startswith("UPDATE")
    ]
    assert len(updates) == 2
    assert len(saves) == 1
    assert model.unscoped_objects.count() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("starting_count", [0, 1], ids=["no_row", "existing_row"])
@pytest.mark.parametrize("counter", COUNTERS)
def test_concurrent_increments_are_not_lost(tbilisi_salon, service, counter, starting_count):
    """Same salon, same day, same service. `no_row` races the first-row
    create (every thread takes the create path); `existing_row` races the
    UPDATE on a row that is already there. Each case catches a different
    read-modify-write (docs/DECISIONS.md § "Sub-step 9 details").

    transaction=True: each thread opens its own DB connection, which cannot
    see rows created inside a test transaction. Each thread binds the tenant
    itself, since a new thread does not inherit context variables, and
    closes its own connection at the end."""
    workers = 8
    if starting_count:
        with tenant_context(tbilisi_salon.id):
            _record_unbound(counter, tbilisi_salon, service.id)
    barrier = threading.Barrier(workers)
    errors: list[BaseException] = []

    def _work() -> None:
        try:
            barrier.wait()
            with tenant_context(tbilisi_salon.id):
                _record_unbound(counter, tbilisi_salon, service.id)
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=_work) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    expected = starting_count + workers
    if counter == "conversation":
        assert _conversation_counts(tbilisi_salon) == {NOW_LOCAL_DATE: expected}
    else:
        assert _proposal_counts(tbilisi_salon) == {(NOW_LOCAL_DATE, service.id): expected}


# --- Orchestrator: conversations ------------------------------------------------


def _finish() -> Finish:
    return Finish(input_tokens=10, output_tokens=5)


def _reply(text: str = "Hi!") -> ScriptTurn:
    return [TextDelta(text=text), _finish()]


def _run(salon, script: list[ScriptTurn], *, session_id: str = SESSION_ID) -> list[dict[str, Any]]:
    provider = MockAssistantProvider(script)
    with tenant_context(salon.id):
        return list(
            run_turn(
                session_id=session_id,
                user_text=USER_TEXT,
                lang="en",
                now=NOW,
                provider=provider,
                safety_identifier=SAFETY_IDENTIFIER,
            )
        )


def test_first_successful_turn_counts_one_conversation(tbilisi_salon):
    events = _run(tbilisi_salon, [_reply()])

    assert events[-1] == {"type": "done"}
    assert _conversation_counts(tbilisi_salon) == {NOW_LOCAL_DATE: 1}


def test_second_turn_same_session_does_not_count_conversation(tbilisi_salon):
    _run(tbilisi_salon, [_reply()])
    _run(tbilisi_salon, [_reply("Again")])

    assert _conversation_counts(tbilisi_salon) == {NOW_LOCAL_DATE: 1}


def test_each_new_session_counts_its_own_conversation(tbilisi_salon):
    _run(tbilisi_salon, [_reply()], session_id=SESSION_ID)
    _run(tbilisi_salon, [_reply()], session_id=OTHER_SESSION_ID)

    assert _conversation_counts(tbilisi_salon) == {NOW_LOCAL_DATE: 2}


def test_failed_first_turn_not_counted_and_successful_retry_counts_once(tbilisi_salon):
    failed = _run(tbilisi_salon, [[TextDelta(text="Let me "), AssistantProviderError()]])
    assert failed[-1] == {"type": "error", "code": "assistant_provider_error"}
    assert _conversation_counts(tbilisi_salon) == {}

    _run(tbilisi_salon, [_reply()])

    assert _conversation_counts(tbilisi_salon) == {NOW_LOCAL_DATE: 1}


# --- Orchestrator: proposals ------------------------------------------------------


def _monday_hours(salon, specialist) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )


def _propose(call_id: str, arguments: dict[str, Any]) -> ToolCall:
    return ToolCall(id=call_id, name="propose_booking", arguments=arguments)


@pytest.mark.parametrize("slots", [[SLOT_0900], []], ids=["one_slot", "zero_slots"])
def test_emitted_proposal_increments_its_service_counter(tbilisi_salon, service, specialist, slots):
    _monday_hours(tbilisi_salon, specialist)
    arguments = {"service_id": service.id, "specialist_id": specialist.id, "slots": slots}

    events = _run(tbilisi_salon, [[_propose("call-1", arguments), _finish()], _reply("Here.")])

    proposals = [event for event in events if event["type"] == "proposal"]
    assert len(proposals) == 1
    assert proposals[0]["card"]["slots"] == slots
    assert _proposal_counts(tbilisi_salon) == {(NOW_LOCAL_DATE, service.id): 1}


def test_proposal_is_counted_when_the_stream_is_closed_right_after_it(
    tbilisi_salon, service, specialist
):
    """The customer clicks the booking link and the stream is closed while
    the generator is paused at the proposal yield: code after that yield
    never runs, so the count must happen before it."""
    _monday_hours(tbilisi_salon, specialist)
    arguments = {"service_id": service.id, "specialist_id": specialist.id}
    provider = MockAssistantProvider([[_propose("call-1", arguments), _finish()], _reply("Here.")])

    with tenant_context(tbilisi_salon.id):
        events = run_turn(
            session_id=SESSION_ID,
            user_text=USER_TEXT,
            lang="en",
            now=NOW,
            provider=provider,
            safety_identifier=SAFETY_IDENTIFIER,
        )
        for event in events:
            if event["type"] == "proposal":
                break
        else:
            pytest.fail("no proposal event")
        events.close()

    assert _proposal_counts(tbilisi_salon) == {(NOW_LOCAL_DATE, service.id): 1}


def test_turn_without_proposal_does_not_count_proposal(tbilisi_salon, service):
    _run(tbilisi_salon, [_reply()])

    assert _proposal_counts(tbilisi_salon) == {}


@pytest.mark.parametrize(
    "arguments",
    [
        {"service_id": 999_999, "specialist_id": "any"},
        {"service_id": "not-an-id", "specialist_id": "any"},
        {"service_id": 1},
    ],
    ids=["not_found", "invalid_value", "missing_argument"],
)
def test_not_found_or_invalid_proposal_is_not_counted(tbilisi_salon, service, arguments):
    events = _run(tbilisi_salon, [[_propose("call-1", arguments), _finish()], _reply("Sorry.")])

    assert all(event["type"] != "proposal" for event in events)
    assert _proposal_counts(tbilisi_salon) == {}


def test_refused_second_proposal_is_counted_once(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)
    arguments = {"service_id": service.id, "specialist_id": specialist.id}

    _run(
        tbilisi_salon,
        [
            [_propose("call-1", arguments), _finish()],
            [_propose("call-2", arguments), _finish()],
            _reply("Here."),
        ],
    )

    assert _proposal_counts(tbilisi_salon) == {(NOW_LOCAL_DATE, service.id): 1}


def test_proposal_of_a_failed_turn_is_not_emitted_or_counted(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)
    arguments = {"service_id": service.id, "specialist_id": specialist.id}

    events = _run(
        tbilisi_salon,
        [[_propose("call-1", arguments), _finish()], [AssistantProviderError()]],
    )

    assert all(event["type"] != "proposal" for event in events)
    assert _proposal_counts(tbilisi_salon) == {}


# --- Orchestrator: counter failures ----------------------------------------------


def test_counter_failure_is_logged_without_text_and_does_not_break_the_stream(
    caplog, monkeypatch, tbilisi_salon, service, specialist
):
    def _boom(**kwargs: object) -> None:
        raise RuntimeError("counter storage down")

    monkeypatch.setattr(orchestrator_module, "record_conversation_started", _boom)
    monkeypatch.setattr(orchestrator_module, "record_proposal", _boom)
    _monday_hours(tbilisi_salon, specialist)
    arguments = {"service_id": service.id, "specialist_id": specialist.id}

    with caplog.at_level(logging.DEBUG):
        events = _run(tbilisi_salon, [[_propose("call-1", arguments), _finish()], _reply("Here.")])

    assert [event["type"] for event in events] == ["text", "proposal", "done"]
    with tenant_context(tbilisi_salon.id):
        assert load_history(SESSION_ID) == [
            {"role": "user", "content": USER_TEXT},
            {"role": "assistant", "content": "Here."},
        ]
    failures = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and f"salon_id={tbilisi_salon.id}" in record.getMessage()
    ]
    assert len(failures) == 2
    assert USER_TEXT not in caplog.text
