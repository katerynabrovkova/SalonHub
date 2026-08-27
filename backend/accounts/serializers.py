"""
Auth serializers (docs/ARCHITECTURE.md § 3).

`LogoutSerializer` validates the refresh token to be blacklisted. The
registration, email-verification and password-reset serializers were
removed in Stage 3-R.D.2 (docs/DECISIONS.md); their `Account`-based
replacements land under the salon prefix in 3-R.D.3-D.5.
"""

from rest_framework import serializers


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()
