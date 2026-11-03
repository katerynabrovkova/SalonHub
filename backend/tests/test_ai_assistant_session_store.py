"""
Stage 16 sub-step 5 — chat session store in Redis (docs/DECISIONS.md §
"Stage 16 planning", "Session", and § "Sub-step 5 details"): one list per
session keyed by salon and the session id's SHA-256, last 20 messages,
sliding 30-minute expiry extended only by a new turn.

Runs against Redis DB 15 through conftest's autouse `_assistant_redis`
fixture, never the dev DB 3.
"""

import hashlib
import json
import re

import pytest
import redis

from ai_assistant.session_store import (
    _client,
    append_turn,
    load_history,
    new_session_id,
)
from core.tenancy import TenantContextMissingError, tenant_context
from tests.conftest import ASSISTANT_TEST_REDIS_DB

pytestmark = pytest.mark.django_db

SESSION_ID = "test-session-id-0123456789abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def redis_db() -> redis.Redis:
    return _client()


def _key(salon_id: int, session_id: str = SESSION_ID) -> str:
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    return f"assistant:session:{salon_id}:{digest}"


def test_append_turn_then_load_history_returns_both_messages_in_order(salon) -> None:
    with tenant_context(salon.id):
        append_turn(SESSION_ID, "Hello", "Hi! How can I help?")
        history = load_history(SESSION_ID)

    assert history == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi! How can I help?"},
    ]


def test_history_keeps_only_the_last_20_messages(salon) -> None:
    with tenant_context(salon.id):
        for i in range(11):
            append_turn(SESSION_ID, f"question {i}", f"answer {i}")
        history = load_history(SESSION_ID)

    assert len(history) == 20
    assert history[0] == {"role": "user", "content": "question 1"}
    assert history[-1] == {"role": "assistant", "content": "answer 10"}
    assert {"role": "user", "content": "question 0"} not in history
    assert {"role": "assistant", "content": "answer 0"} not in history


def test_append_turn_sets_a_30_minute_expiry(salon, redis_db) -> None:
    with tenant_context(salon.id):
        append_turn(SESSION_ID, "Hello", "Hi")

    assert 1790 <= redis_db.ttl(_key(salon.id)) <= 1800


def test_append_turn_resets_the_expiry(salon, redis_db) -> None:
    with tenant_context(salon.id):
        append_turn(SESSION_ID, "Hello", "Hi")
        redis_db.expire(_key(salon.id), 10)
        append_turn(SESSION_ID, "Second", "Reply")

    assert 1790 <= redis_db.ttl(_key(salon.id)) <= 1800


def test_load_history_does_not_extend_the_expiry(salon, redis_db) -> None:
    with tenant_context(salon.id):
        append_turn(SESSION_ID, "Hello", "Hi")
        redis_db.expire(_key(salon.id), 10)
        load_history(SESSION_ID)

    assert 0 < redis_db.ttl(_key(salon.id)) <= 10


def test_session_from_another_salon_resolves_to_nothing(salon, other_salon) -> None:
    with tenant_context(salon.id):
        append_turn(SESSION_ID, "Hello", "Hi")

    with tenant_context(other_salon.id):
        assert load_history(SESSION_ID) == []


def test_raw_session_id_is_never_part_of_a_key(salon, redis_db) -> None:
    with tenant_context(salon.id):
        append_turn(SESSION_ID, "Hello", "Hi")

    keys = [key.decode() for key in redis_db.scan_iter()]
    assert keys == [_key(salon.id)]
    assert hashlib.sha256(SESSION_ID.encode()).hexdigest() in keys[0]
    assert all(SESSION_ID not in key for key in keys)


def test_unknown_session_id_returns_empty_history(salon) -> None:
    with tenant_context(salon.id):
        assert load_history("never-issued-session-id") == []


def test_load_history_without_tenant_context_raises() -> None:
    with pytest.raises(TenantContextMissingError):
        load_history(SESSION_ID)


def test_append_turn_without_tenant_context_raises(redis_db) -> None:
    with pytest.raises(TenantContextMissingError):
        append_turn(SESSION_ID, "Hello", "Hi")

    assert list(redis_db.scan_iter()) == []


def test_new_session_id_is_random_long_and_url_safe() -> None:
    first = new_session_id()
    second = new_session_id()

    assert first != second
    assert len(first) >= 40
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", second)


def test_ukrainian_text_and_emoji_round_trip_unchanged(salon) -> None:
    user_text = "Чи можна записатися на манікюр у суботу? 💅"
    assistant_text = "Так, у суботу є вільні вікна 🤗 «Манікюр» — 60 хв."
    with tenant_context(salon.id):
        append_turn(SESSION_ID, user_text, assistant_text)
        history = load_history(SESSION_ID)

    assert history == [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    ]


def test_store_client_in_tests_uses_db_15_not_db_3(redis_db) -> None:
    db = redis_db.connection_pool.connection_kwargs["db"]
    assert db == ASSISTANT_TEST_REDIS_DB
    assert db != 3


# --- Stage 17.2b: cards in history -----------------------------------------

CARD = {
    "service": {"id": 7, "name": "Манікюр", "duration_minutes": 60},
    "specialist": {"id": 3, "name": "Jane", "average_rating": None, "review_count": 0},
    "price": "500.00",
    "deposit": "150.00",
    "due_at_visit": "350.00",
    "currency": "UAH",
    "slots": ["2026-08-17T09:00:00+04:00"],
}


def test_assistant_message_saved_with_a_card_round_trips_with_its_card(salon) -> None:
    with tenant_context(salon.id):
        append_turn(SESSION_ID, "Book me in", "Here is a card.", card=CARD)
        history = load_history(SESSION_ID)

    assert history == [
        {"role": "user", "content": "Book me in"},
        {"role": "assistant", "content": "Here is a card.", "card": CARD},
    ]


def test_message_with_a_card_cannot_be_read_from_another_salon(salon, other_salon) -> None:
    with tenant_context(salon.id):
        append_turn(SESSION_ID, "Book me in", "Here is a card.", card=CARD)

    with tenant_context(other_salon.id):
        assert load_history(SESSION_ID) == []
    with tenant_context(salon.id):
        assert load_history(SESSION_ID)[-1]["card"] == CARD


def test_message_saved_without_a_card_has_only_role_and_content(salon, redis_db) -> None:
    with tenant_context(salon.id):
        append_turn(SESSION_ID, "Hello", "Hi")

    items = [json.loads(item) for item in redis_db.lrange(_key(salon.id), 0, -1)]
    assert [set(item) for item in items] == [{"role", "content"}, {"role", "content"}]
