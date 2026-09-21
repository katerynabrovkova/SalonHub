"""
Auth session cookie transport (docs/DECISIONS.md § Stage 12 "Cookie mechanism
resolved").

The authenticated ``Account`` JWT session is carried in two httpOnly cookies
set by Django itself — never in the JSON body, never in ``localStorage``:

* ``access_token`` — ``Path=/``, sent on every request; read by
  ``accounts.authentication.AccountJWTCookieAuthentication``.
* ``refresh_token`` — ``Path=/api/v1/salons/<slug>/auth/refresh/``, so the
  browser only ever sends it to the refresh endpoint. The path is per-salon,
  which also scopes a browser session to the salon it logged into.

Both are ``HttpOnly`` and ``SameSite=Lax``, and both carry ``max-age`` equal to
their SIMPLE_JWT lifetime (docs/DECISIONS.md, Session renewal and session
lifetime, decided 21.09.2026), re-set on every refresh.

A third cookie, ``session_hint`` (value ``"1"``, ``Path=/``, refresh-lifetime
``max-age``, NOT httpOnly, ``SameSite``/``Secure`` as the access cookie), tells
the frontend whether a silent refresh is worth trying. It holds no secret and
the backend never trusts it. Logout clears all three.

``Secure`` follows the existing
settings-module split via ``SESSION_COOKIE_SECURE`` (unset -> ``False`` in
``config.settings.development``; ``True`` in ``config.settings.production``),
so the cookies work over plain HTTP on ``localhost`` and are TLS-only in prod.

CSRF protection for unsafe cookie-authenticated requests rides a separate
non-httpOnly `csrftoken` cookie + `X-CSRFToken` header check — see
`accounts/csrf.py` (docs/DECISIONS.md § Stage 12).
"""

import datetime as dt
from typing import cast

from django.conf import settings
from rest_framework.response import Response

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
SESSION_HINT_COOKIE = "session_hint"
SESSION_HINT_VALUE = "1"

_SAMESITE = "Lax"
_ACCESS_PATH = "/"


def refresh_cookie_path(salon_slug: str) -> str:
    return f"/api/v1/salons/{salon_slug}/auth/refresh/"


def _lifetime_seconds(key: str) -> int:
    lifetime = cast(dt.timedelta, settings.SIMPLE_JWT[key])
    return int(lifetime.total_seconds())


def set_auth_cookies(response: Response, *, access: str, refresh: str, salon_slug: str) -> None:
    secure = settings.SESSION_COOKIE_SECURE
    access_max_age = _lifetime_seconds("ACCESS_TOKEN_LIFETIME")
    refresh_max_age = _lifetime_seconds("REFRESH_TOKEN_LIFETIME")
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=access_max_age,
        path=_ACCESS_PATH,
        httponly=True,
        secure=secure,
        samesite=_SAMESITE,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=refresh_max_age,
        path=refresh_cookie_path(salon_slug),
        httponly=True,
        secure=secure,
        samesite=_SAMESITE,
    )
    # Presence hint for the frontend (see module docstring): readable by JS,
    # holds no secret, never used for authorization.
    response.set_cookie(
        SESSION_HINT_COOKIE,
        SESSION_HINT_VALUE,
        max_age=refresh_max_age,
        path=_ACCESS_PATH,
        httponly=False,
        secure=secure,
        samesite=_SAMESITE,
    )


def clear_auth_cookies(response: Response, *, salon_slug: str) -> None:
    # Path must match what set_auth_cookies wrote or the browser keeps the
    # cookie.
    response.delete_cookie(ACCESS_COOKIE, path=_ACCESS_PATH, samesite=_SAMESITE)
    response.delete_cookie(REFRESH_COOKIE, path=refresh_cookie_path(salon_slug), samesite=_SAMESITE)
    response.delete_cookie(SESSION_HINT_COOKIE, path=_ACCESS_PATH, samesite=_SAMESITE)
