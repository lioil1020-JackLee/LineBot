from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from linebot_app.agent_loop import (
    AgentLoopResult,
    _parse_tool_call,
    _run_tool,
    run_agent_loop,
)


# ---------------------------------------------------------------------------
# _parse_tool_call
# ---------------------------------------------------------------------------

def test_parse_tool_call_valid():
    text = '<tool_call>\n{"tool": "web_search", "args": {"query": "台灣天氣"}}\n</tool_call>'
    result = _parse_tool_call(text)
    assert result is not None
    tool, args = result
    assert tool == "web_search"
    assert args == {"query": "台灣天氣"}


def test_parse_tool_call_fetch_url():
    text = '<tool_call>{"tool": "fetch_url", "args": {"url": "https://example.com"}}</tool_call>'
    result = _parse_tool_call(text)
    assert result is not None
    assert result[0] == "fetch_url"
    assert result[1]["url"] == "https://example.com"


def test_parse_tool_call_no_match():
    assert _parse_tool_call("這是一般回覆，不含工具呼叫") is None
    assert _parse_tool_call("") is None


def test_parse_tool_call_invalid_json():
    assert _parse_tool_call("<tool_call>{broken json}</tool_call>") is None


# ---------------------------------------------------------------------------
# _run_tool
# ---------------------------------------------------------------------------

def test_run_tool_web_search(monkeypatch):
    from linebot_app.tools.web_search import SearchResult
    import linebot_app.agent_loop as al

    fake_results = [SearchResult(title="台灣新聞", url="https://news.tw", snippet="今天台灣很熱")]
    monkeypatch.setattr(al, "_run_web_search", lambda args: al.format_search_results(fake_results)
                        if hasattr(al, "format_search_results")
                        else "台灣新聞\nhttps://news.tw\n今天台灣很熱")

    from linebot_app.tools.web_search import format_search_results
    monkeypatch.setattr(al, "_run_web_search", lambda args: format_search_results(fake_results))
    result = al._run_tool("web_search", {"query": "台灣天氣"})
    assert "台灣新聞" in result
    assert "今天台灣很熱" in result


def test_run_tool_fetch_url(monkeypatch):
    import linebot_app.agent_loop as al
    monkeypatch.setattr(al, "_run_fetch_url", lambda args: "頁面內容 hello world")
    result = al._run_tool("fetch_url", {"url": "https://example.com"})
    assert "頁面內容" in result


def test_run_tool_missing_query():
    result = _run_tool("web_search", {})
    assert "缺少" in result


def test_run_tool_unknown():
    result = _run_tool("nonexistent_tool", {})
    assert "未知工具" in result


# ---------------------------------------------------------------------------
# run_agent_loop
# ---------------------------------------------------------------------------

def _make_llm(replies: list[str]) -> MagicMock:
    """Helper: 模擬 LLMService，依序回傳指定的文字"""
    from linebot_app.services.llm_service import LLMReply

    llm = MagicMock()
    llm.chat_model = "mock-model"
    llm.generate_reply.side_effect = [
        LLMReply(
            text=t,
            model_name="mock-model",
            latency_ms=1,
            prompt_tokens=5,
            completion_tokens=5,
            total_tokens=10,
        )
        for t in replies
    ]
    return llm


def test_run_agent_loop_no_tool():
    llm = _make_llm(["今天天氣很好。"])
    result = run_agent_loop(
        llm_service=llm,
        system_prompt="你是助理",
        conversation=[{"role": "user", "content": "你好"}],
    )
    assert result.final_answer == "今天天氣很好。"
    assert result.tool_steps == []
    assert result.rounds == 1


def test_run_agent_loop_with_one_tool_call(monkeypatch):
    monkeypatch.setattr(
        "linebot_app.agent_loop._run_tool",
        lambda tool, args: "搜尋結果：台北 25°C",
    )
    llm = _make_llm([
        '<tool_call>{"tool": "web_search", "args": {"query": "台北天氣"}}</tool_call>',
        "根據搜尋結果，台北今天 25°C，晴天。",
    ])
    result = run_agent_loop(
        llm_service=llm,
        system_prompt="你是助理",
        conversation=[{"role": "user", "content": "台北天氣如何？"}],
    )
    assert "25°C" in result.final_answer
    assert len(result.tool_steps) == 1
    assert result.tool_steps[0].tool == "web_search"


def test_run_agent_loop_max_rounds(monkeypatch):
    """連續要求工具超過上限時，強制回傳最後一次的文字"""
    monkeypatch.setattr(
        "linebot_app.agent_loop._run_tool",
        lambda tool, args: "搜尋結果",
    )
    # 4 輪全部都要求工具，第 4 輪應強制回傳
    tool_call = '<tool_call>{"tool": "web_search", "args": {"query": "test"}}</tool_call>'
    final = "這是最終答案"
    llm = _make_llm([tool_call, tool_call, tool_call, final])

    from linebot_app import agent_loop
    original_max = agent_loop._MAX_TOOL_ROUNDS
    agent_loop._MAX_TOOL_ROUNDS = 3

    try:
        result = run_agent_loop(
            llm_service=llm,
            system_prompt="你是助理",
            conversation=[{"role": "user", "content": "問題"}],
        )
    finally:
        agent_loop._MAX_TOOL_ROUNDS = original_max

    assert result.rounds == 4
    assert len(result.tool_steps) == 3  # 第 4 輪不執行工具
