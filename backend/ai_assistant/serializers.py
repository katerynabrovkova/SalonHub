from rest_framework import serializers

MAX_MESSAGE_LENGTH = 2000


class AssistantMessageSerializer(serializers.Serializer):
    """The customer's message (docs/DECISIONS.md § "Sub-step 7 details").
    Trimmed before the blank and length checks, so whitespace alone is
    blank."""

    message = serializers.CharField(max_length=MAX_MESSAGE_LENGTH, trim_whitespace=True)
