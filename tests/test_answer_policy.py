from linebot_app.services.answer_policy import (
    AnswerMode,
    IntentKind,
    decide_answer_mode,
    decide_intent,
)


def test_decide_intent_blocks_coding_request() -> None:
    decision = decide_intent("可以幫我寫 Python code 嗎")

    assert decision.kind == IntentKind.CODING_BLOCKED
    assert decision.block_coding is True
    assert decision.mode == AnswerMode.GENERAL


def test_decide_intent_detects_self_intro() -> None:
    decision = decide_intent("你的底層模型是什麼")

    assert decision.kind == IntentKind.SELF_INTRO
    assert decision.is_self_intro is True
    assert decision.mode == AnswerMode.GENERAL


def test_decide_intent_routes_weather() -> None:
    decision = decide_intent("台北今天天氣如何")

    assert decision.kind == IntentKind.WEATHER
    assert decision.mode == AnswerMode.WEATHER


def test_decide_answer_mode_keeps_backward_compatibility() -> None:
    assert decide_answer_mode("今天台積電股價是多少") == AnswerMode.MARKET


def test_decide_intent_detects_capability_inquiry() -> None:
    decision = decide_intent("你能上網查資料嗎")

    assert decision.kind == IntentKind.CAPABILITY_INQUIRY
    assert decision.is_capability_inquiry is True
    assert decision.mode == AnswerMode.GENERAL
