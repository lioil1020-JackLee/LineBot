from __future__ import annotations

from unittest.mock import MagicMock

from linebot_app.agent_loop import (
    _parse_tool_call,
    _run_market_quote_fallback,
    _run_tool,
    run_agent_loop,
)


def test_parse_tool_call_valid() -> None:
    text = '<tool_call>\n{"tool": "web_search", "args": {"query": "台北天氣"}}\n</tool_call>'
    result = _parse_tool_call(text)
    assert result == ("web_search", {"query": "台北天氣"})


def test_parse_tool_call_fetch_url() -> None:
    text = '<tool_call>{"tool": "fetch_url", "args": {"url": "https://example.com"}}</tool_call>'
    result = _parse_tool_call(text)
    assert result == ("fetch_url", {"url": "https://example.com"})


def test_parse_tool_call_invalid_json() -> None:
    assert _parse_tool_call("<tool_call>{broken json}</tool_call>") is None


def test_run_tool_missing_query() -> None:
    result = _run_tool("web_search", {})
    assert "missing query" in result


def test_run_tool_unknown() -> None:
    result = _run_tool("nonexistent_tool", {})
    assert "unknown tool" in result


def test_run_tool_fetch_url(monkeypatch) -> None:
    import linebot_app.agent_loop as al

    monkeypatch.setattr(al, "_run_fetch_url", lambda _: "fetched content")
    result = al._run_tool("fetch_url", {"url": "https://example.com"})
    assert result == "fetched content"


def _make_llm(replies: list[str]) -> MagicMock:
    from linebot_app.services.llm_service import LLMReply

    llm = MagicMock()
    llm.chat_model = "mock-model"
    llm.generate_reply.side_effect = [
        LLMReply(
            text=text,
            model_name="mock-model",
            latency_ms=1,
            prompt_tokens=5,
            completion_tokens=5,
            total_tokens=10,
        )
        for text in replies
    ]
    return llm


def test_run_agent_loop_no_tool() -> None:
    llm = _make_llm(["final answer"])
    result = run_agent_loop(
        llm_service=llm,
        system_prompt="system",
        conversation=[{"role": "user", "content": "hello"}],
    )
    assert result.final_answer == "final answer"
    assert result.tool_steps == []
    assert result.rounds == 1


def test_run_agent_loop_with_tool_call(monkeypatch) -> None:
    monkeypatch.setattr("linebot_app.agent_loop._run_tool", lambda *_: "search result")
    llm = _make_llm(
        [
            '<tool_call>{"tool": "web_search", "args": {"query": "台北天氣"}}</tool_call>',
            "台北今天 25C",
        ]
    )
    result = run_agent_loop(
        llm_service=llm,
        system_prompt="system",
        conversation=[{"role": "user", "content": "台北天氣"}],
    )
    assert "25C" in result.final_answer
    assert len(result.tool_steps) == 1
    assert result.tool_steps[0].tool == "web_search"


def test_run_agent_loop_uses_raw_user_query_for_web_search(monkeypatch) -> None:
    captured_args: list[dict] = []

    def _fake_run_tool(tool_name: str, args: dict) -> str:
        captured_args.append({"tool": tool_name, "args": dict(args)})
        return "search result"

    monkeypatch.setattr("linebot_app.agent_loop._run_tool", _fake_run_tool)
    llm = _make_llm(
        [
            (
                '<tool_call>{"tool": "web_search", '
                '"args": {"query": "延續上一題股票內容，查你的底層模型"}}</tool_call>'
            ),
            "回答完成",
        ]
    )
    result = run_agent_loop(
        llm_service=llm,
        system_prompt="system",
        conversation=[{"role": "user", "content": "上一題補強文本"}],
        raw_user_query="你的底層模型是什麼",
    )

    assert result.final_answer == "回答完成"
    assert captured_args[0]["tool"] == "web_search"
    assert captured_args[0]["args"]["query"] == "你的底層模型是什麼"
    assert result.tool_steps[0].args["query"] == "你的底層模型是什麼"


def test_run_agent_loop_auto_search_when_uncertain(monkeypatch) -> None:
    monkeypatch.setattr("linebot_app.agent_loop._run_tool", lambda *_: "search result")
    llm = _make_llm(["I am not sure.", "now I can answer"])
    result = run_agent_loop(
        llm_service=llm,
        system_prompt="system",
        conversation=[{"role": "user", "content": "latest weather"}],
        fast_mode=False,
        auto_search_enabled=True,
    )
    assert result.final_answer == "now I can answer"
    assert len(result.tool_steps) == 1
    assert result.tool_steps[0].tool == "web_search"


def test_market_quote_fallback_for_tsmc(monkeypatch) -> None:
    from linebot_app.services.market_service import MarketSnapshot

    class _FakeMarketService:
        def query_taiwan_weighted_index(self):
            return None

        def query_taiwan_stock_by_query(self, query: str):
            if "台積電" not in query:
                return None
            return MarketSnapshot(
                symbol="2330.TW",
                display_name="台積電",
                price=912.0,
                change=8.0,
                change_percent=0.89,
                market_time=None,
                source="TWSE MIS API",
            )

    import linebot_app.agent_loop as al

    monkeypatch.setattr(al, "MarketService", _FakeMarketService)
    text = _run_market_quote_fallback("今天台積電股價是多少")

    assert "台積電" in text
    assert "2330.TW" in text
