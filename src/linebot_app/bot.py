from __future__ import annotations

from collections.abc import Iterable
import logging
import re

from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from .config import get_settings
from .services.bot_service import BotService

settings = get_settings()
parser = WebhookParser(settings.line_channel_secret or "development-secret")
logger = logging.getLogger(__name__)


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
