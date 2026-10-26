"""
Stage 16 sub-step 8a — the per-salon daily message limit (docs/DECISIONS.md
§ "Sub-step 8 details", 8a).

1000 accepted messages per salon-local day, counted in the assistant Redis
DB at `assistant:daily:<salon_id>:<local ISO date>`. The key expires one
hour after the salon's local midnight, set as a relative EXPIRE computed
from `now`. Over the limit: 429 `assistant_daily_limit` with Retry-After
until the salon's local midnight.
"""

import datetime as dt
import math
from typing import cast
from zoneinfo import ZoneInfo

from ai_assistant.session_store import _client
from tenants.models import Salon

DAILY_MESSAGE_LIMIT = 1000
EXPIRY_MARGIN_SECONDS = 3600


def seconds_until_local_midnight(*, salon: Salon, now: dt.datetime) -> int:
    """Whole seconds from `now` to the next midnight in the salon's timezone."""
    tz = ZoneInfo(salon.timezone)
    next_day = now.astimezone(tz).date() + dt.timedelta(days=1)
    midnight = dt.datetime.combine(next_day, dt.time(0), tzinfo=tz)
    # Both sides in UTC: subtracting two datetimes with the same tzinfo
    # compares wall clocks and is wrong across a DST change.
    remaining = midnight.astimezone(dt.UTC) - now.astimezone(dt.UTC)
    return math.ceil(remaining.total_seconds())


def count_daily_message(*, salon: Salon, now: dt.datetime) -> int:
    """Counts one accepted message for the salon's local day of `now` and
    returns the new count. INCR and EXPIRE run in one MULTI/EXEC."""
    local_date = now.astimezone(ZoneInfo(salon.timezone)).date()
    key = f"assistant:daily:{salon.id}:{local_date.isoformat()}"
    ttl = seconds_until_local_midnight(salon=salon, now=now) + EXPIRY_MARGIN_SECONDS
    pipeline = _client().pipeline(transaction=True)
    pipeline.incr(key)
    pipeline.expire(key, ttl)
    count, _ = pipeline.execute()
    return cast(int, count)
