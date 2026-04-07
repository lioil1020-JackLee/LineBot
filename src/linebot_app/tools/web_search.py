from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_MAX_RESULTS = 5
_SNIPPET_MAX_CHARS = 300


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def web_search(query: str, max_results: int = _MAX_RESULTS) -> list[SearchResult]:
    """用 DuckDuckGo 搜尋網路，回傳標題、URL、摘要清單。

    Args:
        query: 搜尋關鍵字
        max_results: 最多回傳幾筆結果

    Returns:
        SearchResult 清單，失敗時回傳空清單
    """
    try:
        from duckduckgo_search import DDGS  # type: ignore[import-untyped]
    except ModuleNotFoundError:
        logger.warning("duckduckgo_search not installed; web_search disabled")
        return []

    results: list[SearchResult] = []
    try:
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max(1, min(max_results, 10))):
                snippet = (item.get("body") or "").strip()
                if len(snippet) > _SNIPPET_MAX_CHARS:
                    snippet = snippet[:_SNIPPET_MAX_CHARS] + "…"
                results.append(
                    SearchResult(
                        title=(item.get("title") or "").strip(),
                        url=(item.get("href") or "").strip(),
                        snippet=snippet,
                    )
                )
    except Exception:
        logger.exception("web_search error for query=%r", query)

    return results


def format_search_results(results: list[SearchResult]) -> str:
    """將搜尋結果格式化為可注入 prompt 的文字"""
    if not results:
        return "（搜尋無結果）"
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.title}")
        if r.url:
            lines.append(f"   URL: {r.url}")
        if r.snippet:
            lines.append(f"   {r.snippet}")
    return "\n".join(lines)
