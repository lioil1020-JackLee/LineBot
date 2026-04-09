from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models.search import SearchResult, format_search_results

if TYPE_CHECKING:
    from .llm_service import LLMService

logger = logging.getLogger(__name__)

_GENERAL_CHAT = "general_chat"
_CHECKABLE = "checkable"
_HIGH_RISK = "high_risk"
_CHECKABLE_CATEGORIES = {_CHECKABLE, _HIGH_RISK}

_MIN_TEXT_LENGTH = 15
_MAX_SOURCE_ITEMS = 5

_FACTCHECK_TRIGGER_KEYWORDS = (
    "查證",
    "查证",
    "查核",
    "核實",
    "核实",
    "事實查核",
    "事實查證",
    "真的假的",
    "是否屬實",
    "正確嗎",
    "來源",
    "證據",
    "证据",
    "fact check",
    "verify",
)

_HIGH_RISK_KEYWORDS = ("醫療", "药", "藥", "法律", "投資", "財務", "診斷", "處方", "保險")

_NEEDS_CONTEXT_FALLBACK = "請補充你想查證的具體說法、人物、時間或事件。"
_FACTCHECK_FAILURE_MESSAGE = "查證流程暫時失敗，請稍後再試。"
_SOURCE_HEADER = "[參考來源]"
_NO_SEARCH_MESSAGE = "目前無法執行網路搜尋，以下判斷僅根據你提供的內容整理。"
_NO_RESULTS_MESSAGE = "這次沒有找到可直接引用的外部來源。"
_NO_VALID_RESULTS_MESSAGE = "有搜尋結果，但沒有可列出的有效來源連結。"

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_FIRST_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class FactCheckConfig:
    max_search_queries: int = 2
    max_results_per_query: int = 4


class FactCheckService:
    def __init__(
        self,
        *,
        llm_service: LLMService,
        search_fn: Callable[[str], list[SearchResult]] | None = None,
        config: FactCheckConfig | None = None,
    ) -> None:
        self._llm = llm_service
        self._search_fn = search_fn
        self._cfg = config or FactCheckConfig()

    def try_factcheck(self, text: str) -> str | None:
        text = text.strip()
        if len(text) < _MIN_TEXT_LENGTH:
            return None
        if not _should_consider_factcheck(text):
            return None

        category = self._classify(text)
        if category not in _CHECKABLE_CATEGORIES:
            return None

        claims, needs_context, context_hint = self._extract_claims(text)
        if needs_context:
            hint = context_hint or _NEEDS_CONTEXT_FALLBACK
            return f"我可以幫你查證，但目前還缺少關鍵上下文。\n\n{hint}"
        if not claims:
            return None

        search_results, search_ok = self._search_claims(claims)
        return self._synthesize(claims, search_results, search_ok)

    def _classify(self, text: str) -> str:
        prompt = (
            'Return JSON only: {"category": "general_chat|checkable|high_risk"}.\n'
            "Classify whether the user is asking to verify factual claims.\n"
            f"Text:\n{text}"
        )
        try:
            reply = self._llm.generate_reply(
                system_prompt=(
                    "You are a strict JSON classifier. "
                    "Use high_risk for medical, legal, or financial verification requests."
                ),
                conversation=[{"role": "user", "content": prompt}],
            )
            return _parse_category(reply.text)
        except Exception:
            logger.exception("factcheck classify failed")
            return _GENERAL_CHAT

    def _extract_claims(self, text: str) -> tuple[list[str], bool, str]:
        prompt = (
            "Extract 1-3 checkable factual claims from the text.\n"
            'Return JSON only: {"claims": string[], "needs_more_context": bool, '
            '"context_hint": string}.\n'
            "If the text is too vague to verify, set needs_more_context to true "
            "and explain what is missing.\n"
            f"Text:\n{text}"
        )
        try:
            reply = self._llm.generate_reply(
                system_prompt="You are a strict JSON extractor.",
                conversation=[{"role": "user", "content": prompt}],
            )
            data = _parse_json(reply.text)
            claims = [str(item).strip() for item in (data.get("claims") or []) if str(item).strip()]
            needs_more_context = bool(data.get("needs_more_context", False))
            context_hint = str(data.get("context_hint") or "").strip()
            return claims, needs_more_context, context_hint
        except Exception:
            logger.exception("factcheck extract claims failed")
            return [], False, ""

    def _search_claims(self, claims: list[str]) -> tuple[list[SearchResult], bool]:
        if self._search_fn is None:
            return [], False

        all_results: list[SearchResult] = []
        try:
            for claim in claims[: self._cfg.max_search_queries]:
                items = self._search_fn(claim)
                all_results.extend(items[: self._cfg.max_results_per_query])
        except Exception:
            logger.exception("factcheck search failed")
            return [], False
        return all_results, True

    def _synthesize(
        self,
        claims: list[str],
        search_results: list[SearchResult],
        search_ok: bool,
    ) -> str:
        search_text = (
            format_search_results(search_results)
            if search_results
            else "No search results."
        )
        prompt = (
            "Please write the fact-check result in Traditional Chinese.\n"
            "Separate supported, unsupported, and uncertain points.\n"
            "Only rely on the provided search results; if evidence is weak, say so clearly.\n\n"
            f"Claims:\n{json.dumps(claims, ensure_ascii=False)}\n\n"
            f"SearchAvailable: {str(search_ok).lower()}\n"
            f"SearchResults:\n{search_text}"
        )
        try:
            reply = self._llm.generate_reply(
                system_prompt=(
                    "You are a careful fact-checking assistant. "
                    "Write in Traditional Chinese and do not invent sources."
                ),
                conversation=[{"role": "user", "content": prompt}],
            )
            content = f"查證結果\n\n{reply.text.strip()}"
            return _append_source_block(content, search_results, search_ok)
        except Exception:
            logger.exception("factcheck synthesize failed")
            return _FACTCHECK_FAILURE_MESSAGE


def _parse_json(text: str) -> dict:
    cleaned = text.strip()
    match = _JSON_FENCE_RE.search(cleaned)
    if match:
        cleaned = match.group(1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    brace = _FIRST_BRACE_RE.search(cleaned)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _parse_category(text: str) -> str:
    data = _parse_json(text)
    category = str(data.get("category") or "").strip().lower()
    if category in {_GENERAL_CHAT, _CHECKABLE, _HIGH_RISK}:
        return category

    lower = text.lower()
    if "high_risk" in lower:
        return _HIGH_RISK
    if "checkable" in lower:
        return _CHECKABLE
    return _GENERAL_CHAT


def _should_consider_factcheck(text: str) -> bool:
    lower = text.lower()
    if "http://" in lower or "https://" in lower:
        return True
    if any(keyword in lower for keyword in _FACTCHECK_TRIGGER_KEYWORDS):
        return True
    if any(keyword in text for keyword in _HIGH_RISK_KEYWORDS):
        return True
    return False


def _append_source_block(content: str, results: list[SearchResult], search_ok: bool) -> str:
    if not search_ok:
        return f"{content}\n\n{_SOURCE_HEADER}\n{_NO_SEARCH_MESSAGE}"
    if not results:
        return f"{content}\n\n{_SOURCE_HEADER}\n{_NO_RESULTS_MESSAGE}"

    lines = [_SOURCE_HEADER]
    seen_urls: set[str] = set()
    index = 1
    for item in results:
        title = item.title.strip() or "Untitled"
        url = item.url.strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        lines.append(f"{index}. {title} - {url}")
        index += 1
        if index > _MAX_SOURCE_ITEMS:
            break

    if len(lines) == 1:
        lines.append(_NO_VALID_RESULTS_MESSAGE)
    return f"{content}\n\n" + "\n".join(lines)
