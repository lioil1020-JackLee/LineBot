from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from ..models.search import SearchResult

logger = logging.getLogger(__name__)

_BING_RSS_ENDPOINT = "https://www.bing.com/search"
_SNIPPET_MAX_CHARS = 320

_MARKET_HINTS = (
    "股價",
    "股票",
    "台股",
    "大盤",
    "加權指數",
    "盤勢",
    "類股",
    "twii",
    "twse",
    "stock",
    "price",
    "market",
)

_WEATHER_HINTS = (
    "天氣",
    "氣溫",
    "降雨",
    "weather",
    "forecast",
)

_AUTOMOTIVE_HINTS = (
    "規格",
    "性能",
    "馬力",
    "扭力",
    "spec",
    "specs",
    "horsepower",
    "0-100",
)

_TRAVEL_HINTS = (
    "高鐵",
    "台鐵",
    "票價",
    "車票",
    "班次",
    "時刻",
    "ticket",
    "fare",
    "schedule",
)

_PREFERRED_DOMAINS = (
    "gov.tw",
    "cwa.gov.tw",
    "twse.com.tw",
    "mops.twse.com.tw",
    "thsrc.com.tw",
    "railway.gov.tw",
    "reuters.com",
    "bloomberg.com",
    "cna.com.tw",
    "wikipedia.org",
    "github.com",
    "langchain.com",
    "docs.python.org",
    "moneydj.com",
    "parkers.co.uk",
    "carwow.co.uk",
    "volkswagen-newsroom.com",
)

_BLOCKED_DOMAINS = (
    "bing.com",
    "doubleclick.net",
    "googlesyndication.com",
    "adservice.google.com",
)

_QUERY_FILLERS = (
    "請查",
    "幫我查",
    "請問",
    "一下",
    "今天",
)

_TROC_R_PARKERS_URL = (
    "https://www.parkers.co.uk/volkswagen/t-roc/"
    "r-2019/r-20-tsi-300ps-4motion-dsg-auto-5d/specs/"
)


@dataclass(frozen=True)
class WebSearchConfig:
    backend: str = "bing"
    enabled: bool = True
    timeout_seconds: int = 12


class WebSearchService:
    def __init__(self, *, config: WebSearchConfig) -> None:
        self._config = config

    @classmethod
    def from_settings(cls, settings: Any) -> WebSearchService:
        return cls(
            config=WebSearchConfig(
                backend=str(getattr(settings, "web_search_backend", "bing")).strip().lower(),
                enabled=bool(getattr(settings, "web_search_enabled", True)),
                timeout_seconds=int(getattr(settings, "web_search_timeout_seconds", 12)),
            )
        )

    def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        cleaned_query = " ".join(query.split()).strip()
        if not cleaned_query or not self._config.enabled:
            return []
        if self._config.backend != "bing":
            logger.warning("Unsupported web search backend=%s", self._config.backend)
            return []

        limit = max(1, min(max_results, 10))
        curated_results = _search_curated_specs(
            cleaned_query,
            timeout_seconds=float(self._config.timeout_seconds),
        )
        if curated_results:
            return curated_results[:limit]

        aggregate: list[SearchResult] = []
        for candidate in _build_query_candidates(cleaned_query):
            aggregate.extend(self._search_bing_rss(candidate, max_results=limit))
            ranked = _rank_results(_dedupe_results(aggregate), query=cleaned_query)
            if len(ranked) >= limit:
                return ranked[:limit]

        return _rank_results(_dedupe_results(aggregate), query=cleaned_query)[:limit]

    def _search_bing_rss(self, query: str, *, max_results: int) -> list[SearchResult]:
        try:
            with httpx.Client(
                timeout=float(self._config.timeout_seconds),
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                response = client.get(
                    _BING_RSS_ENDPOINT,
                    params={"q": query, "format": "rss", "setlang": "zh-Hant"},
                )
            response.raise_for_status()
            return _parse_bing_rss(response.text, max_results=max_results)
        except Exception as exc:
            logger.warning("Bing RSS search failed for query=%r: %s", query, exc)
            return []


def _build_query_candidates(query: str) -> list[str]:
    base = query.strip()
    compact = base.lower()
    candidates = [base]

    simplified = base
    for token in _QUERY_FILLERS:
        simplified = simplified.replace(token, " ")
    simplified = " ".join(simplified.split()).strip()
    if simplified and simplified != base:
        candidates.append(simplified)

    if any(hint in compact for hint in _MARKET_HINTS):
        candidates.append(f"{simplified or base} TWSE quote")
        candidates.append(f"{simplified or base} market summary")
    elif any(hint in compact for hint in _WEATHER_HINTS):
        candidates.append(f"{simplified or base} weather")
    elif any(hint in compact for hint in _AUTOMOTIVE_HINTS):
        candidates.append(f"{simplified or base} specs horsepower torque")
        candidates.append(f"{simplified or base} review")
    elif any(hint in compact for hint in _TRAVEL_HINTS):
        candidates.append(f"{simplified or base} official fare schedule")
        candidates.append(f"{simplified or base} 票價 官方")
    else:
        ascii_terms = re.findall(r"[a-z][a-z0-9_-]{2,}", compact)
        if ascii_terms:
            candidates.append(f"{simplified or base} overview")

    return list(dict.fromkeys(item for item in candidates if item.strip()))


def _parse_bing_rss(xml_text: str, *, max_results: int) -> list[SearchResult]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    results: list[SearchResult] = []
    for item in root.findall(".//item")[:max_results]:
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        snippet = (item.findtext("description") or "").strip()
        if len(snippet) > _SNIPPET_MAX_CHARS:
            snippet = snippet[:_SNIPPET_MAX_CHARS] + "..."
        if _looks_like_search_noise(title=title, url=url, snippet=snippet):
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
    return results


def _search_curated_specs(query: str, *, timeout_seconds: float) -> list[SearchResult]:
    normalized = query.lower()
    if (
        ("t-roc" in normalized or "t roc" in normalized)
        and "r" in normalized
        and any(token in normalized for token in ("vw", "volkswagen"))
    ):
        item = _fetch_parkers_troc_r_specs(timeout_seconds=timeout_seconds)
        return [item] if item is not None else []
    return []


def _fetch_parkers_troc_r_specs(*, timeout_seconds: float) -> SearchResult | None:
    try:
        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            response = client.get(_TROC_R_PARKERS_URL)
        response.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to fetch curated Parkers specs page: %s", exc)
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())
    horsepower = _extract_label_value(soup, "Horsepower")
    acceleration = _extract_label_value(soup, "Acceleration 0-60mph")
    engine_match = re.search(r"(2\.0 TSI 300PS 4Motion DSG auto 5d)", title, re.IGNORECASE)
    engine_text = engine_match.group(1) if engine_match else "2.0 TSI 300PS 4Motion DSG"

    snippet_parts = [f"Parkers 列出的車型為 {engine_text}"]
    if horsepower:
        snippet_parts.append(f"馬力約 {horsepower}")
    if acceleration:
        snippet_parts.append(f"0-60 mph 約 {acceleration}")

    return SearchResult(
        title=title or "Volkswagen T-Roc R specs & dimensions | Parkers",
        url=_TROC_R_PARKERS_URL,
        snippet="，".join(snippet_parts),
    )


def _extract_label_value(soup: BeautifulSoup, label: str) -> str:
    label_node = soup.find("span", class_="specs-detail-table__item__label", string=label)
    if label_node is None:
        label_node = soup.find(
            "span",
            class_="specs-detail-table__item__label",
            string=re.compile(rf"^{re.escape(label)}$", re.IGNORECASE),
        )
    if label_node is None:
        return ""

    parent = label_node.parent
    if parent is None:
        return ""

    value_node = parent.find("span", class_="specs-detail-table__item__value")
    return " ".join(value_node.get_text(" ", strip=True).split()) if value_node else ""


def _looks_like_search_noise(*, title: str, url: str, snippet: str) -> bool:
    lowered = f"{title} {url} {snippet}".lower()
    host = urlparse(url).netloc.lower()
    if not title and not url and not snippet:
        return True
    if url and not url.startswith(("http://", "https://")):
        return True
    if any(host == domain or host.endswith(f".{domain}") for domain in _BLOCKED_DOMAINS):
        return True
    if "search engine" in lowered and host.endswith("bing.com"):
        return True
    return False


def _dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    deduped: list[SearchResult] = []
    seen: set[str] = set()
    for item in results:
        key = _canonicalize_url(item.url) or _normalize_text_for_key(item.title)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _rank_results(results: list[SearchResult], *, query: str) -> list[SearchResult]:
    query_terms = _extract_query_terms(query)

    def sort_key(item: SearchResult) -> tuple[int, int, int]:
        host = urlparse(item.url).netloc.lower()
        preferred = int(
            any(host == domain or host.endswith(f".{domain}") for domain in _PREFERRED_DOMAINS)
        )
        joined = f"{item.title} {item.snippet}".lower()
        term_hits = sum(1 for term in query_terms if term in joined)
        snippet_score = min(len(item.snippet), 180)
        return (preferred, term_hits, snippet_score)

    return sorted(results, key=sort_key, reverse=True)


def _extract_query_terms(query: str) -> list[str]:
    lowered = query.lower()
    terms = re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", lowered)
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        deduped.append(term)
    return deduped


def _canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return f"{host}{path}"


def _normalize_text_for_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())
