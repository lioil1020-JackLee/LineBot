from __future__ import annotations

from linebot_app.models.search import SearchResult
from linebot_app.services.web_search_service import (
    WebSearchConfig,
    WebSearchService,
    _parse_bing_rss,
)


def test_web_search_service_uses_single_backend_with_query_variants(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_search(self, query: str, *, max_results: int) -> list[SearchResult]:
        calls.append(query)
        if "weather" in query:
            return [
                SearchResult(
                    title="Taipei Weather",
                    url="https://www.cwa.gov.tw/",
                    snippet="Taipei forecast",
                )
            ]
        return []

    monkeypatch.setattr(WebSearchService, "_search_bing_rss", _fake_search)

    service = WebSearchService(
        config=WebSearchConfig(backend="bing", enabled=True, timeout_seconds=5)
    )
    results = service.search("台北今天天氣如何", max_results=3)

    assert results
    assert calls[0] == "台北今天天氣如何"
    assert any("weather" in item for item in calls[1:])


def test_web_search_service_ranks_preferred_domains(monkeypatch) -> None:
    def _fake_search(self, query: str, *, max_results: int) -> list[SearchResult]:
        return [
            SearchResult(
                title="Example blog",
                url="https://example.com/post",
                snippet="foo",
            ),
            SearchResult(
                title="Central Weather Administration",
                url="https://www.cwa.gov.tw/V8/C/",
                snippet="bar",
            ),
        ]

    monkeypatch.setattr(WebSearchService, "_search_bing_rss", _fake_search)

    service = WebSearchService(config=WebSearchConfig())
    results = service.search("台北 天氣", max_results=2)

    assert results[0].url.startswith("https://www.cwa.gov.tw/")


def test_web_search_does_not_prefer_wikipedia_for_realtime_queries(monkeypatch) -> None:
    def _fake_search(self, query: str, *, max_results: int) -> list[SearchResult]:
        return [
            SearchResult(
                title="Shenda - Wikipedia",
                url="https://zh.wikipedia.org/wiki/%E7%A5%9E%E9%81%94",
                snippet="神達是一家…",
            ),
            SearchResult(
                title="TWSE quote",
                url="https://www.twse.com.tw/zh/stockSearch/stock.html",
                snippet="台灣證券交易所 股價",
            ),
        ]

    monkeypatch.setattr(WebSearchService, "_search_bing_rss", _fake_search)

    service = WebSearchService(config=WebSearchConfig())
    results = service.search("今天 神達 股價", max_results=2)

    assert results
    assert "twse.com.tw" in results[0].url


def test_web_search_realtime_keeps_trying_until_high_trust_domain(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_search(self, query: str, *, max_results: int) -> list[SearchResult]:
        calls.append(query)
        if "site:twse.com.tw" in query:
            return [
                SearchResult(
                    title="TWSE stock search",
                    url="https://www.twse.com.tw/zh/stockSearch/stock.html",
                    snippet="台灣證券交易所",
                )
            ]
        return [
            SearchResult(
                title="Some wiki",
                url="https://zh.wikipedia.org/wiki/%E7%A5%9E%E9%81%94",
                snippet="神達",
            )
        ]

    monkeypatch.setattr(WebSearchService, "_search_bing_rss", _fake_search)

    service = WebSearchService(config=WebSearchConfig())
    results, diag = service.search_with_diagnostics("今天 神達 股價", max_results=2)

    assert results
    assert any("twse.com.tw" in item.url for item in results)
    assert diag.get("realtime_intent") is True
    assert "twse.com.tw" in (diag.get("required_domains") or [])
    assert len(calls) >= 2


def test_web_search_realtime_falls_back_to_ddg_html(monkeypatch) -> None:
    def _fake_bing(self, query: str, *, max_results: int):
        return [SearchResult(title="Some wiki", url="https://zh.wikipedia.org/wiki/X", snippet="")]

    def _fake_ddg(self, query: str, *, max_results: int):
        return (
            [
                SearchResult(
                    title="THSRC fare",
                    url="https://www.thsrc.com.tw/",
                    snippet="",
                )
            ],
            None,
        )

    monkeypatch.setattr(WebSearchService, "_search_bing_rss", _fake_bing)
    monkeypatch.setattr(WebSearchService, "_search_duckduckgo_html", _fake_ddg)

    service = WebSearchService(config=WebSearchConfig())
    results, diag = service.search_with_diagnostics("高鐵 台北 嘉義 票價", max_results=2)

    assert results
    assert diag["reason"] == "bing_rss+ddg_html"


def test_parse_bing_rss_returns_clean_results() -> None:
    xml_text = """
    <rss>
      <channel>
        <item>
          <title>Volkswagen T-Roc R Specs</title>
          <link>https://example.com/troc-r</link>
          <description>300 hp, AWD, 0-100 km/h in 4.9 seconds</description>
        </item>
      </channel>
    </rss>
    """

    results = _parse_bing_rss(xml_text, max_results=3)

    assert results == [
        SearchResult(
            title="Volkswagen T-Roc R Specs",
            url="https://example.com/troc-r",
            snippet="300 hp, AWD, 0-100 km/h in 4.9 seconds",
        )
    ]
