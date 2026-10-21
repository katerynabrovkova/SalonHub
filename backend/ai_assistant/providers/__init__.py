from ai_assistant.providers.base import AssistantProvider
from ai_assistant.providers.mock import MockAssistantProvider


def get_assistant_provider() -> AssistantProvider:
    """
    The one place the assistant gets its provider (docs/DECISIONS.md §
    "Stage 16 planning", "Sub-step 3 details"). Returns the mock until
    sub-step 10 adds the OpenAI adapter and a setting to choose it. Tests
    patch it where it is imported.
    """
    return MockAssistantProvider()
