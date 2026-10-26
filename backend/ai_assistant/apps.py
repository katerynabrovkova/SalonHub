from django.apps import AppConfig
from django.core import checks


class AiAssistantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ai_assistant"

    def ready(self) -> None:
        from ai_assistant.checks import check_assistant_provider_settings

        checks.register(check_assistant_provider_settings)
