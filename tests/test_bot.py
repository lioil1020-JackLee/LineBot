from __future__ import annotations

import pytest
from types import SimpleNamespace

from linebot_app.bot import handle_webhook, reply_text


class _FakeBotService:
    def handle_user_message(self, *, line_user_id: str, text: str) -> str:
        return "ok"


def test_handle_webhook_invalid_signature() -> None:
    with pytest.raises(ValueError, match="Invalid LINE signature"):
        handle_webhook(body='{"events": []}', signature="invalid", bot_service=_FakeBotService())


def test_reply_text_swallows_line_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeApiClient:
        def __init__(self, _configuration: object) -> None:
            pass

        def __enter__(self) -> "_FakeApiClient":
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

    # 不應拋出例外
    reply_text("reply-token", "hello")


def test_group_message_without_self_mention_skips_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="group", user_id="u-group"),
        message=SimpleNamespace(text="大家好", mention=None),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [event])

    called = {"reply": False, "service": False}

    def _fake_reply_text(_reply_token: str, _text: str) -> None:
        called["reply"] = True

    class _Service:
        def handle_user_message(self, *, line_user_id: str, text: str) -> str:
            called["service"] = True
            return "ok"

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(body='{"events": []}', signature="valid", bot_service=_Service())

    assert called["service"] is False
    assert called["reply"] is False


def test_group_message_with_self_mention_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="group", user_id="u-group"),
        message=SimpleNamespace(
            text="@機器人 請自我介紹",
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
    handle_webhook(body='{"events": []}', signature="valid", bot_service=_Service())

    assert got["replied"] is True
    assert got["text"] == "請自我介紹"


def test_group_message_with_sdk_mention_format_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="group", user_id="u-group"),
        message=SimpleNamespace(
            text="@小助手 幫我總結",
            mention=SimpleNamespace(
                # LINE SDK mentionee model may only provide type/index/length
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
    handle_webhook(body='{"events": []}', signature="valid", bot_service=_Service())

    assert got["replied"] is True
    assert got["text"] == "幫我總結"


def test_group_message_with_plain_name_call_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="group", user_id="u-group"),
        message=SimpleNamespace(text="@lioil_bot 幫我整理重點", mention=None),
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
    handle_webhook(body='{"events": []}', signature="valid", bot_service=_Service())

    assert got["replied"] is True
    assert got["text"] == "幫我整理重點"


def test_image_message_runs_ocr_and_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="user", user_id="u-img"),
        message=SimpleNamespace(id="img-1"),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [])
    monkeypatch.setattr("linebot_app.bot._iter_image_events", lambda _events: [event])
    monkeypatch.setattr("linebot_app.bot._download_line_message_content", lambda _id: b"fake-bytes")

    class _FakeOCR:
        def extract_text(self, _image_bytes: bytes) -> str:
            return "這是圖片中的文字"

    monkeypatch.setattr("linebot_app.bot.image_ocr_service", _FakeOCR())

    got = {"replied": False, "text": ""}

    def _fake_reply_text(_reply_token: str, text: str) -> None:
        got["replied"] = True
        got["text"] = text

    class _Service:
        def handle_user_message(self, *, line_user_id: str, text: str) -> str:
            assert "這是圖片中的文字" in text
            return "已解析圖片"

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(body='{"events": []}', signature="valid", bot_service=_Service())

    assert got["replied"] is True
    assert got["text"] == "已解析圖片"


def test_image_message_without_text_returns_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="user", user_id="u-img"),
        message=SimpleNamespace(id="img-1"),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [])
    monkeypatch.setattr("linebot_app.bot._iter_image_events", lambda _events: [event])
    monkeypatch.setattr("linebot_app.bot._download_line_message_content", lambda _id: b"fake-bytes")

    class _FakeOCR:
        def extract_text(self, _image_bytes: bytes) -> str:
            return ""

    monkeypatch.setattr("linebot_app.bot.image_ocr_service", _FakeOCR())

    got = {"text": ""}

    def _fake_reply_text(_reply_token: str, text: str) -> None:
        got["text"] = text

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(body='{"events": []}', signature="valid", bot_service=_FakeBotService())

    assert "沒有辨識到可用文字" in got["text"]


def test_image_message_skipped_when_ocr_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="user", user_id="u-img"),
        message=SimpleNamespace(id="img-1"),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [])
    monkeypatch.setattr("linebot_app.bot._iter_image_events", lambda _events: [event])
    monkeypatch.setattr("linebot_app.bot.settings", SimpleNamespace(image_ocr_enabled=False, line_bot_name=""))

    called = {"reply": False}

    def _fake_reply_text(_reply_token: str, _text: str) -> None:
        called["reply"] = True

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(body='{"events": []}', signature="valid", bot_service=_FakeBotService())

    assert called["reply"] is False


def test_file_message_runs_parser_and_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="user", user_id="u-file"),
        message=SimpleNamespace(id="file-1", file_name="demo.pdf"),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [])
    monkeypatch.setattr("linebot_app.bot._iter_image_events", lambda _events: [])
    monkeypatch.setattr("linebot_app.bot._iter_file_events", lambda _events: [event])
    monkeypatch.setattr("linebot_app.bot._download_line_message_content", lambda _id: b"fake-bytes")

    class _FakeParser:
        def extract_text(self, *, file_name: str, content: bytes) -> tuple[str, str | None]:
            assert file_name == "demo.pdf"
            assert content == b"fake-bytes"
            return "文件重點 A\n文件重點 B", None

    monkeypatch.setattr("linebot_app.bot.document_parser_service", _FakeParser())

    got = {"text": ""}

    def _fake_reply_text(_reply_token: str, text: str) -> None:
        got["text"] = text

    class _Service:
        def handle_user_message(self, *, line_user_id: str, text: str) -> str:
            assert "文件重點 A" in text
            return "已整理檔案"

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(body='{"events": []}', signature="valid", bot_service=_Service())

    assert got["text"] == "已整理檔案"


def test_file_message_skipped_when_parser_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="user", user_id="u-file"),
        message=SimpleNamespace(id="file-1", file_name="demo.pdf"),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [])
    monkeypatch.setattr("linebot_app.bot._iter_image_events", lambda _events: [])
    monkeypatch.setattr("linebot_app.bot._iter_file_events", lambda _events: [event])
    monkeypatch.setattr(
        "linebot_app.bot.settings",
        SimpleNamespace(image_ocr_enabled=True, file_parser_enabled=False, line_bot_name=""),
    )

    called = {"reply": False}

    def _fake_reply_text(_reply_token: str, _text: str) -> None:
        called["reply"] = True

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(body='{"events": []}', signature="valid", bot_service=_FakeBotService())

    assert called["reply"] is False


def test_group_file_message_replies_without_mention(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="group", user_id="u-group"),
        message=SimpleNamespace(id="file-1", file_name="demo.pdf", mention=None),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [])
    monkeypatch.setattr("linebot_app.bot._iter_image_events", lambda _events: [])
    monkeypatch.setattr("linebot_app.bot._iter_file_events", lambda _events: [event])
    monkeypatch.setattr("linebot_app.bot._should_reply", lambda _event: True)
    monkeypatch.setattr("linebot_app.bot._download_line_message_content", lambda _id: b"fake-bytes")

    class _FakeParser:
        def extract_text(self, *, file_name: str, content: bytes) -> tuple[str, str | None]:
            return "群組文件文字", None

    monkeypatch.setattr("linebot_app.bot.document_parser_service", _FakeParser())

    got = {"text": ""}

    def _fake_reply_text(_reply_token: str, text: str) -> None:
        got["text"] = text

    class _Service:
        def handle_user_message(self, *, line_user_id: str, text: str) -> str:
            return "群組檔案已處理"

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(body='{"events": []}', signature="valid", bot_service=_Service())

    assert got["text"] == "群組檔案已處理"


def test_file_message_still_works_when_image_ocr_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        reply_token="reply-token",
        source=SimpleNamespace(type="user", user_id="u-file"),
        message=SimpleNamespace(id="file-1", file_name="demo.pdf"),
    )

    monkeypatch.setattr("linebot_app.bot.parser.parse", lambda _body, _sig: [event])
    monkeypatch.setattr("linebot_app.bot._iter_text_events", lambda _events: [])
    monkeypatch.setattr("linebot_app.bot._iter_image_events", lambda _events: [])
    monkeypatch.setattr("linebot_app.bot._iter_file_events", lambda _events: [event])
    monkeypatch.setattr("linebot_app.bot._download_line_message_content", lambda _id: b"fake-bytes")
    monkeypatch.setattr(
        "linebot_app.bot.settings",
        SimpleNamespace(image_ocr_enabled=False, file_parser_enabled=True, line_bot_name=""),
    )

    class _FakeParser:
        def extract_text(self, *, file_name: str, content: bytes) -> tuple[str, str | None]:
            return "文件內容", None

    monkeypatch.setattr("linebot_app.bot.document_parser_service", _FakeParser())

    got = {"text": ""}

    def _fake_reply_text(_reply_token: str, text: str) -> None:
        got["text"] = text

    class _Service:
        def handle_user_message(self, *, line_user_id: str, text: str) -> str:
            return "檔案正常處理"

    monkeypatch.setattr("linebot_app.bot.reply_text", _fake_reply_text)
    handle_webhook(body='{"events": []}', signature="valid", bot_service=_Service())

    assert got["text"] == "檔案正常處理"
