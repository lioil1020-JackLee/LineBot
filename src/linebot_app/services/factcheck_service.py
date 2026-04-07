"""
假訊息查證服務。

完整流程：
  1. classify()   — 判斷訊息是否需要查證
  2. extract()    — 抽取可查證主張
  3. search()     — 搜尋相關證據
  4. synthesize() — 整合出查證報告

對外只暴露 try_factcheck(text) -> str | None：
  - 若屬一般聊天，回傳 None（由 BotService 繼續普通對話流程）
  - 若屬可查證主張或高風險訊息，回傳格式化查證結果
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm_service import LLMService

from ..factcheck_prompts import (
    CLASSIFIER_SYSTEM,
    CLAIM_EXTRACTION_SYSTEM,
    SYNTHESIS_SYSTEM,
    claim_extraction_user_prompt,
    classifier_user_prompt,
    synthesis_user_prompt,
)
from ..tools.web_search import SearchResult, format_search_results

logger = logging.getLogger(__name__)

# 訊息類別常數
_GENERAL_CHAT = "general_chat"
_CHECKABLE = "checkable"
_HIGH_RISK = "high_risk"
_CHECKABLE_CATEGORIES = {_CHECKABLE, _HIGH_RISK}

# 太短的訊息（< 15 字）不進查證流程
_MIN_TEXT_LENGTH = 15
_MAX_SOURCE_ITEMS = 5

# 僅在訊息看起來像「需要查證」時才啟動查證流程，避免一般聊天被過度攔截。
_FACTCHECK_TRIGGER_KEYWORDS = (
    "查證",
    "闢謠",
    "謠言",
    "真的假的",
    "是真的嗎",
    "是否屬實",
    "有這回事",
    "聽說",
    "轉傳",
    "新聞",
    "求證",
    "事實",
)

_HIGH_RISK_KEYWORDS = (
    "疫苗",
    "藥",
    "醫療",
    "地震",
    "颱風",
    "海嘯",
    "停電",
    "銀行",
    "投資",
    "股票",
    "詐騙",
    "選舉",
    "政策",
)

# 用於從 LLM 輸出解析 JSON 的 regex
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_FIRST_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class FactCheckConfig:
    """可調整的查證行為參數"""
    max_search_queries: int = 2       # 最多對幾個主張發出搜尋
    max_results_per_query: int = 4    # 每次搜尋保留幾筆結果


class FactCheckService:
    """
    假訊息查證服務。

    可透過 search_fn 注入替換搜尋提供者（預設使用 DuckDuckGo）。
    若 search_fn=None，查證報告中會明確說明缺少即時查證來源。
    """

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def try_factcheck(self, text: str) -> str | None:
        """
        全流程入口。

        Returns:
            None  — 屬一般聊天，交回 BotService 走普通對話
            str   — 查證結果（含格式化報告）
        """
        text = text.strip()
        if len(text) < _MIN_TEXT_LENGTH:
            return None
        if not _should_consider_factcheck(text):
            return None

        # Step 1: 分類
        category = self._classify(text)
        logger.debug("factcheck: category=%s", category)
        if category not in _CHECKABLE_CATEGORIES:
            return None

        # Step 2: 抽取主張
        claims, needs_context, context_hint = self._extract_claims(text)
        if needs_context:
            hint = context_hint or "請提供更多上下文，例如原始訊息連結或具體出處。"
            return f"🔍 這則訊息需要更多資訊才能查證。\n\n{hint}"
        if not claims:
            return None

        # Step 3: 搜尋
        search_results, search_ok = self._search_claims(claims)

        # Step 4: 整合出報告
        return self._synthesize(claims, search_results, search_ok)

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    def _classify(self, text: str) -> str:
        """Step 1：訊息分類。回傳類別字串；錯誤時 fallback 為 general_chat。"""
        try:
            reply = self._llm.generate_reply(
                system_prompt=CLASSIFIER_SYSTEM,
                conversation=[{"role": "user", "content": classifier_user_prompt(text)}],
            )
            return _parse_category(reply.text)
        except Exception:
            logger.exception("factcheck._classify error")
            return _GENERAL_CHAT  # 安全 fallback：不中斷對話

    def _extract_claims(self, text: str) -> tuple[list[str], bool, str]:
        """
        Step 2：主張抽取。
        Returns: (claims, needs_more_context, context_hint)
        """
        try:
            reply = self._llm.generate_reply(
                system_prompt=CLAIM_EXTRACTION_SYSTEM,
                conversation=[{"role": "user", "content": claim_extraction_user_prompt(text)}],
            )
            data = _parse_json(reply.text)
            claims = [str(c).strip() for c in (data.get("claims") or []) if c]
            needs_context = bool(data.get("needs_more_context", False))
            context_hint = str(data.get("context_hint") or "").strip()
            return claims, needs_context, context_hint
        except Exception:
            logger.exception("factcheck._extract_claims error")
            return [], False, ""

    def _search_claims(self, claims: list[str]) -> tuple[list[SearchResult], bool]:
        """
        Step 3：對每個主張做搜尋。
        Returns: (results, search_was_available)
        """
        if self._search_fn is None:
            return [], False

        all_results: list[SearchResult] = []
        try:
            for claim in claims[: self._cfg.max_search_queries]:
                results = self._search_fn(claim)
                all_results.extend(results[: self._cfg.max_results_per_query])
        except Exception:
            logger.exception("factcheck._search_claims error")
            return [], False

        return all_results, True

    def _synthesize(
        self,
        claims: list[str],
        search_results: list[SearchResult],
        search_ok: bool,
    ) -> str:
        """Step 4：呼叫 LLM 整合出查證報告。"""
        search_text = format_search_results(search_results) if search_results else ""
        user_prompt = synthesis_user_prompt(claims, search_text, search_ok)
        try:
            reply = self._llm.generate_reply(
                system_prompt=SYNTHESIS_SYSTEM,
                conversation=[{"role": "user", "content": user_prompt}],
            )
            content = f"【假訊息查證】\n\n{reply.text}"
            return _append_source_block(content, search_results, search_ok)
        except Exception:
            logger.exception("factcheck._synthesize error")
            return "查證流程發生問題，無法完成查證，請稍後再試。"


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> dict:
    """從 LLM 回覆中解析 JSON，容錯 markdown 圍欄和前後雜訊。"""
    cleaned = text.strip()

    # 去除 ```json ... ``` 圍欄
    fence_match = _JSON_FENCE_RE.search(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 找第一個完整 {...} 區塊
    brace_match = _FIRST_BRACE_RE.search(cleaned)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return {}


def _parse_category(text: str) -> str:
    """從分類 LLM 回覆中提取 category 字串。"""
    data = _parse_json(text)
    category = str(data.get("category") or "").strip().lower()
    if category in (_GENERAL_CHAT, _CHECKABLE, _HIGH_RISK):
        return category

    # 容錯：直接搜原文
    lower = text.lower()
    if "high_risk" in lower:
        return _HIGH_RISK
    if "checkable" in lower:
        return _CHECKABLE
    if "general_chat" in lower:
        return _GENERAL_CHAT

    return _GENERAL_CHAT  # 安全 fallback


def _should_consider_factcheck(text: str) -> bool:
    """是否值得啟動查證流程。"""
    lower = text.lower()
    if "http://" in lower or "https://" in lower:
        return True
    if any(keyword in text for keyword in _FACTCHECK_TRIGGER_KEYWORDS):
        return True
    if any(keyword in text for keyword in _HIGH_RISK_KEYWORDS):
        return True
    return False


def _append_source_block(content: str, results: list[SearchResult], search_ok: bool) -> str:
    """在查證報告尾端固定附加來源，避免模型漏引。"""
    if not search_ok:
        return (
            f"{content}\n\n[來源狀態]\n"
            "目前缺少即時查證來源，只能做初步判讀。"
        )

    if not results:
        return f"{content}\n\n[來源狀態]\n本次查證未找到可用來源。"

    lines: list[str] = ["[引用來源]"]
    seen_urls: set[str] = set()
    idx = 1
    for item in results:
        title = item.title.strip() or "未命名來源"
        url = item.url.strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        lines.append(f"{idx}. {title} - {url}")
        idx += 1
        if idx > _MAX_SOURCE_ITEMS:
            break

    if len(lines) == 1:
        lines.append("本次查證未找到可用來源。")

    return f"{content}\n\n" + "\n".join(lines)
