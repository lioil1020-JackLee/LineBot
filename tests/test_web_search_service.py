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
