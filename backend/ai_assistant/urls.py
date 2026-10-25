from django.urls import path

from ai_assistant import views

app_name = "ai_assistant"

urlpatterns = [
    path(
        "assistant/messages/",
        views.AssistantMessageCreateView.as_view(),
        name="assistant-message-create",
    ),
    path(
        "assistant/history/",
        views.AssistantHistoryView.as_view(),
        name="assistant-history",
    ),
]
