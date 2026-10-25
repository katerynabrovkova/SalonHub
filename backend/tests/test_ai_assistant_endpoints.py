"""
Stage 16 sub-step 7 — the assistant endpoints (docs/DECISIONS.md §
"Sub-step 7 details").

    POST /api/v1/salons/<slug>/assistant/messages/  (NDJSON stream)
    GET  /api/v1/salons/<slug>/assistant/history/

The provider is a scripted MockAssistantProvider patched into
`ai_assistant.views.get_assistant_provider`; what the model received is read
back from `provider.calls`. No test here depends on free slots, so none
freezes `now`. Session history goes to Redis DB 15 through conftest's
autouse `_assistant_redis` fixture.
"""

import datetime as dt
import json
import logging
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from ai_assistant import tools as tools_module
from ai_assistant.providers.base import ChatMessage, Finish, TextDelta, ToolCall
from ai_assistant.providers.mock import MockAssistantProvider, ScriptTurn
from ai_assistant.session_store import append_turn, load_history, new_session_id
from catalog.models import Service, ServiceCategory
from core.exceptions import AssistantProviderError
from core.tenancy import get_current_salon_id, tenant_context

pytestmark = pytest.mark.django_db

SESSION_HEADER = "X-Assistant-Session"
ACCESS_COOKIE = "access_token"
GARBAGE = "this-is-not-a-jwt"
ALLOWED_ORIGIN = "http://bella-demo.localhost:3000"
CUSTOMER_TEXT = "my-private-customer-text-7f3a"
EXCEPTION_TEXT = "secret-exception-detail-91c2"


@pytest.fixture
def enabled_salon(salon):
    salon.assistant_enabled = True
    salon.save(update_fields=["assistant_enabled"])
    return salon


def _messages_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/assistant/messages/"


def _history_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/assistant/history/"


def _finish() -> Finish:
    return Finish(input_tokens=10, output_tokens=5)


def _reply(text: str = "Hi! How can I help?") -> list[ScriptTurn]:
    return [[TextDelta(text=text), _finish()]]


def _use_provider(monkeypatch, script: list[ScriptTurn] | None) -> MockAssistantProvider:
    provider = MockAssistantProvider(script)
    monkeypatch.setattr("ai_assistant.views.get_assistant_provider", lambda: provider)
    return provider


def _post(
    slug: str,
    body: Any,
    *,
    session_id: str | None = None,
    lang: str | None = None,
    client: APIClient | None = None,
    **extra: Any,
):
    client = client or APIClient()
    url = _messages_url(slug) + (f"?lang={lang}" if lang else "")
    if session_id is not None:
        extra["HTTP_X_ASSISTANT_SESSION"] = session_id
    return client.post(url, body, format="json", **extra)


def _get_history(slug: str, *, session_id: str | None = None, client: APIClient | None = None):
    client = client or APIClient()
    extra = {"HTTP_X_ASSISTANT_SESSION": session_id} if session_id is not None else {}
    return client.get(_history_url(slug), **extra)


def _assert_stream(response) -> None:
    assert response.status_code == 200
    assert response.streaming
    assert response["Content-Type"] == "application/x-ndjson"


def _body(response) -> bytes:
    """The whole streamed body; the stream is consumed fully."""
    _assert_stream(response)
    return b"".join(response.streaming_content)


def _events(response) -> list[dict[str, Any]]:
    lines = _body(response).decode().splitlines()
    return [json.loads(line) for line in lines if line]


def _history(salon, session_id: str) -> list[dict[str, str]]:
    with tenant_context(salon.id):
        return load_history(session_id)


def _seed(salon, session_id: str, *turns: tuple[str, str]) -> None:
    with tenant_context(salon.id):
        for user_text, assistant_text in turns:
            append_turn(session_id, user_text, assistant_text)


def _first_call_messages(provider: MockAssistantProvider) -> tuple[ChatMessage, ...]:
    messages = provider.calls[0]["messages"]
    assert isinstance(messages, tuple)
    return messages


def _error_code(response) -> str | None:
    data = getattr(response, "data", None)
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return data["error"].get("code")
    return None


def _expired_access_token() -> str:
    token = AccessToken()
    token["user_id"] = "1"
    token["identity_model"] = "account"
    token.set_exp(
        from_time=timezone.now() - dt.timedelta(days=1), lifetime=dt.timedelta(minutes=15)
    )
    return str(token)


# --- 1. assistant disabled -----------------------------------------------


def test_disabled_assistant_returns_404_envelope_on_both_endpoints(monkeypatch, salon):
    provider = _use_provider(monkeypatch, _reply())
    assert salon.assistant_enabled is False

    post = _post(salon.slug, {"message": "Hello"})
    get = _get_history(salon.slug)

    for response in (post, get):
        assert response.status_code == 404
        assert not response.streaming
        assert _error_code(response) == "not_found"
    assert provider.calls == []


# --- 2-5. session header --------------------------------------------------


def test_post_without_header_streams_ndjson_with_a_new_session_id(monkeypatch, enabled_salon):
    _use_provider(monkeypatch, _reply("Hi! How can I help?"))

    response = _post(enabled_salon.slug, {"message": "Hello"})
    events = _events(response)

    session_id = response[SESSION_HEADER]
    assert session_id
    assert [event["type"] for event in events] == ["text", "done"]
    assert events[0]["text"] == "Hi! How can I help?"
    assert _history(enabled_salon, session_id) == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi! How can I help?"},
    ]


def test_post_with_a_valid_id_continues_that_conversation(monkeypatch, enabled_salon):
    session_id = new_session_id()
    _seed(enabled_salon, session_id, ("Do you do manicures?", "Yes, we do."))
    provider = _use_provider(monkeypatch, _reply("At 10:00."))

    response = _post(enabled_salon.slug, {"message": "When?"}, session_id=session_id)
    _events(response)

    assert response[SESSION_HEADER] == session_id
    assert [(m.role, m.content) for m in _first_call_messages(provider)] == [
        ("user", "Do you do manicures?"),
        ("assistant", "Yes, we do."),
        ("user", "When?"),
    ]


def test_post_with_an_unknown_id_starts_a_new_conversation_silently(monkeypatch, enabled_salon):
    unknown = new_session_id()
    provider = _use_provider(monkeypatch, _reply())

    response = _post(enabled_salon.slug, {"message": "Hello"}, session_id=unknown)
    events = _events(response)

    new_id = response[SESSION_HEADER]
    assert new_id
    assert new_id != unknown
    assert all(event["type"] != "error" for event in events)
    assert [(m.role, m.content) for m in _first_call_messages(provider)] == [("user", "Hello")]


def test_post_with_another_salons_id_starts_a_new_conversation(
    monkeypatch, enabled_salon, other_salon
):
    foreign_id = new_session_id()
    _seed(other_salon, foreign_id, ("Foreign question", "Foreign answer"))
    provider = _use_provider(monkeypatch, _reply())

    response = _post(enabled_salon.slug, {"message": "Hello"}, session_id=foreign_id)
    _events(response)

    assert response[SESSION_HEADER] != foreign_id
    assert [(m.role, m.content) for m in _first_call_messages(provider)] == [("user", "Hello")]
    assert _history(other_salon, foreign_id) == [
        {"role": "user", "content": "Foreign question"},
        {"role": "assistant", "content": "Foreign answer"},
    ]


# --- 6. validation --------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing"),
        pytest.param({"message": ""}, id="empty"),
        pytest.param({"message": " \t\n "}, id="whitespace_only"),
        pytest.param({"message": "x" * 2001}, id="2001_characters"),
    ],
)
def test_invalid_message_returns_400_envelope_not_a_stream(monkeypatch, enabled_salon, body):
    provider = _use_provider(monkeypatch, _reply())

    response = _post(enabled_salon.slug, body)

    assert response.status_code == 400
    assert not response.streaming
    assert _error_code(response) == "invalid"
    assert "message" in response.data["error"]["details"]
    assert provider.calls == []


def test_message_of_exactly_2000_characters_streams(monkeypatch, enabled_salon):
    _use_provider(monkeypatch, _reply())

    response = _post(enabled_salon.slug, {"message": "x" * 2000})

    assert [event["type"] for event in _events(response)] == ["text", "done"]


# --- 7. stale access cookie -----------------------------------------------


@pytest.mark.parametrize("cookie", ["garbage", "expired"])
def test_invalid_access_cookie_is_ignored_on_both_endpoints(monkeypatch, enabled_salon, cookie):
    value = GARBAGE if cookie == "garbage" else _expired_access_token()
    _use_provider(monkeypatch, _reply() * 2)

    def _client(with_cookie: bool) -> APIClient:
        client = APIClient()
        if with_cookie:
            client.cookies[ACCESS_COOKIE] = value
        return client

    for with_cookie in (False, True):
        post = _post(enabled_salon.slug, {"message": "Hello"}, client=_client(with_cookie))
        assert [event["type"] for event in _events(post)] == ["text", "done"]

        get = _get_history(enabled_salon.slug, client=_client(with_cookie))
        assert get.status_code == 200
        assert get.json() == {"messages": []}


# --- 8. tenant isolation through the endpoint (KEY) -----------------------


def test_tool_mid_stream_sees_only_the_bound_salons_data(
    monkeypatch, enabled_salon, other_salon, service_category
):
    with tenant_context(enabled_salon.id):
        Service.objects.create(
            salon=enabled_salon,
            category=service_category,
            name={"en": "Own manicure"},
            duration_minutes=60,
            price="500.00",
        )
    with tenant_context(other_salon.id):
        other_category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Nails"})
        Service.objects.create(
            salon=other_salon,
            category=other_category,
            name={"en": "Foreign manicure"},
            duration_minutes=60,
            price="500.00",
        )
    provider = _use_provider(
        monkeypatch,
        [
            [ToolCall(id="call-1", name="list_services", arguments={}), _finish()],
            [TextDelta(text="We do manicures."), _finish()],
        ],
    )

    response = _post(enabled_salon.slug, {"message": "What do you offer?"})
    events = _events(response)

    assert events == [{"type": "text", "text": "We do manicures."}, {"type": "done"}]
    second_call = provider.calls[1]["messages"]
    assert isinstance(second_call, tuple)
    [tool_message] = [m for m in second_call if m.role == "tool"]
    assert "Own manicure" in tool_message.content
    assert "Foreign manicure" not in tool_message.content


# --- 9-10. errors mid-stream ----------------------------------------------


def test_unexpected_exception_mid_stream_becomes_internal_error(monkeypatch, caplog, enabled_salon):
    spec, _real = tools_module.TOOLS["list_services"]

    def _boom(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(EXCEPTION_TEXT)

    monkeypatch.setitem(tools_module.TOOLS, "list_services", (spec, _boom))
    _use_provider(
        monkeypatch,
        [
            [
                TextDelta(text="Let me check. "),
                ToolCall(id="call-1", name="list_services", arguments={}),
                _finish(),
            ]
        ],
    )

    with caplog.at_level(logging.DEBUG):
        response = _post(enabled_salon.slug, {"message": CUSTOMER_TEXT})
        body = _body(response)

    events = [json.loads(line) for line in body.decode().splitlines() if line]
    assert events[-1] == {"type": "error", "code": "internal_error"}
    assert EXCEPTION_TEXT.encode() not in body
    assert b"RuntimeError" not in body
    assert CUSTOMER_TEXT.encode() not in body
    assert _history(enabled_salon, response[SESSION_HEADER]) == []
    assert CUSTOMER_TEXT not in caplog.text


def test_provider_error_mid_stream_keeps_its_own_code(monkeypatch, enabled_salon):
    _use_provider(monkeypatch, [[TextDelta(text="Let me "), AssistantProviderError()]])

    response = _post(enabled_salon.slug, {"message": "Hello"})
    events = _events(response)

    assert events[-1] == {"type": "error", "code": "assistant_provider_error"}
    assert _history(enabled_salon, response[SESSION_HEADER]) == []


# --- 11. history ----------------------------------------------------------


def test_history_with_a_valid_id_returns_the_messages_in_order(enabled_salon):
    session_id = new_session_id()
    _seed(enabled_salon, session_id, ("First?", "One."), ("Second?", "Two."))

    response = _get_history(enabled_salon.slug, session_id=session_id)

    assert response.status_code == 200
    assert response.json() == {
        "messages": [
            {"role": "user", "content": "First?"},
            {"role": "assistant", "content": "One."},
            {"role": "user", "content": "Second?"},
            {"role": "assistant", "content": "Two."},
        ]
    }


@pytest.mark.parametrize("case", ["no_header", "unknown_id", "other_salons_id"])
def test_history_without_a_usable_id_is_empty_not_404(enabled_salon, other_salon, case):
    session_id: str | None = None
    if case == "unknown_id":
        session_id = new_session_id()
    elif case == "other_salons_id":
        session_id = new_session_id()
        _seed(other_salon, session_id, ("Foreign question", "Foreign answer"))

    response = _get_history(enabled_salon.slug, session_id=session_id)

    assert response.status_code == 200
    assert response.json() == {"messages": []}


# --- 12. CORS -------------------------------------------------------------


def test_cors_preflight_allows_the_session_header(enabled_salon):
    response = APIClient().options(
        _messages_url(enabled_salon.slug),
        HTTP_ORIGIN=ALLOWED_ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-assistant-session",
    )

    allowed = [h.strip().lower() for h in response["Access-Control-Allow-Headers"].split(",")]
    assert "x-assistant-session" in allowed


def test_cors_post_exposes_the_session_header(monkeypatch, enabled_salon):
    _use_provider(monkeypatch, _reply())

    response = _post(enabled_salon.slug, {"message": "Hello"}, HTTP_ORIGIN=ALLOWED_ORIGIN)
    _events(response)

    exposed = response.get("Access-Control-Expose-Headers", "")
    assert "x-assistant-session" in [h.strip().lower() for h in exposed.split(",")]


# --- 13. site language ----------------------------------------------------


def test_lang_query_parameter_reaches_the_orchestrator(monkeypatch, enabled_salon):
    provider = _use_provider(monkeypatch, _reply())

    response = _post(enabled_salon.slug, {"message": "Привіт"}, lang="uk")
    _events(response)

    assert 'The site language is "uk".' in provider.calls[0]["system"]


# --- 14. client disconnect ------------------------------------------------


def test_client_disconnect_stores_nothing_and_logs_cancelled(monkeypatch, caplog, enabled_salon):
    _use_provider(monkeypatch, [[TextDelta(text="First "), TextDelta(text="second"), _finish()]])

    with caplog.at_level(logging.INFO):
        response = _post(enabled_salon.slug, {"message": "Hello"})
        _assert_stream(response)
        next(iter(response.streaming_content))
        response.close()

    assert _history(enabled_salon, response[SESSION_HEADER]) == []
    outcomes = [r.getMessage() for r in caplog.records if r.name == "ai_assistant.orchestrator"]
    assert len(outcomes) == 1
    assert "outcome=cancelled" in outcomes[0]


# --- 15. tenant context released ------------------------------------------


def test_no_tenant_context_is_left_bound_after_the_stream(monkeypatch, enabled_salon):
    _use_provider(monkeypatch, _reply())

    response = _post(enabled_salon.slug, {"message": "Hello"})
    _events(response)

    assert get_current_salon_id() is None
