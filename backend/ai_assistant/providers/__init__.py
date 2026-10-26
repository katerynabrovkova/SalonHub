from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from ai_assistant.checks import check_assistant_provider_settings
from ai_assistant.providers.base import AssistantProvider
from ai_assistant.providers.mock import MockAssistantProvider


def get_assistant_provider() -> AssistantProvider:
    """
    The one place the assistant gets its provider (docs/DECISIONS.md §
    "Stage 16 planning", "Sub-step 3 details" and "Sub-step 10 details").
    ASSISTANT_PROVIDER picks it: "mock" (default) or "openai". A setting
    the system check rejects raises ImproperlyConfigured here too, as a
    second guard. The OpenAI adapter is imported only when chosen, so the
    SDK is never loaded with the mock. Tests patch this function where it
    is imported.
    """
    errors = check_assistant_provider_settings(app_configs=None)
    if errors:
        raise ImproperlyConfigured(errors[0].msg)
    if settings.ASSISTANT_PROVIDER == "openai":
        from ai_assistant.providers.openai import OpenAIAssistantProvider

        return OpenAIAssistantProvider()
    return MockAssistantProvider()
