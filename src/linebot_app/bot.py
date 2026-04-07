from __future__ import annotations

from collections.abc import Iterable
import logging
import re

from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import ImageMessageContent, MessageEvent, TextMessageContent
from linebot.v3.webhooks import FileMessageContent

from .config import get_settings
from .services.document_parser_service import DocumentParserService
from .services.image_ocr_service import ImageOCRService
from .services.bot_service import BotService

settings = get_settings()
parser = WebhookParser(settings.line_channel_secret or "development-secret")
logger = logging.getLogger(__name__)
image_ocr_service = ImageOCRService()
document_parser_service = DocumentParserService()


def reply_text(reply_token: str, text: str) -> None:
    configuration = Configuration(access_token=settings.line_channel_access_token)
    try:
        with ApiClient(configuration) as api_client:
            api = MessagingApi(api_client)
            api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=text)],
                )
            )
    except Exception:
        # LINE API 失敗時僅記錄錯誤，避免讓 webhook 直接回 500 造成重試風暴。
        logger.exception("Failed to reply LINE message")


def _extract_line_user_id(event: MessageEvent) -> str:
    source = getattr(event, "source", None)
    user_id = getattr(source, "user_id", "")
    return user_id or "unknown-user"


def _iter_text_events(events: Iterable[object]) -> Iterable[MessageEvent]:
    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            yield event


def _iter_image_events(events: Iterable[object]) -> Iterable[MessageEvent]:
    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, ImageMessageContent):
            yield event


def _iter_file_events(events: Iterable[object]) -> Iterable[MessageEvent]:
    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, FileMessageContent):
            yield event


def _is_group_or_room_event(event: MessageEvent) -> bool:
    source = getattr(event, "source", None)
    source_type = getattr(source, "type", "")
    return source_type in {"group", "room"}


def _extract_mentions(event: MessageEvent) -> list[object]:
    mention = getattr(event.message, "mention", None)
    if mention is None:
        return []

    mentionees = getattr(mention, "mentionees", None)
    if mentionees is None and isinstance(mention, dict):
        mentionees = mention.get("mentionees", [])
    return list(mentionees or [])


def _is_self_mention(mentionee: object) -> bool:
    if isinstance(mentionee, dict):
        return bool(mentionee.get("is_self"))
    return bool(getattr(mentionee, "is_self", False))


def _strip_named_call_prefix(text: str) -> str:
    bot_name = (settings.line_bot_name or "").strip()
    if not bot_name:
        return text.strip()

    # Support both half-width @ and full-width ＠ prefixes in group chats.
    pattern = rf"^\s*[@＠]{re.escape(bot_name)}[\s,:，：-]*"
    return re.sub(pattern, "", text, flags=re.IGNORECASE).strip()


def _should_reply(event: MessageEvent) -> bool:
    if not _is_group_or_room_event(event):
        return True

    # 圖片/檔案訊息通常沒有 @mention，群組內預設仍允許回應。
    if isinstance(getattr(event, "message", None), (ImageMessageContent, FileMessageContent)):
        return True

    mentionees = _extract_mentions(event)
    if any(_is_self_mention(mentionee) for mentionee in mentionees):
        return True

    # LINE v3 SDK mention model may only expose type/index/length.
    if len(mentionees) > 0:
        return True

    # Fallback: some clients may send plain text like "@lioil_bot ...".
    text = getattr(event.message, "text", "")
    return _strip_named_call_prefix(text) != text.strip()


def _strip_self_mentions_from_text(event: MessageEvent, text: str) -> str:
    mentionees = _extract_mentions(event)
    ranges: list[tuple[int, int]] = []
    for mentionee in mentionees:
        if isinstance(mentionee, dict):
            index = mentionee.get("index")
            length = mentionee.get("length")
        else:
            index = getattr(mentionee, "index", None)
            length = getattr(mentionee, "length", None)
        if isinstance(index, int) and isinstance(length, int) and index >= 0 and length > 0:
            ranges.append((index, index + length))

    if not ranges:
        return _strip_named_call_prefix(text)

    # Reverse order to avoid index shift while slicing.
    for start, end in sorted(ranges, reverse=True):
        text = text[:start] + text[end:]
    return text.strip()


def handle_webhook(body: str, signature: str, bot_service: BotService) -> None:
    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError as exc:
        raise ValueError("Invalid LINE signature") from exc

    for event in _iter_text_events(events):
        reply_token = getattr(event, "reply_token", "")
        if not reply_token:
            continue

        if not _should_reply(event):
            continue

        incoming_text = getattr(event.message, "text", "")
        incoming_text = _strip_self_mentions_from_text(event, incoming_text)
        if not incoming_text:
            continue
        line_user_id = _extract_line_user_id(event)
        reply = bot_service.handle_user_message(line_user_id=line_user_id, text=incoming_text)
        reply_text(reply_token, reply)

    if getattr(settings, "image_ocr_enabled", True):
        for event in _iter_image_events(events):
            reply_token = getattr(event, "reply_token", "")
            if not reply_token:
                continue

            if not _should_reply(event):
                continue

            line_user_id = _extract_line_user_id(event)
            message_id = str(getattr(event.message, "id", "") or "")
            if not message_id:
                reply_text(reply_token, "目前無法讀取這張圖片，請稍後再試。")
                continue

            image_bytes = _download_line_message_content(message_id)
            if not image_bytes:
                reply_text(reply_token, "目前無法下載這張圖片，請稍後再試。")
                continue

            try:
                ocr_text = image_ocr_service.extract_text(image_bytes)
            except ModuleNotFoundError:
                reply_text(reply_token, "目前尚未安裝圖片 OCR 套件，暫時無法解析圖片文字。")
                continue
            except Exception:
                logger.exception("Failed to OCR image message_id=%s", message_id)
                reply_text(reply_token, "圖片解析失敗，請改傳更清晰圖片或直接貼上文字。")
                continue

            if not ocr_text.strip():
                reply_text(reply_token, "我有收到圖片，但沒有辨識到可用文字，請改傳清晰截圖或文字內容。")
                continue

            prompt = (
                "使用者上傳了一張圖片，以下是我辨識出的文字（可能有 OCR 誤差）：\n"
                f"{ocr_text}\n\n"
                "請先簡短提醒可能有辨識誤差，再根據內容回答。"
            )
            reply = bot_service.handle_user_message(line_user_id=line_user_id, text=prompt)
            reply_text(reply_token, reply)

    if not getattr(settings, "file_parser_enabled", True):
        return

    for event in _iter_file_events(events):
        reply_token = getattr(event, "reply_token", "")
        if not reply_token:
            continue

        if not _should_reply(event):
            continue

        line_user_id = _extract_line_user_id(event)
        message_id = str(getattr(event.message, "id", "") or "")
        file_name = str(
            getattr(event.message, "file_name", "")
            or getattr(event.message, "fileName", "")
            or "未命名檔案"
        )
        if not message_id:
            reply_text(reply_token, "目前無法讀取這份檔案，請稍後再試。")
            continue

        file_bytes = _download_line_message_content(message_id)
        if not file_bytes:
            reply_text(reply_token, "目前無法下載這份檔案，請稍後再試。")
            continue

        extracted_text, parse_error = document_parser_service.extract_text(
            file_name=file_name,
            content=file_bytes,
        )
        if parse_error:
            reply_text(reply_token, parse_error)
            continue

        prompt = (
            f"使用者上傳了一份檔案（{file_name}），以下是抽取到的文字內容：\n"
            f"{extracted_text}\n\n"
            "請先簡短說明你是根據抽取內容回答，再提供重點整理與建議。"
        )
        reply = bot_service.handle_user_message(line_user_id=line_user_id, text=prompt)
        reply_text(reply_token, reply)


def _download_line_message_content(message_id: str) -> bytes | None:
    configuration = Configuration(access_token=settings.line_channel_access_token)
    try:
        with ApiClient(configuration) as api_client:
            blob_api = MessagingApiBlob(api_client)
            response = blob_api.get_message_content(message_id)
        data = getattr(response, "data", None)
        if isinstance(data, bytes):
            return data
        if isinstance(response, (bytes, bytearray)):
            return bytes(response)
    except Exception:
        logger.exception("Failed to download LINE image content message_id=%s", message_id)
    return None
