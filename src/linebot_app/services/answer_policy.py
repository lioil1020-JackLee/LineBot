from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AnswerMode(StrEnum):
    GENERAL = "general"
    WEATHER = "weather"
    MARKET = "market"
    REALTIME_SENSITIVE = "realtime_sensitive"


class IntentKind(StrEnum):
    GENERAL = "general"
    WEATHER = "weather"
    MARKET = "market"
    REALTIME_SENSITIVE = "realtime_sensitive"
    SELF_INTRO = "self_intro"
    CODING_BLOCKED = "coding_blocked"
    CAPABILITY_INQUIRY = "capability_inquiry"


@dataclass(frozen=True)
class IntentDecision:
    kind: IntentKind
    mode: AnswerMode
    reason: str

    @property
    def block_coding(self) -> bool:
        return self.kind == IntentKind.CODING_BLOCKED

    @property
    def is_self_intro(self) -> bool:
        return self.kind == IntentKind.SELF_INTRO

    @property
    def is_capability_inquiry(self) -> bool:
        return self.kind == IntentKind.CAPABILITY_INQUIRY


_WEATHER_HINTS = (
    "天氣",
    "氣溫",
    "降雨",
    "下雨",
    "weather",
    "forecast",
)

_MARKET_HINTS = (
    "股價",
    "股市",
    "股票",
    "台股",
    "大盤",
    "盤勢",
    "類股",
    "加權指數",
    "twii",
    "twse",
    "etf",
    "stock",
    "market",
    "price",
    "quote",
)

_REALTIME_HINTS = (
    "最新",
    "即時",
    "今天",
    "剛剛",
    "新聞",
    "地震",
    "颱風",
    "疫情",
    "匯率",
    "油價",
    "latest",
    "breaking",
    "real-time",
)

_SELF_INTRO_HINTS = (
    "你是誰",
    "你的底層模型",
    "你的模型",
    "what model are you",
    "who are you",
)

_CODING_HINTS = (
    "寫 code",
    "寫程式",
    "debug",
    "python",
    "javascript",
    "typescript",
    "java",
    "c++",
    "c#",
    "sql",
    "regex",
    "api",
    "github",
    "git",
)

_CAPABILITY_INQUIRY_HINTS = (
    "你能上網嗎",
    "上網查資料",
    "查資料",
    "查詢資料",
    "你會什麼",
    "你能做什麼",
    "can you browse",
    "can you search",
    "what can you do",
)


def _normalize(text: str) -> str:
    return "".join(text.lower().split())


def decide_intent(text: str) -> IntentDecision:
    normalized = _normalize(text)

    if any(hint.replace(" ", "") in normalized for hint in _CAPABILITY_INQUIRY_HINTS):
        return IntentDecision(
            kind=IntentKind.CAPABILITY_INQUIRY,
            mode=AnswerMode.GENERAL,
            reason="matched_capability_inquiry_hint",
        )

    if any(hint.replace(" ", "") in normalized for hint in _CODING_HINTS):
        return IntentDecision(
            kind=IntentKind.CODING_BLOCKED,
            mode=AnswerMode.GENERAL,
            reason="matched_coding_hint",
        )

    if any(hint.replace(" ", "") in normalized for hint in _SELF_INTRO_HINTS):
        return IntentDecision(
            kind=IntentKind.SELF_INTRO,
            mode=AnswerMode.GENERAL,
            reason="matched_self_intro_hint",
        )

    if any(hint.replace(" ", "") in normalized for hint in _MARKET_HINTS):
        return IntentDecision(
            kind=IntentKind.MARKET,
            mode=AnswerMode.MARKET,
            reason="matched_market_hint",
        )

    if any(hint.replace(" ", "") in normalized for hint in _WEATHER_HINTS):
        return IntentDecision(
            kind=IntentKind.WEATHER,
            mode=AnswerMode.WEATHER,
            reason="matched_weather_hint",
        )

    if any(hint.replace(" ", "") in normalized for hint in _REALTIME_HINTS):
        return IntentDecision(
            kind=IntentKind.REALTIME_SENSITIVE,
            mode=AnswerMode.REALTIME_SENSITIVE,
            reason="matched_realtime_hint",
        )

    return IntentDecision(
        kind=IntentKind.GENERAL,
        mode=AnswerMode.GENERAL,
        reason="default_general",
    )


def decide_answer_mode(text: str) -> AnswerMode:
    return decide_intent(text).mode
