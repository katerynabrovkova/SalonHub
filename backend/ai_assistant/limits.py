"""
Stage 16 sub-step 8a — the per-salon daily message limit (docs/DECISIONS.md
§ "Sub-step 8 details", 8a).

1000 accepted messages per salon-local day, counted in the assistant Redis
DB at `assistant:daily:<salon_id>:<local ISO date>`. The key expires one
hour after the salon's local midnight, set as a relative EXPIRE computed
from `now`. Over the limit: 429 `assistant_daily_limit` with Retry-After
until the salon's local midnight. A message refused by the stream cap is
uncounted again (undo_daily_message).

Sub-step 8b — the global cap of 16 concurrent streams across processes: a
sorted set of stream members in the same DB, scored with the Redis
server's time. A member older than 120 seconds is a crashed process's
leftover and no longer holds a slot.
"""

import datetime as dt
import math
import secrets
from functools import cache
from typing import cast
from zoneinfo import ZoneInfo

from django.conf import settings
from redis.commands.core import Script

from ai_assistant.session_store import _client, _client_for
from tenants.models import Salon

DAILY_MESSAGE_LIMIT = 1000
EXPIRY_MARGIN_SECONDS = 3600

MAX_CONCURRENT_STREAMS = 16
STREAM_SLOT_STALE_SECONDS = 120
# One global sorted set, not per salon: the cap protects the server.
STREAM_SLOTS_KEY = "assistant:streams"

# KEYS[1]: the sorted set. ARGV: stale seconds, cap, new member. Scored with
# the server's TIME, so every process shares one clock. Returns 1 when the
# member was added, 0 when all slots are taken.
_ACQUIRE_LUA = """
local time = redis.call('TIME')
local now = tonumber(time[1]) + tonumber(time[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - tonumber(ARGV[1]))
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[2]) then
    redis.call('ZADD', KEYS[1], now, ARGV[3])
    return 1
end
return 0
"""


def seconds_until_local_midnight(*, salon: Salon, now: dt.datetime) -> int:
    """Whole seconds from `now` to the next midnight in the salon's timezone."""
    tz = ZoneInfo(salon.timezone)
    next_day = now.astimezone(tz).date() + dt.timedelta(days=1)
    midnight = dt.datetime.combine(next_day, dt.time(0), tzinfo=tz)
    # Both sides in UTC: subtracting two datetimes with the same tzinfo
    # compares wall clocks and is wrong across a DST change.
    remaining = midnight.astimezone(dt.UTC) - now.astimezone(dt.UTC)
    return math.ceil(remaining.total_seconds())


def _daily_key(*, salon: Salon, now: dt.datetime) -> str:
    local_date = now.astimezone(ZoneInfo(salon.timezone)).date()
    return f"assistant:daily:{salon.id}:{local_date.isoformat()}"


def count_daily_message(*, salon: Salon, now: dt.datetime) -> int:
    """Counts one accepted message for the salon's local day of `now` and
    returns the new count. INCR and EXPIRE run in one MULTI/EXEC."""
    key = _daily_key(salon=salon, now=now)
    ttl = seconds_until_local_midnight(salon=salon, now=now) + EXPIRY_MARGIN_SECONDS
    pipeline = _client().pipeline(transaction=True)
    pipeline.incr(key)
    pipeline.expire(key, ttl)
    count, _ = pipeline.execute()
    return cast(int, count)


def undo_daily_message(*, salon: Salon, now: dt.datetime) -> None:
    """Takes back the count of a message refused by the stream cap."""
    _client().decr(_daily_key(salon=salon, now=now))


@cache
def _acquire_script_for(url: str) -> Script:
    return _client_for(url).register_script(_ACQUIRE_LUA)


def acquire_stream_slot() -> str | None:
    """Takes one of the MAX_CONCURRENT_STREAMS global stream slots; returns
    its random member id, or None when all slots are taken. One Lua script
    scored with the Redis server's TIME: drops members older than
    STREAM_SLOT_STALE_SECONDS, counts, adds only below the cap."""
    member = secrets.token_hex(16)
    script = _acquire_script_for(settings.ASSISTANT_REDIS_URL)
    added = script(
        keys=[STREAM_SLOTS_KEY],
        args=[STREAM_SLOT_STALE_SECONDS, MAX_CONCURRENT_STREAMS, member],
    )
    return member if added == 1 else None


def release_stream_slot(member: str) -> None:
    """Frees the slot held by `member` (ZREM); releasing twice is harmless."""
    _client().zrem(STREAM_SLOTS_KEY, member)
