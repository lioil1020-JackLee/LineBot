from __future__ import annotations

import json

from linebot_app.models.search import SearchResult
from linebot_app.services.factcheck_service import FactCheckConfig, FactCheckService
from linebot_app.services.llm_service import LLMReply


class _StubLLM:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self._idx = 0
        self.calls = 0
        self.chat_model = "stub-model"

    def generate_reply(self, *, system_prompt: str, conversation: list[dict[str, str]]) -> LLMReply:
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


def _fake_search(_: str) -> list[SearchResult]:
    return [
        SearchResult(
            title="Test Source",
            url="https://example.com/news",
            snippet="A short snippet",
        )
    ]


def _build_service(llm: _StubLLM, search_fn=_fake_search) -> FactCheckService:
    return FactCheckService(
        llm_service=llm,
        search_fn=search_fn,
        config=FactCheckConfig(max_search_queries=1, max_results_per_query=2),
    )


def test_short_text_returns_none() -> None:
    service = _build_service(_StubLLM([]))
    assert service.try_factcheck("hello") is None


def test_general_chat_returns_none() -> None:
    llm = _StubLLM([json.dumps({"category": "general_chat", "reason": "casual"})])
    service = _build_service(llm)
    result = service.try_factcheck("今天天氣不錯，我們等一下去散步吧。")
    assert result is None


def test_checkable_claim_returns_report_with_sources() -> None:
    llm = _StubLLM(
        [
            json.dumps({"category": "checkable", "reason": "factual claim"}),
            json.dumps({"claims": ["台北 101 是台灣最高的大樓"], "needs_more_context": False}),
            "根據提供的搜尋結果，這個說法目前看起來成立。",
        ]
    )
    service = _build_service(llm)
    result = service.try_factcheck("請幫我查證台北 101 是不是台灣最高的大樓。")
    assert result is not None
    assert "查證結果" in result
    assert "https://example.com/news" in result


def test_needs_context_returns_hint() -> None:
    llm = _StubLLM(
        [
            json.dumps({"category": "checkable", "reason": "need context"}),
            json.dumps(
                {
                    "claims": [],
                    "needs_more_context": True,
                    "context_hint": "請補充是哪一則新聞、哪個人物，或是哪一天的說法。",
                }
            ),
        ]
    )
    service = _build_service(llm)
    result = service.try_factcheck("幫我查證一下那則新聞是真的假的。")
    assert result is not None
    assert "請補充是哪一則新聞" in result


def test_no_search_fn_still_returns_report() -> None:
    llm = _StubLLM(
        [
            json.dumps({"category": "high_risk", "reason": "medical"}),
            json.dumps({"claims": ["感冒一定要吃抗生素"], "needs_more_context": False}),
            "這個說法需要更謹慎，因為並非所有感冒都需要抗生素。",
        ]
    )
    service = FactCheckService(llm_service=llm, search_fn=None)
    result = service.try_factcheck("這是醫療問題，請幫我查證感冒是不是一定要吃抗生素。")
    assert result is not None
    assert "查證結果" in result
