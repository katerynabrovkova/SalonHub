"""
Manual CSRF enforcement for cookie-borne JWT sessions (docs/DECISIONS.md
§ Stage 12 "CSRF protection resolved").

DRF only runs a CSRF check inside ``SessionAuthentication.enforce_csrf()``,
and ``APIView.as_view()`` marks every DRF view ``csrf_exempt`` so
``CsrfViewMiddleware`` never fires either. Our session rides an httpOnly
cookie (an ambient credential the browser attaches automatically), so unsafe
requests carrying it need the same CSRF guard a session cookie would get.

``enforce_csrf`` mirrors DRF's reference implementation exactly: instantiate
Django's ``CsrfViewMiddleware`` (via DRF's ``CSRFCheck`` subclass, whose
``_reject`` returns the reason string instead of a response), call
``process_request`` then ``process_view``, and raise ``PermissionDenied`` on
any failure reason. It honours ``request._dont_enforce_csrf_checks`` (set by
the Django test client unless ``enforce_csrf_checks=True``) through the
middleware, same as the session path.

Applies to: the cookie authentication class (for Account-authenticated
writes) and the three ``AllowAny`` cookie-driven auth views
(login/refresh/logout), which the auth class never runs for. Guest-token and
webhook endpoints are deliberately untouched — no ambient cookie, already
CSRF-immune.
"""

from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS
from rest_framework.request import Request


def _dummy_get_response(request: object) -> None:  # pragma: no cover
    return None


def enforce_csrf(request: Request) -> None:
    """Raise ``PermissionDenied`` unless *request* carries a valid Django CSRF
    token (``csrftoken`` cookie + ``X-CSRFToken`` header)."""
    check = CSRFCheck(_dummy_get_response)
    check.process_request(request)  # populates request.META['CSRF_COOKIE']
    reason = check.process_view(request, None, (), {})
    if reason:
        raise PermissionDenied(f"CSRF Failed: {reason}")


def enforce_csrf_on_unsafe(request: Request) -> None:
    """``enforce_csrf`` gated on an unsafe HTTP method — the guard the
    login/refresh/logout views call."""
    if request.method not in SAFE_METHODS:
        enforce_csrf(request)
