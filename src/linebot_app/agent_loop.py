from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .config import get_settings
from .models.search import format_search_results
from .services.market_service import MarketService
from .services.web_search_service import WebSearchService

if TYPE_CHECKING:
    from .services.llm_service import LLMService

logger = logging.getLogger(__name__)

_TOOLS_PROMPT_WITH_WEB_SEARCH = """You may call tools when needed.

Format:
<tool_call>
{"tool": "tool_name", "args": {"arg_name": "value"}}
</tool_call>

Tools:
1. web_search(query): search the web
2. fetch_url(url): fetch page text

Answering rules after receiving <tool_result>:
- Do not only list sources.
- Start with a direct answer in 1-3 sentences.
- Then provide a short summary of key facts.
- If the tool results are partial, answer with what is confirmed and say what is missing.
- Do not claim browsing is unavailable when tool_result is present.
"""

_TOOLS_PROMPT_FETCH_ONLY = """You may call tools when needed.

Format:
<tool_call>
{"tool": "tool_name", "args": {"arg_name": "value"}}
</tool_call>

Tools:
1. fetch_url(url): fetch page text
"""

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL | re.IGNORECASE)
_MAX_TOOL_ROUNDS = 3
_AUTO_SEARCH_TOOL = "web_search"
_UNCERTAIN_HINTS = (
    "not sure",
    "i don't know",
    "cannot confirm",
    "unknown",
    "無法確認",
    "不確定",
    "查不到",
)
_REALTIME_QUERY_HINTS = (
    "最新",
    "今天",
    "即時",
    "新聞",
    "天氣",
    "weather",
    "forecast",
    "market",
    "stock",
    "price",
    "quote",
)
_MARKET_QUERY_HINTS = (
    "股價",
    "股票",
    "台股",
    "大盤",
    "加權指數",
    "twii",
    "twse",
    "market",
    "stock",
    "price",
    "quote",
)
_WEATHER_QUERY_HINTS = ("天氣", "氣溫", "降雨", "weather", "forecast")
_REFUSAL_HINTS = (
    "cannot provide real-time",
    "can't browse",
    "do not have browsing",
    "我目前無法查到",
    "我目前查不到",
)
_SEARCH_FILLER_TOKENS = ("請問", "幫我查", "查一下", "資料", "資訊")
_MARKET_RESULT_PREFIX = "[market_quote]"


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
    match = _TOOL_CALL_RE.search(text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except (json.JSONDecodeError, AttributeError):
        return None

    tool = str(payload.get("tool", "")).strip()
    args = payload.get("args", {})
    if tool and isinstance(args, dict):
        return tool, args
    return None


def _run_web_search(args: dict) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "(web_search missing query)"

    market_fallback = _run_market_quote_fallback(query)
    if market_fallback:
        return market_fallback

    settings = get_settings()
    if not getattr(settings, "web_search_enabled", True):
        return "(web_search unavailable: web search is disabled)"

    service = WebSearchService.from_settings(settings)
    results: list = []
    for candidate in _build_search_candidates(query):
        results = service.search(query=candidate, max_results=5)
        if results:
            break

    if not results:
        return "(web_search returned no results)"
    return format_search_results(results)


def _build_search_candidates(query: str) -> list[str]:
    compact = query.strip().lower()
    candidates = [query.strip()]

    topic = query
    for token in _SEARCH_FILLER_TOKENS:
        topic = topic.replace(token, " ")
    topic = " ".join(topic.split()).strip()
    if topic and topic != query.strip():
        candidates.append(topic)

    if any(hint in compact for hint in _MARKET_QUERY_HINTS):
        candidates.append(f"{topic or query.strip()} TWSE quote")
    elif any(hint in compact for hint in _WEATHER_QUERY_HINTS):
        candidates.append(f"{topic or query.strip()} weather")
    else:
        candidates.append(f"{topic or query.strip()} latest")

    return list(dict.fromkeys(item for item in candidates if item.strip()))


def _run_market_quote_fallback(query: str) -> str:
    compact = query.strip().lower()
    if not any(hint in compact for hint in _MARKET_QUERY_HINTS):
        return ""

    service = MarketService()
    lines: list[str] = []

    if any(token in compact for token in ("台股", "大盤", "加權指數", "twii")):
        twii = service.query_taiwan_weighted_index()
        if twii is not None:
            summary = f"台股加權指數 {twii.price:,.2f}"
            if twii.change is not None and twii.change_percent is not None:
                summary += f"（{twii.change:+,.2f}, {twii.change_percent:+.2f}%）"
            lines.append(summary)

    stock = service.query_taiwan_stock_by_query(query)
    if stock is not None and stock.symbol.lower() != "t00":
        summary = f"{stock.display_name} ({stock.symbol}) {stock.price:,.2f}"
        if stock.change is not None and stock.change_percent is not None:
            summary += f"（{stock.change:+,.2f}, {stock.change_percent:+.2f}%）"
        lines.append(summary)

    if not lines:
        return ""

    rendered = "\n".join(f"- {line}" for line in lines)
    return f"{_MARKET_RESULT_PREFIX}\n資料來源：TWSE MIS API\n{rendered}"


def _run_fetch_url(args: dict) -> str:
    from .tools.fetch_url import fetch_url as _fetch_url

    url = str(args.get("url", "")).strip()
    if not url:
        return "(fetch_url missing url)"
    return _fetch_url(url)


def _run_tool(tool_name: str, args: dict) -> str:
    if tool_name == "web_search":
        return _run_web_search(args)
    if tool_name == "fetch_url":
        return _run_fetch_url(args)
    return f"(unknown tool: {tool_name})"


def run_agent_loop(
    *,
    llm_service: LLMService,
    system_prompt: str,
    conversation: list[dict[str, str]],
    fast_mode: bool = True,
    auto_search_enabled: bool = False,
    max_tool_rounds: int = _MAX_TOOL_ROUNDS,
    raw_user_query: str | None = None,
) -> AgentLoopResult:
    tool_steps: list[ToolCallStep] = []
    current_conversation = list(conversation)
    auto_search_used = False
    effective_raw_user_query = (
        (raw_user_query or "").strip() or _extract_latest_user_query(conversation)
    )

    settings = get_settings()
    web_search_available = bool(getattr(settings, "web_search_enabled", True))
    tools_prompt = (
        _TOOLS_PROMPT_WITH_WEB_SEARCH
        if web_search_available
        else _TOOLS_PROMPT_FETCH_ONLY
    )
    augmented_system_prompt = system_prompt + "\n\n" + tools_prompt
    effective_max_rounds = max(0, max_tool_rounds)

    for round_idx in range(effective_max_rounds + 1):
        reply = llm_service.generate_reply(
            system_prompt=augmented_system_prompt,
            conversation=current_conversation,
        )
        text = reply.text

        if round_idx >= effective_max_rounds:
            if _looks_like_refusal(text) and _has_usable_search_result(tool_steps):
                text = _rewrite_with_tool_results(
                    llm_service=llm_service,
                    system_prompt=augmented_system_prompt,
                    conversation=current_conversation,
                    fallback=text,
                )
            return AgentLoopResult(final_answer=text, tool_steps=tool_steps, rounds=round_idx + 1)

        parsed = _parse_tool_call(text)
        if parsed is None:
            should_auto_search = auto_search_enabled and (not auto_search_used) and (
                _looks_uncertain(text) or _looks_realtime_query(effective_raw_user_query)
            )
            if should_auto_search and effective_raw_user_query:
                tool_args = {"query": effective_raw_user_query}
                tool_result = _run_tool(_AUTO_SEARCH_TOOL, tool_args)
                tool_steps.append(
                    ToolCallStep(tool=_AUTO_SEARCH_TOOL, args=tool_args, result=tool_result)
                )
                auto_search_used = True
                current_conversation.append({"role": "assistant", "content": text})
                current_conversation.append(
                    {"role": "user", "content": f"<tool_result>\n{tool_result}\n</tool_result>"}
                )
                continue

            if fast_mode and _looks_like_refusal(text) and _has_usable_search_result(tool_steps):
                text = _rewrite_with_tool_results(
                    llm_service=llm_service,
                    system_prompt=augmented_system_prompt,
                    conversation=current_conversation,
                    fallback=text,
                )
            return AgentLoopResult(final_answer=text, tool_steps=tool_steps, rounds=round_idx + 1)

        tool_name, tool_args = parsed
        if tool_name == "web_search" and effective_raw_user_query:
            tool_args = {**tool_args, "query": effective_raw_user_query}

        logger.info("agent_loop round=%d tool=%s args=%r", round_idx + 1, tool_name, tool_args)
        tool_result = _run_tool(tool_name, tool_args)
        tool_steps.append(ToolCallStep(tool=tool_name, args=tool_args, result=tool_result))
        current_conversation.append({"role": "assistant", "content": text})
        current_conversation.append(
            {"role": "user", "content": f"<tool_result>\n{tool_result}\n</tool_result>"}
        )

    return AgentLoopResult(
        final_answer="The assistant reached maximum tool rounds.",
        tool_steps=tool_steps,
        rounds=effective_max_rounds,
    )


def _looks_uncertain(text: str) -> bool:
    lower = text.lower()
    return any(hint in lower for hint in _UNCERTAIN_HINTS)


def _extract_latest_user_query(conversation: list[dict[str, str]]) -> str:
    for item in reversed(conversation):
        if item.get("role") != "user":
            continue
        content = (item.get("content") or "").strip()
        if content and "<tool_result>" not in content:
            return content
    return ""


def _looks_realtime_query(query: str) -> bool:
    lowered = query.lower().strip()
    return bool(lowered) and any(hint in lowered for hint in _REALTIME_QUERY_HINTS)


def _looks_like_refusal(text: str) -> bool:
    lower = text.lower()
    return any(hint.lower() in lower for hint in _REFUSAL_HINTS)


def _has_usable_search_result(tool_steps: list[ToolCallStep]) -> bool:
    for step in reversed(tool_steps):
        if step.tool != "web_search":
            continue
        result = (step.result or "").strip().lower()
        if not result or "returned no results" in result or "unavailable" in result:
            continue
        return True
    return False


def _rewrite_with_tool_results(
    *,
    llm_service: LLMService,
    system_prompt: str,
    conversation: list[dict[str, str]],
    fallback: str,
) -> str:
    market_summary = _extract_market_summary(conversation)
    if market_summary:
        return market_summary

    rewrite_instruction = (
        "Use the available tool_result to answer directly. "
        "Start with a concise answer, include the key facts you can confirm, "
        "and mention any missing details instead of refusing."
    )
    try:
        rewritten = llm_service.generate_reply(
            system_prompt=system_prompt,
            conversation=[*conversation, {"role": "user", "content": rewrite_instruction}],
        ).text.strip()
        return rewritten or fallback
    except Exception:
        return fallback


def _extract_market_summary(conversation: list[dict[str, str]]) -> str:
    for item in reversed(conversation):
        if item.get("role") != "user":
            continue
        content = (item.get("content") or "").strip()
        if _MARKET_RESULT_PREFIX not in content:
            continue

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        kept = [
            line
            for line in lines
            if line not in {"<tool_result>", "</tool_result>"}
            and not line.startswith("<tool_result>")
        ]
        if kept:
            return "\n".join(kept)
    return ""
