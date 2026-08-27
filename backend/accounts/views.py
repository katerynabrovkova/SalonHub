"""
Auth views under /api/v1/auth/ (docs/ARCHITECTURE.md § 3, § 13).

Only login/refresh/logout remain here: the `User`-based registration,
email-verification and password-reset endpoints were removed in Stage
3-R.D.2 (docs/DECISIONS.md), and their `Account`-based replacements land
under the salon prefix in 3-R.D.3-D.5. These three still authenticate
against `AUTH_USER_MODEL` and move under `/api/v1/salons/<slug>/auth/` in
3-R.E.

Login and refresh opt out of the project-wide IsAuthenticated default with
AllowAny; logout relies on that default deliberately — it needs a valid
access token to blacklist a refresh token (docs/DECISIONS.md § Stage 3
decisions).
"""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.serializers import LogoutSerializer
from core.exceptions import InvalidOrExpiredTokenError


class LoginView(TokenObtainPairView):
    # TokenViewBase (simplejwt) has no explicit annotation on
    # permission_classes, so django-stubs infers its type from `()` — the
    # empty-tuple literal type — and any non-empty override is flagged
    # regardless of list vs. tuple syntax. Known stub friction, not a real
    # type error.
    permission_classes = (AllowAny,)  # type: ignore[assignment]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class RefreshView(TokenRefreshView):
    permission_classes = (AllowAny,)  # type: ignore[assignment]


class LogoutView(APIView):
    def post(self, request: Request) -> Response:
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError as exc:
            raise InvalidOrExpiredTokenError("Invalid or already-used refresh token.") from exc
        return Response(status=status.HTTP_205_RESET_CONTENT)
