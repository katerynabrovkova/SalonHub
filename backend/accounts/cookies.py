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

Both are ``HttpOnly`` and ``SameSite=Lax``. ``Secure`` follows the existing
settings-module split via ``SESSION_COOKIE_SECURE`` (unset -> ``False`` in
``config.settings.development``; ``True`` in ``config.settings.production``),
so the cookies work over plain HTTP on ``localhost`` and are TLS-only in prod.

CSRF protection for unsafe methods is deliberately NOT implemented here — it
rides a separate non-httpOnly cookie + header check, deferred to its own
sub-step (docs/DECISIONS.md § Stage 12).
"""

from django.conf import settings
from rest_framework.response import Response

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"

_SAMESITE = "Lax"
_ACCESS_PATH = "/"


def refresh_cookie_path(salon_slug: str) -> str:
    return f"/api/v1/salons/{salon_slug}/auth/refresh/"


def set_auth_cookies(response: Response, *, access: str, refresh: str, salon_slug: str) -> None:
    secure = settings.SESSION_COOKIE_SECURE
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        path=_ACCESS_PATH,
        httponly=True,
        secure=secure,
        samesite=_SAMESITE,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        path=refresh_cookie_path(salon_slug),
        httponly=True,
        secure=secure,
        samesite=_SAMESITE,
    )


def clear_auth_cookies(response: Response, *, salon_slug: str) -> None:
    # Path must match what set_auth_cookies wrote or the browser keeps the
    # cookie.
    response.delete_cookie(ACCESS_COOKIE, path=_ACCESS_PATH, samesite=_SAMESITE)
    response.delete_cookie(REFRESH_COOKIE, path=refresh_cookie_path(salon_slug), samesite=_SAMESITE)
