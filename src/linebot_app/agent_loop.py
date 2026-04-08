from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .services.llm_service import LLMService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt 工具說明（注入到每次 LLM 呼叫）
# ---------------------------------------------------------------------------
TOOLS_SYSTEM_PROMPT = """你可以使用以下工具來獲取你不知道的即時資訊：

**可用工具：**

1. `web_search(query)` — 用 DuckDuckGo 搜尋網路
   適用：查詢即時新聞、近期事件、不確定答案的問題
   
2. `fetch_url(url)` — 抓取指定網頁的文字內容
   適用：需要看某網頁詳細內容時

**使用方式：**
當你需要使用工具時，請輸出以下格式（且該回應只包含這個，不要額外文字）：

<tool_call>
{"tool": "工具名稱", "args": {"參數名": "參數值"}}
</tool_call>

取得工具結果後，你將收到：
<tool_result>
...工具輸出...
</tool_result>

然後根據結果繼續回答使用者。
若問題已有足夠資訊，直接回答不需使用工具。

**重要：** 最多使用 3 次工具後請給出最終答案。」
"""

_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL | re.IGNORECASE,
)

_MAX_TOOL_ROUNDS = 3
_AUTO_SEARCH_TOOL = "web_search"
_UNCERTAIN_HINTS = (
    "不知道",
    "不清楚",
    "無法確認",
    "無法判斷",
    "不確定",
    "資訊不足",
    "我無法",
    "我不確定",
    "can't",
    "cannot",
    "not sure",
    "i don't know",
)


@dataclass
class ToolCallStep:
    tool: str
    args: dict
    result: str


@dataclass
class AgentLoopResult:
    final_answer: str
    tool_steps: list[ToolCallStep] = field(default_factory=list)
    rounds: int = 0


def _parse_tool_call(text: str) -> tuple[str, dict] | None:
    """從模型輸出中解析 <tool_call> 區塊，回傳 (tool_name, args) 或 None"""
    match = _TOOL_CALL_RE.search(text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
        tool = payload.get("tool", "").strip()
        args = payload.get("args", {})
        if tool and isinstance(args, dict):
            return tool, args
    except (json.JSONDecodeError, AttributeError):
        pass
    return None


def _run_web_search(args: dict) -> str:
    from .tools.web_search import format_search_results
    from .tools.web_search import web_search as _ws

    query = str(args.get("query", ""))
    if not query:
        return "（web_search 缺少 query 參數）"
    results = _ws(query, max_results=5)
    return format_search_results(results)


def _run_fetch_url(args: dict) -> str:
    from .tools.fetch_url import fetch_url as _fu

    url = str(args.get("url", ""))
    if not url:
        return "（fetch_url 缺少 url 參數）"
    return _fu(url)


def _run_tool(tool_name: str, args: dict) -> str:
    """執行工具並回傳結果字串"""
    if tool_name == "web_search":
        return _run_web_search(args)
    if tool_name == "fetch_url":
        return _run_fetch_url(args)
    return f"（未知工具：{tool_name}）"


def run_agent_loop(
    *,
    llm_service: LLMService,
    system_prompt: str,
    conversation: list[dict[str, str]],
    fast_mode: bool = True,
    auto_search_enabled: bool = False,
    max_tool_rounds: int = _MAX_TOOL_ROUNDS,
) -> AgentLoopResult:
    """
    ReAct-style agent loop：
    1. 呼叫 LLM
    2. 若模型要求工具呼叫，執行工具、把結果加入對話
    3. 最多 _MAX_TOOL_ROUNDS 輪後強制回傳最終答案
    """
    tool_steps: list[ToolCallStep] = []
    current_conversation = list(conversation)
    auto_search_used = False
    # 工具說明附加在 system prompt 後
    augmented_system = system_prompt + "\n\n" + TOOLS_SYSTEM_PROMPT

    effective_max_rounds = max(0, max_tool_rounds)
    for round_idx in range(effective_max_rounds + 1):
        reply = llm_service.generate_reply(
            system_prompt=augmented_system,
            conversation=current_conversation,
        )
        text = reply.text

        # 最後一輪強制回傳（不執行工具）
        if round_idx >= effective_max_rounds:
            logger.debug("agent_loop: hit max rounds, returning final answer")
            return AgentLoopResult(
                final_answer=text,
                tool_steps=tool_steps,
                rounds=round_idx + 1,
            )

        parsed = _parse_tool_call(text)
        if parsed is None:
            if fast_mode:
                return AgentLoopResult(
                    final_answer=text,
                    tool_steps=tool_steps,
                    rounds=round_idx + 1,
                )
            # 如果模型直接說不確定，且尚未用過工具，強制補一次 web_search 再回答。
            if auto_search_enabled and (not auto_search_used) and _looks_uncertain(text):
                query = _extract_latest_user_query(current_conversation)
                if query:
                    tool_result = _run_tool(_AUTO_SEARCH_TOOL, {"query": query})
                    tool_steps.append(
                        ToolCallStep(
                            tool=_AUTO_SEARCH_TOOL,
                            args={"query": query},
                            result=tool_result,
                        )
                    )
                    auto_search_used = True
                    current_conversation.append({"role": "assistant", "content": text})
                    current_conversation.append({
                        "role": "user",
                        "content": f"<tool_result>\n{tool_result}\n</tool_result>",
                    })
                    continue
            # 模型沒有要求工具 — 這就是最終答案
            return AgentLoopResult(
                final_answer=text,
                tool_steps=tool_steps,
                rounds=round_idx + 1,
            )

        tool_name, args = parsed
        logger.info("agent_loop round=%d tool=%s args=%r", round_idx + 1, tool_name, args)

        tool_result = _run_tool(tool_name, args)
        tool_steps.append(ToolCallStep(tool=tool_name, args=args, result=tool_result))

        # 把工具呼叫與結果加回對話
        current_conversation.append({"role": "assistant", "content": text})
        current_conversation.append({
            "role": "user",
            "content": f"<tool_result>\n{tool_result}\n</tool_result>",
        })

    # 不應到達這裡，保險回傳
    return AgentLoopResult(
        final_answer="（嘗試取得資訊但未能完成）",
        tool_steps=tool_steps,
        rounds=effective_max_rounds,
    )


def _looks_uncertain(text: str) -> bool:
    lower = text.lower()
    return any(hint in lower for hint in _UNCERTAIN_HINTS)


def _extract_latest_user_query(conversation: list[dict[str, str]]) -> str:
    for item in reversed(conversation):
        if item.get("role") == "user":
            content = (item.get("content") or "").strip()
            if content and "<tool_result>" not in content:
                return content
    return ""
