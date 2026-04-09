from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable

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
from linebot.v3.webhooks import (
    FileMessageContent,
    ImageMessageContent,
    MessageEvent,
    TextMessageContent,
)

from .config import get_settings
from .services.bot_service import BotService
from .services.document_parser_service import DocumentParserService
from .services.image_ocr_service import ImageOCRService

settings = get_settings()
parser = WebhookParser(settings.line_channel_secret or "development-secret")
logger = logging.getLogger(__name__)
image_ocr_service = ImageOCRService()
document_parser_service = DocumentParserService()

_IMAGE_ID_MISSING_MESSAGE = "無法取得圖片訊息 ID，請再試一次。"
_IMAGE_DOWNLOAD_FAILED_MESSAGE = "無法下載圖片內容，請稍後再試。"
_IMAGE_OCR_DEPENDENCY_MESSAGE = "目前未安裝圖片 OCR 所需套件，暫時無法辨識圖片文字。"
_IMAGE_OCR_FAILED_MESSAGE = "圖片辨識失敗，請稍後再試，或改傳更清晰的圖片。"
_IMAGE_EMPTY_TEXT_MESSAGE = "這張圖片沒有辨識到可用文字，可以改傳更清晰的圖片或直接描述想問的內容。"

_FILE_ID_MISSING_MESSAGE = "無法取得檔案訊息 ID，請再試一次。"
_FILE_DOWNLOAD_FAILED_MESSAGE = "無法下載檔案內容，請稍後再試。"


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
    return getattr(source, "type", "") in {"group", "room"}


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


def _get_bot_name_aliases() -> tuple[str, ...]:
    configured_name = (settings.line_bot_name or "").strip()
    if configured_name:
        return (configured_name,)
    return ()


def _strip_named_call_prefix(text: str) -> str:
    aliases = _get_bot_name_aliases()
    if not aliases:
        return text.strip()

    joined = "|".join(re.escape(name) for name in aliases)
    pattern = rf"^\s*@?(?:{joined})(?:[\s,，:：-]+)?"
    return re.sub(pattern, "", text, flags=re.IGNORECASE).strip()


def _should_reply(event: MessageEvent) -> bool:
    if not _is_group_or_room_event(event):
        return True

    if not getattr(settings, "line_group_require_mention", True):
        return True

    if isinstance(getattr(event, "message", None), (ImageMessageContent, FileMessageContent)):
        return True

    mentionees = _extract_mentions(event)
    if any(_is_self_mention(item) for item in mentionees):
        return True
    if mentionees:
        return True

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

    for start, end in sorted(ranges, reverse=True):
        text = text[:start] + text[end:]
    return text.strip()


def _call_bot_service(
    *,
    bot_service: BotService,
    line_user_id: str,
    text: str,
    schedule_background_task: Callable[..., object] | None,
) -> str:
    try:
        return bot_service.handle_user_message(
            line_user_id=line_user_id,
            text=text,
            schedule_background_task=schedule_background_task,
        )
    except TypeError:
        return bot_service.handle_user_message(
            line_user_id=line_user_id,
            text=text,
        )


def _build_image_prompt(ocr_text: str) -> str:
    return (
        "請協助閱讀以下從圖片辨識出的文字，整理重點並直接回答使用者可能想知道的內容。\n\n"
        f"{ocr_text}\n\n"
        "若文字有明顯辨識錯誤，可以依上下文合理修正，但不要捏造原文沒有的資訊。"
    )


def _build_file_prompt(*, file_name: str, extracted_text: str) -> str:
    return (
        f"請協助閱讀以下檔案內容，檔名為 {file_name}。\n\n"
        f"{extracted_text}\n\n"
        "請先整理重點，再直接回答使用者可能最在意的內容；如果資訊不足，請明確說明。"
    )


def handle_webhook(
    body: str,
    signature: str,
    bot_service: BotService,
    schedule_background_task: Callable[..., object] | None = None,
) -> None:
    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError as exc:
        raise ValueError("Invalid LINE signature") from exc

    for event in _iter_text_events(events):
        reply_token = getattr(event, "reply_token", "")
        if not reply_token or not _should_reply(event):
            continue

        incoming_text = _strip_self_mentions_from_text(
            event,
            getattr(event.message, "text", ""),
        )
        if not incoming_text:
            continue

        line_user_id = _extract_line_user_id(event)
        reply = _call_bot_service(
            bot_service=bot_service,
            line_user_id=line_user_id,
            text=incoming_text,
            schedule_background_task=schedule_background_task,
        )
        reply_text(reply_token, reply)

    if getattr(settings, "image_ocr_enabled", True):
        for event in _iter_image_events(events):
            reply_token = getattr(event, "reply_token", "")
            if not reply_token or not _should_reply(event):
                continue

            line_user_id = _extract_line_user_id(event)
            message_id = str(getattr(event.message, "id", "") or "")
            if not message_id:
                reply_text(reply_token, _IMAGE_ID_MISSING_MESSAGE)
                continue

            image_bytes = _download_line_message_content(message_id)
            if not image_bytes:
                reply_text(reply_token, _IMAGE_DOWNLOAD_FAILED_MESSAGE)
                continue

            try:
                ocr_text = image_ocr_service.extract_text(image_bytes)
            except ModuleNotFoundError:
                reply_text(reply_token, _IMAGE_OCR_DEPENDENCY_MESSAGE)
                continue
            except Exception:
                logger.exception("Failed to OCR image message_id=%s", message_id)
                reply_text(reply_token, _IMAGE_OCR_FAILED_MESSAGE)
                continue

            if not ocr_text.strip():
                reply_text(reply_token, _IMAGE_EMPTY_TEXT_MESSAGE)
                continue

            prompt = _build_image_prompt(ocr_text)
            reply = _call_bot_service(
                bot_service=bot_service,
                line_user_id=line_user_id,
                text=prompt,
                schedule_background_task=schedule_background_task,
            )
            reply_text(reply_token, reply)

    if not getattr(settings, "file_parser_enabled", True):
        return

    for event in _iter_file_events(events):
        reply_token = getattr(event, "reply_token", "")
        if not reply_token or not _should_reply(event):
            continue

        line_user_id = _extract_line_user_id(event)
        message_id = str(getattr(event.message, "id", "") or "")
        file_name = str(
            getattr(event.message, "file_name", "")
            or getattr(event.message, "fileName", "")
            or "unnamed-file"
        )
        if not message_id:
            reply_text(reply_token, _FILE_ID_MISSING_MESSAGE)
            continue

        file_bytes = _download_line_message_content(message_id)
        if not file_bytes:
            reply_text(reply_token, _FILE_DOWNLOAD_FAILED_MESSAGE)
            continue

        extracted_text, parse_error = document_parser_service.extract_text(
            file_name=file_name,
            content=file_bytes,
        )
        if parse_error:
            reply_text(reply_token, parse_error)
            continue

        prompt = _build_file_prompt(file_name=file_name, extracted_text=extracted_text)
        reply = _call_bot_service(
            bot_service=bot_service,
            line_user_id=line_user_id,
            text=prompt,
            schedule_background_task=schedule_background_task,
        )
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
        logger.exception("Failed to download LINE message content message_id=%s", message_id)
    return None
