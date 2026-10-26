"""
Stage 16 sub-step 8a — the assistant's pre-stream limits (docs/DECISIONS.md
§ "Sub-step 8 details", 8a).

    per session        10/min    429 throttled, Retry-After
    per salon + IP     200/hour  429 throttled, Retry-After
    per salon per day  1000      429 assistant_daily_limit, Retry-After
                                 until the salon's local midnight

The provider is a scripted MockAssistantProvider patched into
`ai_assistant.views.get_assistant_provider`. The throttles' timer is frozen
at the real time the test starts, so a window never rolls over mid-test and
Retry-After is exact. The daily-limit tests freeze timezone.now instead: the
daily key and Retry-After come from the salon-local date. Throttle counters
live in the Django cache (cleared by conftest's `_clear_cache`), daily
counters in Redis DB 15 (conftest's `_assistant_redis`).
"""

import datetime as dt
import json
import time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import redis
from django.utils import timezone
from rest_framework.test import APIClient

from ai_assistant.limits import DAILY_MESSAGE_LIMIT, count_daily_message
from ai_assistant.providers.base import Finish, TextDelta
from ai_assistant.providers.mock import MockAssistantProvider, ScriptTurn
from ai_assistant.session_store import _client
from ai_assistant.throttles import AssistantSalonIPThrottle, AssistantSessionThrottle
from core.exceptions import AssistantProviderError
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

TBILISI = ZoneInfo("Asia/Tbilisi")
# 12:00 in Tbilisi (UTC+4, no DST): 12 hours to the salon's local midnight.
NOON_TBILISI = dt.datetime(2026, 11, 10, 8, 0, tzinfo=dt.UTC)
SECONDS_TO_MIDNIGHT_AT_NOON = 12 * 3600
# 21:30 UTC is already 01:30 on 11 November in Tbilisi.
LATE_UTC = dt.datetime(2026, 11, 10, 21, 30, tzinfo=dt.UTC)
CLIENT_IP = "203.0.113.7"


@pytest.fixture(autouse=True)
def _frozen_throttle_timer(monkeypatch):
    frozen = time.time()
    for throttle in (AssistantSessionThrottle, AssistantSalonIPThrottle):
        monkeypatch.setattr(throttle, "timer", staticmethod(lambda: frozen))


@pytest.fixture
def enabled_salon(salon):
    salon.assistant_enabled = True
    salon.timezone = "Asia/Tbilisi"
    salon.save(update_fields=["assistant_enabled", "timezone"])
    return salon


@pytest.fixture
def enabled_other_salon(other_salon):
    other_salon.assistant_enabled = True
    other_salon.timezone = "Asia/Tbilisi"
    other_salon.save(update_fields=["assistant_enabled", "timezone"])
    return other_salon


@pytest.fixture
def redis_db() -> redis.Redis:
    return _client()


def _finish() -> Finish:
    return Finish(input_tokens=10, output_tokens=5)


def _reply(text: str = "Hi! How can I help?") -> list[ScriptTurn]:
    return [[TextDelta(text=text), _finish()]]


def _use_provider(monkeypatch, script: list[ScriptTurn]) -> MockAssistantProvider:
    provider = MockAssistantProvider(script)
    monkeypatch.setattr("ai_assistant.views.get_assistant_provider", lambda: provider)
    return provider


def _freeze_now(monkeypatch, now: dt.datetime) -> None:
    monkeypatch.setattr(timezone, "now", lambda: now)


def _post(
    slug: str,
    body: Any = None,
    *,
    session_id: str | None = None,
    ip: str = CLIENT_IP,
):
    extra: dict[str, str] = {"REMOTE_ADDR": ip}
    if session_id is not None:
        extra["HTTP_X_ASSISTANT_SESSION"] = session_id
    body = {"message": "Hello"} if body is None else body
    return APIClient().post(
        f"/api/v1/salons/{slug}/assistant/messages/", body, format="json", **extra
    )


def _event_types(response) -> list[str]:
    assert response.status_code == 200
    assert response.streaming
    lines = b"".join(response.streaming_content).decode().splitlines()
    return [json.loads(line)["type"] for line in lines if line]


def _error_code(response) -> str | None:
    data = getattr(response, "data", None)
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return data["error"].get("code")
    return None


def _daily_key(salon, local_date: dt.date) -> str:
    return f"assistant:daily:{salon.id}:{local_date.isoformat()}"


def _tbilisi_date(now: dt.datetime) -> dt.date:
    return now.astimezone(TBILISI).date()


def _count(redis_db: redis.Redis, key: str) -> int | None:
    value = redis_db.get(key)
    return None if value is None else int(value)  # type: ignore[arg-type]


# --- 1-4. per session -----------------------------------------------------


def test_session_throttle_refuses_the_11th_message_with_retry_after(monkeypatch, enabled_salon):
    _use_provider(monkeypatch, _reply() * 11)

    for _ in range(10):
        assert _post(enabled_salon.slug, session_id="session-a").status_code == 200

    response = _post(enabled_salon.slug, session_id="session-a")

    assert response.status_code == 429
    assert not response.streaming
    assert _error_code(response) == "throttled"
    assert response["Retry-After"] == "60"


def test_another_session_id_from_the_same_ip_is_not_affected(monkeypatch, enabled_salon):
    _use_provider(monkeypatch, _reply() * 12)
    for _ in range(10):
        _post(enabled_salon.slug, session_id="session-a")
    assert _post(enabled_salon.slug, session_id="session-a").status_code == 429

    response = _post(enabled_salon.slug, session_id="session-b")

    assert response.status_code == 200


def test_without_a_session_header_the_session_throttle_does_not_apply(monkeypatch, enabled_salon):
    _use_provider(monkeypatch, _reply() * 11)

    statuses = [_post(enabled_salon.slug).status_code for _ in range(11)]

    assert statuses == [200] * 11


def test_session_counts_are_per_salon(monkeypatch, enabled_salon, enabled_other_salon):
    _use_provider(monkeypatch, _reply() * 22)

    for salon in (enabled_salon, enabled_other_salon):
        for _ in range(10):
            assert _post(salon.slug, session_id="same-raw-value").status_code == 200

    assert _post(enabled_salon.slug, session_id="same-raw-value").status_code == 429
    assert _post(enabled_other_salon.slug, session_id="same-raw-value").status_code == 429


# --- 5. per salon + IP ----------------------------------------------------


def test_salon_ip_throttle_refuses_the_201st_message_per_salon(
    monkeypatch, enabled_salon, enabled_other_salon
):
    _use_provider(monkeypatch, _reply() * 202)

    for _ in range(200):
        assert _post(enabled_salon.slug).status_code == 200

    refused = _post(enabled_salon.slug)
    other = _post(enabled_other_salon.slug)

    assert refused.status_code == 429
    assert _error_code(refused) == "throttled"
    assert refused["Retry-After"] == "3600"
    assert other.status_code == 200


# --- 6. history -----------------------------------------------------------


def test_history_get_is_never_throttled(enabled_salon):
    client = APIClient()
    url = f"/api/v1/salons/{enabled_salon.slug}/assistant/history/"

    statuses = {
        client.get(url, REMOTE_ADDR=CLIENT_IP, HTTP_X_ASSISTANT_SESSION="session-a").status_code
        for _ in range(250)
    }

    assert statuses == {200}


# --- 7-12. per salon per day ----------------------------------------------


def test_daily_limit_refuses_the_1001st_message_until_local_midnight(
    monkeypatch, enabled_salon, redis_db
):
    _freeze_now(monkeypatch, NOON_TBILISI)
    _use_provider(monkeypatch, _reply() * 2)
    key = _daily_key(enabled_salon, _tbilisi_date(NOON_TBILISI))
    redis_db.set(key, 999)

    assert _event_types(_post(enabled_salon.slug)) == ["text", "done"]
    assert _count(redis_db, key) == 1000

    response = _post(enabled_salon.slug)

    assert response.status_code == 429
    assert not response.streaming
    assert _error_code(response) == "assistant_daily_limit"
    assert response["Retry-After"] == str(SECONDS_TO_MIDNIGHT_AT_NOON)


def test_daily_key_uses_the_salon_local_date(monkeypatch, enabled_salon, redis_db):
    _freeze_now(monkeypatch, LATE_UTC)
    _use_provider(monkeypatch, _reply())

    _event_types(_post(enabled_salon.slug))

    assert _count(redis_db, _daily_key(enabled_salon, dt.date(2026, 11, 11))) == 1
    assert _count(redis_db, _daily_key(enabled_salon, dt.date(2026, 11, 10))) is None


def test_daily_counters_are_per_salon(monkeypatch, enabled_salon, enabled_other_salon, redis_db):
    _freeze_now(monkeypatch, NOON_TBILISI)
    _use_provider(monkeypatch, _reply())
    today = _tbilisi_date(NOON_TBILISI)
    # Filled through the real counter, so a key that loses the salon id
    # makes both salons share it and this salon's message is refused.
    with tenant_context(enabled_other_salon.id):
        for _ in range(DAILY_MESSAGE_LIMIT):
            count_daily_message(salon=enabled_other_salon, now=NOON_TBILISI)

    response = _post(enabled_salon.slug)

    assert (response.status_code, _error_code(response)) == (200, None)
    assert _event_types(response) == ["text", "done"]
    assert _count(redis_db, _daily_key(enabled_salon, today)) == 1
    assert _count(redis_db, _daily_key(enabled_other_salon, today)) == 1000


def test_invalid_messages_and_a_disabled_assistant_are_not_counted(
    monkeypatch, enabled_salon, other_salon, redis_db
):
    _freeze_now(monkeypatch, NOON_TBILISI)
    _use_provider(monkeypatch, _reply())
    today = _tbilisi_date(NOON_TBILISI)
    assert other_salon.assistant_enabled is False

    for body in ({}, {"message": ""}, {"message": "x" * 2001}):
        assert _post(enabled_salon.slug, body).status_code == 400
    assert _post(other_salon.slug).status_code == 404
    _event_types(_post(enabled_salon.slug))

    assert _count(redis_db, _daily_key(enabled_salon, today)) == 1
    assert _count(redis_db, _daily_key(other_salon, today)) is None


def test_a_message_whose_reply_fails_still_counts(monkeypatch, enabled_salon, redis_db):
    _freeze_now(monkeypatch, NOON_TBILISI)
    _use_provider(monkeypatch, [[TextDelta(text="Let me "), AssistantProviderError()]])

    assert _event_types(_post(enabled_salon.slug))[-1] == "error"

    assert _count(redis_db, _daily_key(enabled_salon, _tbilisi_date(NOON_TBILISI))) == 1


def test_daily_key_expires_after_the_end_of_the_local_day(monkeypatch, enabled_salon, redis_db):
    _freeze_now(monkeypatch, NOON_TBILISI)
    _use_provider(monkeypatch, _reply())

    _event_types(_post(enabled_salon.slug))

    ttl_ms = redis_db.pttl(_daily_key(enabled_salon, _tbilisi_date(NOON_TBILISI)))
    assert isinstance(ttl_ms, int)
    assert (
        SECONDS_TO_MIDNIGHT_AT_NOON * 1000 < ttl_ms <= (SECONDS_TO_MIDNIGHT_AT_NOON + 86400) * 1000
    )
