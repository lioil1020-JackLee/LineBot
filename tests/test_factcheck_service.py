"""
FactCheckService 最小測試集。

設計原則：
- 使用 stub LLM，不呼叫真實 LM Studio。
- 使用 stub search_fn，不呼叫真實 DuckDuckGo。
- 驗證三個核心行為：
    1. 一般聊天 → try_factcheck 回傳 None
    2. 可查證主張 → 回傳包含「假訊息查證」的字串
    3. 訊息太模糊 → 回傳詢問更多上下文
"""
from __future__ import annotations

import json

import pytest

from linebot_app.services.factcheck_service import FactCheckConfig, FactCheckService
from linebot_app.services.llm_service import LLMReply
from linebot_app.tools.web_search import SearchResult


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _StubLLM:
    """可依序回傳預設回覆的 stub LLM service。"""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self._idx = 0
        self.calls = 0
        self.chat_model = "stub-model"

    def generate_reply(
        self, *, system_prompt: str, conversation: list[dict[str, str]]
    ) -> LLMReply:
        self.calls += 1
        text = self._replies[self._idx % len(self._replies)]
        self._idx += 1
        return LLMReply(
            text=text,
            model_name="stub-model",
            latency_ms=1,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
        )


def _fake_search(query: str) -> list[SearchResult]:
    return [
        SearchResult(
            title="測試新聞標題",
            url="https://example.com/news",
            snippet="這是測試摘要。",
        )
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _build_service(llm: _StubLLM, search_fn=_fake_search) -> FactCheckService:
    return FactCheckService(
        llm_service=llm,
        search_fn=search_fn,
        config=FactCheckConfig(max_search_queries=1, max_results_per_query=2),
    )


def test_general_chat_returns_none() -> None:
    """一般聊天訊息 → try_factcheck 回傳 None，不進查證流程。"""
    llm = _StubLLM([
        json.dumps({"category": "general_chat", "reason": "純問候"}),
    ])
    service = _build_service(llm)
    result = service.try_factcheck("你好，今天天氣怎麼樣？我們等等吃什麼？")
    assert result is None
    assert llm.calls == 0


def test_short_text_returns_none() -> None:
    """太短的訊息（< 15字）直接回傳 None，不呼叫 LLM。"""
    llm = _StubLLM([])  # 不應被呼叫
    service = _build_service(llm)
    result = service.try_factcheck("你好")
    assert result is None


def test_checkable_claim_triggers_factcheck() -> None:
    """可查證主張 → 回傳查證結果，且包含標頭。"""
    llm = _StubLLM([
        # Step 1: classify
        json.dumps({"category": "checkable", "reason": "聲稱某事件"}),
        # Step 2: extract claims
        json.dumps({"claims": ["台灣 2024 年 GDP 成長率超過 5%"], "needs_more_context": False}),
        # Step 3: synthesize
        "查證結論：無足夠證據\n\n核心主張：台灣 2024 GDP 成長率超過 5%\n\n...",
    ])
    service = _build_service(llm)
    result = service.try_factcheck("有人說台灣 2024 年 GDP 成長率超過 5%，是真的嗎？")
    assert result is not None
    assert "假訊息查證" in result
    assert "[引用來源]" in result
    assert "https://example.com/news" in result


def test_vague_claim_asks_for_context() -> None:
    """訊息太模糊 → 回傳請求更多上下文的提示。"""
    llm = _StubLLM([
        # Step 1: classify
        json.dumps({"category": "checkable", "reason": "聲稱某人說話"}),
        # Step 2: extract claims — needs_more_context = True
        json.dumps({
            "claims": [],
            "needs_more_context": True,
            "context_hint": "請提供說話者姓名和具體日期。",
        }),
    ])
    service = _build_service(llm)
    result = service.try_factcheck("有人說某個政治人物說了某些話，你知道嗎？我想查證。")
    assert result is not None
    assert "請提供說話者" in result


def test_no_search_fn_still_returns_report() -> None:
    """即使 search_fn=None，查證仍完成並說明缺少即時查證來源。"""
    llm = _StubLLM([
        json.dumps({"category": "high_risk", "reason": "醫療謠言"}),
        json.dumps({"claims": ["喝鹽水可以殺死新冠病毒"], "needs_more_context": False}),
        "查證結論：假\n\n缺少即時查證來源，以下為模型初步判讀。",
    ])
    service = FactCheckService(llm_service=llm, search_fn=None)
    result = service.try_factcheck("最近流傳喝高濃度鹽水可以殺死新冠病毒，是真的嗎？")
    assert result is not None
    assert "假訊息查證" in result
    assert "缺少即時查證來源" in result


def test_llm_classify_error_returns_none() -> None:
    """分類時 LLM 拋例外 → 安全 fallback，回傳 None 不中斷服務。"""

    class _ErrorLLM:
        chat_model = "error-model"

        def generate_reply(self, **_):
            raise RuntimeError("LLM unavailable")

    service = FactCheckService(llm_service=_ErrorLLM(), search_fn=None)
    result = service.try_factcheck("有人說台灣即將發生大地震，請問是真的嗎？")
    assert result is None
