"""
Stage 16 sub-step 8b — the global cap of 16 concurrent assistant streams
(docs/DECISIONS.md § "Sub-step 8 details", 8b, and the cap-refusal note in
8a).

The cap is one global sorted set in the assistant Redis DB (DB 15 in tests,
conftest's `_assistant_redis`). Members are seeded directly, scored with
the Redis server's own TIME, as the acquire script scores them: "fresh"
members hold a slot, "stale" ones (older than 120 s) do not. A held slot is
checked right after the view returns, before the body is iterated: the
slot is taken in the view, so it must already be in the set.

The provider is a scripted MockAssistantProvider patched into
`ai_assistant.views.get_assistant_provider`. The daily-limit test freezes
timezone.now so the daily key's date is known.
"""

import datetime as dt
import json
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import redis
from django.utils import timezone
from rest_framework.test import APIClient

from ai_assistant import tools as tools_module
from ai_assistant.limits import (
    MAX_CONCURRENT_STREAMS,
    STREAM_SLOT_STALE_SECONDS,
    STREAM_SLOTS_KEY,
    acquire_stream_slot,
    release_stream_slot,
)
from ai_assistant.providers.base import Finish, TextDelta, ToolCall
from ai_assistant.providers.mock import MockAssistantProvider, ScriptTurn
from ai_assistant.session_store import _client
from core.exceptions import AssistantProviderError
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 11, 10, 8, 0, tzinfo=dt.UTC)


@pytest.fixture
def enabled_salon(salon):
    salon.assistant_enabled = True
    salon.save(update_fields=["assistant_enabled"])
    return salon


@pytest.fixture
def enabled_other_salon(other_salon):
    other_salon.assistant_enabled = True
    other_salon.save(update_fields=["assistant_enabled"])
    return other_salon


@pytest.fixture
def redis_db() -> redis.Redis:
    return _client()


def _server_now(redis_db: redis.Redis) -> float:
    seconds, microseconds = redis_db.time()  # type: ignore[misc]
    return seconds + microseconds / 1_000_000


def _seed(redis_db: redis.Redis, count: int, *, stale: bool = False) -> list[str]:
    score = _server_now(redis_db) - (STREAM_SLOT_STALE_SECONDS + 60 if stale else 0)
    members = [f"seeded-{i}" for i in range(count)]
    if members:
        redis_db.zadd(STREAM_SLOTS_KEY, dict.fromkeys(members, score))
    return members


def _held(redis_db: redis.Redis) -> int:
    return int(redis_db.zcard(STREAM_SLOTS_KEY))  # type: ignore[arg-type]


def _members(redis_db: redis.Redis) -> set[str]:
    return {m.decode() for m in redis_db.zrange(STREAM_SLOTS_KEY, 0, -1)}  # type: ignore[union-attr]


def _finish() -> Finish:
    return Finish(input_tokens=10, output_tokens=5)


def _reply(text: str = "Hi! How can I help?") -> list[ScriptTurn]:
    return [[TextDelta(text=text), _finish()]]


def _use_provider(monkeypatch, script: list[ScriptTurn]) -> MockAssistantProvider:
    provider = MockAssistantProvider(script)
    monkeypatch.setattr("ai_assistant.views.get_assistant_provider", lambda: provider)
    return provider


def _post(slug: str, body: Any = None):
    body = {"message": "Hello"} if body is None else body
    return APIClient().post(f"/api/v1/salons/{slug}/assistant/messages/", body, format="json")


def _events(response) -> list[dict[str, Any]]:
    assert response.status_code == 200
    assert response.streaming
    lines = b"".join(response.streaming_content).decode().splitlines()
    return [json.loads(line) for line in lines if line]


def _error_code(response) -> str | None:
    data = getattr(response, "data", None)
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return data["error"].get("code")
    return None


def _daily_key(salon, now: dt.datetime) -> str:
    local_date = now.astimezone(ZoneInfo(salon.timezone)).date()
    return f"assistant:daily:{salon.id}:{local_date.isoformat()}"


# --- 1-2. refusal ---------------------------------------------------------


def test_full_cap_refuses_with_503_assistant_busy_and_no_stream(
    monkeypatch, enabled_salon, redis_db
):
    provider = _use_provider(monkeypatch, _reply())
    _seed(redis_db, MAX_CONCURRENT_STREAMS)

    response = _post(enabled_salon.slug)

    assert response.status_code == 503
    assert not response.streaming
    assert _error_code(response) == "assistant_busy"
    assert response["Retry-After"] == "10"
    assert provider.calls == []
    assert _held(redis_db) == MAX_CONCURRENT_STREAMS


def test_busy_refusal_does_not_count_toward_the_daily_limit(monkeypatch, enabled_salon, redis_db):
    monkeypatch.setattr(timezone, "now", lambda: NOW)
    _use_provider(monkeypatch, _reply())
    key = _daily_key(enabled_salon, NOW)
    redis_db.set(key, 5)
    _seed(redis_db, MAX_CONCURRENT_STREAMS)

    response = _post(enabled_salon.slug)

    assert response.status_code == 503
    assert int(redis_db.get(key)) == 5  # type: ignore[arg-type]


# --- 3-4. acquiring -------------------------------------------------------


def test_with_one_free_slot_the_message_streams_and_frees_it(monkeypatch, enabled_salon, redis_db):
    _use_provider(monkeypatch, _reply())
    _seed(redis_db, MAX_CONCURRENT_STREAMS - 1)

    response = _post(enabled_salon.slug)
    assert _held(redis_db) == MAX_CONCURRENT_STREAMS

    assert [event["type"] for event in _events(response)] == ["text", "done"]
    assert _held(redis_db) == MAX_CONCURRENT_STREAMS - 1


def test_stale_members_do_not_hold_slots_and_are_removed(monkeypatch, enabled_salon, redis_db):
    _use_provider(monkeypatch, _reply())
    stale = _seed(redis_db, MAX_CONCURRENT_STREAMS, stale=True)

    response = _post(enabled_salon.slug)
    held = _members(redis_db)

    assert len(held) == 1
    assert held.isdisjoint(stale)
    assert [event["type"] for event in _events(response)] == ["text", "done"]
    assert _held(redis_db) == 0


# --- 5-9. release ---------------------------------------------------------


def test_slot_is_released_after_a_provider_error(monkeypatch, enabled_salon, redis_db):
    _use_provider(monkeypatch, [[TextDelta(text="Let me "), AssistantProviderError()]])

    response = _post(enabled_salon.slug)
    assert _held(redis_db) == 1

    assert _events(response)[-1] == {"type": "error", "code": "assistant_provider_error"}
    assert _held(redis_db) == 0


def test_slot_is_released_after_an_unexpected_exception(monkeypatch, enabled_salon, redis_db):
    spec, _real = tools_module.TOOLS["list_services"]

    def _boom(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setitem(tools_module.TOOLS, "list_services", (spec, _boom))
    _use_provider(
        monkeypatch, [[ToolCall(id="call-1", name="list_services", arguments={}), _finish()]]
    )

    response = _post(enabled_salon.slug)
    assert _held(redis_db) == 1

    assert _events(response)[-1] == {"type": "error", "code": "internal_error"}
    assert _held(redis_db) == 0


def test_slot_is_released_when_the_client_disconnects(monkeypatch, enabled_salon, redis_db):
    _use_provider(monkeypatch, [[TextDelta(text="First "), TextDelta(text="second"), _finish()]])

    response = _post(enabled_salon.slug)
    assert response.status_code == 200
    next(iter(response.streaming_content))
    assert _held(redis_db) == 1

    response.close()

    assert _held(redis_db) == 0


def test_slot_is_released_when_the_stream_never_starts(monkeypatch, enabled_salon, redis_db):
    _use_provider(monkeypatch, _reply())

    response = _post(enabled_salon.slug)
    assert response.status_code == 200
    assert _held(redis_db) == 1

    response.close()

    assert _held(redis_db) == 0


def test_slot_is_released_on_an_exception_before_the_response(monkeypatch, enabled_salon, redis_db):
    held_when_raised: list[int] = []

    def _broken_provider() -> MockAssistantProvider:
        held_when_raised.append(_held(redis_db))
        raise RuntimeError("provider setup failed")

    monkeypatch.setattr("ai_assistant.views.get_assistant_provider", _broken_provider)

    response = _post(enabled_salon.slug)

    assert response.status_code == 500
    assert held_when_raised == [1]
    assert _held(redis_db) == 0


# --- 10-11. release and scope ---------------------------------------------


def test_releasing_twice_is_harmless_and_keeps_other_members(redis_db):
    others = _seed(redis_db, 2)
    member = acquire_stream_slot()
    assert member is not None

    release_stream_slot(member)
    release_stream_slot(member)

    assert _members(redis_db) == set(others)


def test_cap_is_global_across_salons(monkeypatch, enabled_salon, enabled_other_salon, redis_db):
    _use_provider(monkeypatch, _reply())
    # Taken through the real acquire, so a per-salon key would leave this
    # salon free and the message would stream.
    with tenant_context(enabled_other_salon.id):
        for _ in range(MAX_CONCURRENT_STREAMS):
            assert acquire_stream_slot() is not None

    response = _post(enabled_salon.slug)

    assert (response.status_code, _error_code(response)) == (503, "assistant_busy")


# --- 12. order ------------------------------------------------------------


def test_validation_and_disabled_answer_before_the_cap(
    monkeypatch, enabled_salon, other_salon, redis_db
):
    _use_provider(monkeypatch, _reply())
    _seed(redis_db, MAX_CONCURRENT_STREAMS)
    assert other_salon.assistant_enabled is False

    invalid = _post(enabled_salon.slug, {"message": ""})
    disabled = _post(other_salon.slug)

    assert (invalid.status_code, _error_code(invalid)) == (400, "invalid")
    assert (disabled.status_code, _error_code(disabled)) == (404, "not_found")
