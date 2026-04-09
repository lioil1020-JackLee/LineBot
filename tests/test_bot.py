from __future__ import annotations

from types import SimpleNamespace

import pytest

from linebot_app.bot import handle_webhook, reply_text


class _FakeChatOrchestrator:
    def handle_user_message(self, *, line_user_id: str, text: str) -> str:
        return "ok"


def test_handle_webhook_invalid_signature() -> None:
    with pytest.raises(ValueError, match="Invalid LINE signature"):
        handle_webhook(
            body='{"events": []}',
            signature="invalid",
            chat_orchestrator=_FakeChatOrchestrator(),
        )


def test_reply_text_swallows_line_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeApiClient:
        def __init__(self, _configuration: object) -> None:
            pass

        def __enter__(self) -> _FakeApiClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            return False

    class _FakeMessagingApi:
        def __init__(self, _api_client: object) -> None:
            pass

        def reply_message(self, _request: object) -> None:
            raise RuntimeError("line reply failed")

    monkeypatch.setattr("linebot_app.bot.ApiClient", _FakeApiClient)
    monkeypatch.setattr("linebot_app.bot.MessagingApi", _FakeMessagingApi)

    reply_text("reply-token", "hello")


def test_group_message_without_self_mention_skips_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="group", user_id="u-group"),
        message=SimpleNamespace(text="大家晚安", mention=None),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [event])
    monkeypatch.setattr(
        "linebot_app.bot.settings",
        SimpleNamespace(line_bot_name="lioil_bot", line_group_require_mention=True),
    )

    called = {"reply": False, "service": False}

    def _fake_reply_text(_reply_token: str, _text: str) -> None:
        called["reply"] = True

    class _Service:
        def handle_user_message(self, *, line_user_id: str, text: str) -> str:
            called["service"] = True
            return "ok"

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(body='{"events": []}', signature="valid", chat_orchestrator=_Service())

    assert called["service"] is False
    assert called["reply"] is False


def test_group_message_without_mention_replies_when_switch_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="group", user_id="u-group"),
        message=SimpleNamespace(text="大家晚安", mention=None),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [event])
    monkeypatch.setattr(
        "linebot_app.bot.settings",
        SimpleNamespace(line_bot_name="lioil_bot", line_group_require_mention=False),
    )

    called = {"reply": False, "service": False}

    def _fake_reply_text(_reply_token: str, _text: str) -> None:
        called["reply"] = True

    class _Service:
        def handle_user_message(self, *, line_user_id: str, text: str) -> str:
            called["service"] = True
            return "ok"

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(body='{"events": []}', signature="valid", chat_orchestrator=_Service())

    assert called["service"] is True
    assert called["reply"] is True


def test_group_message_with_self_mention_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="group", user_id="u-group"),
        message=SimpleNamespace(
            text="@bot 你好嗎",
            mention=SimpleNamespace(
                mentionees=[SimpleNamespace(index=0, length=4, is_self=True)]
            ),
        ),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [event])

    got = {"text": "", "replied": False}

    def _fake_reply_text(_reply_token: str, _text: str) -> None:
        got["replied"] = True

    class _Service:
        def handle_user_message(self, *, line_user_id: str, text: str) -> str:
            got["text"] = text
            return "ok"

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(body='{"events": []}', signature="valid", chat_orchestrator=_Service())

    assert got["replied"] is True
    assert got["text"] == "你好嗎"


def test_group_message_with_sdk_mention_format_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="group", user_id="u-group"),
        message=SimpleNamespace(
            text="@bot 請幫我查一下",
            mention=SimpleNamespace(
                mentionees=[SimpleNamespace(type="user", index=0, length=4)]
            ),
        ),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [event])

    got = {"text": "", "replied": False}

    def _fake_reply_text(_reply_token: str, _text: str) -> None:
        got["replied"] = True

    class _Service:
        def handle_user_message(self, *, line_user_id: str, text: str) -> str:
            got["text"] = text
            return "ok"

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(body='{"events": []}', signature="valid", chat_orchestrator=_Service())

    assert got["replied"] is True
    assert got["text"] == "請幫我查一下"


def test_group_message_with_plain_name_call_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="group", user_id="u-group"),
        message=SimpleNamespace(text="@lioil_bot 幫我查天氣", mention=None),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [event])
    monkeypatch.setattr("linebot_app.bot.settings", SimpleNamespace(line_bot_name="lioil_bot"))

    got = {"text": "", "replied": False}

    def _fake_reply_text(_reply_token: str, _text: str) -> None:
        got["replied"] = True

    class _Service:
        def handle_user_message(self, *, line_user_id: str, text: str) -> str:
            got["text"] = text
            return "ok"

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(body='{"events": []}', signature="valid", chat_orchestrator=_Service())

    assert got["replied"] is True
    assert got["text"] == "幫我查天氣"


def test_non_text_events_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="user", user_id="u-x"),
        message=SimpleNamespace(id="x-1"),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [])

    called = {"replied": False}

    def _fake_reply_text(_reply_token: str, _text: str) -> None:
        called["replied"] = True

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(
        body='{"events": []}',
        signature="valid",
        chat_orchestrator=_FakeChatOrchestrator(),
    )

    assert called["replied"] is False
