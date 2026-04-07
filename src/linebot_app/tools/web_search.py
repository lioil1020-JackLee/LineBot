from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

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
    provider = os.getenv("WEB_SEARCH_PROVIDER", "duckduckgo").strip().lower()
    if provider == "perplexity":
        return _web_search_perplexity(query=query, max_results=max_results)

    try:
        from ddgs import DDGS  # type: ignore[import-untyped]
    except ModuleNotFoundError:
        # Backward compatibility for environments that still keep old package name.
        try:
            from duckduckgo_search import DDGS  # type: ignore[import-untyped]
        except ModuleNotFoundError:
            logger.warning("ddgs not installed; web_search disabled")
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


def _web_search_perplexity(query: str, max_results: int = _MAX_RESULTS) -> list[SearchResult]:
    api_key = os.getenv("PERPLEXITY_API_KEY", "").strip()
    if not api_key:
        logger.warning("PERPLEXITY_API_KEY missing; fallback to empty results")
        return []

    base_url = os.getenv("PERPLEXITY_BASE_URL", "https://api.perplexity.ai").rstrip("/")
    model = os.getenv("PERPLEXITY_MODEL", "sonar")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a web research assistant. Provide concise factual answer.",
            },
            {
                "role": "user",
                "content": query,
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
        if response.status_code >= 400:
            logger.warning("perplexity search failed status=%s", response.status_code)
            return []

        data = response.json()
        answer = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        citations = data.get("citations") or []
        results: list[SearchResult] = []

        for url in citations[: max(1, min(max_results, 10))]:
            link = str(url).strip()
            if not link:
                continue
            results.append(
                SearchResult(
                    title="Perplexity Citation",
                    url=link,
                    snippet=answer[:_SNIPPET_MAX_CHARS] if answer else "",
                )
            )

        if results:
            return results
        if answer:
            return [
                SearchResult(
                    title="Perplexity Answer",
                    url="",
                    snippet=answer[:_SNIPPET_MAX_CHARS],
                )
            ]
    except Exception:
        logger.exception("web_search perplexity error for query=%r", query)

    return []


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
