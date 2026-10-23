"""
Chat session history in Redis (docs/DECISIONS.md § Stage 16 planning,
"Session", and § "Sub-step 5 details").

One Redis list per session, keyed by the bound salon and the SHA-256 of the
session id, holding the last 20 messages as JSON `{role, content}` items
with a sliding 30-minute expiry. The salon comes only from tenant context.
Message texts are never logged.
"""

import hashlib
import json
import secrets
from functools import cache
from typing import cast

import redis
from django.conf import settings

from core.tenancy import TenantContextMissingError, get_current_salon_id

MAX_MESSAGES = 20
SESSION_TTL_SECONDS = 30 * 60


@cache
def _client_for(url: str) -> redis.Redis:
    return redis.Redis.from_url(url)


def _client() -> redis.Redis:
    """
    The client for the current ASSISTANT_REDIS_URL, read at call time so a
    test's settings override applies; built once per URL, then reused.
    """
    return _client_for(settings.ASSISTANT_REDIS_URL)


def _key(session_id: str) -> str:
    """
    The session's key under the bound salon. Only the id's hash is in the
    key, so a key listing or snapshot reveals no usable session id.
    Checked before any Redis call, so a missing tenant context never reads
    or writes anything.
    """
    salon_id = get_current_salon_id()
    if salon_id is None:
        raise TenantContextMissingError("The assistant session store needs a bound tenant context.")
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    return f"assistant:session:{salon_id}:{digest}"


def _message(role: str, content: str) -> str:
    return json.dumps({"role": role, "content": content}, ensure_ascii=False)


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def load_history(session_id: str) -> list[dict[str, str]]:
    """The session's messages, oldest first; [] when unknown or expired.
    Reading does not extend the expiry: only a new turn does."""
    # redis-py types its commands for the sync and async clients together;
    # the sync client always returns the list itself.
    items = cast(list[bytes], _client().lrange(_key(session_id), 0, -1))
    return [json.loads(item) for item in items]


def append_turn(session_id: str, user_text: str, assistant_text: str) -> None:
    """
    Stores one customer message with its reply. One MULTI/EXEC transaction,
    so two overlapping turns of the same session both survive, each pair
    kept together.
    """
    key = _key(session_id)
    pipeline = _client().pipeline(transaction=True)
    pipeline.rpush(key, _message("user", user_text), _message("assistant", assistant_text))
    pipeline.ltrim(key, -MAX_MESSAGES, -1)
    pipeline.expire(key, SESSION_TTL_SECONDS)
    pipeline.execute()
