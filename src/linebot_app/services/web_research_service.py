from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..models.research import EvidenceBundle, EvidenceItem, ResearchPlan
from ..tools.fetch_url import fetch_url
from .web_search_service import WebSearchService


@dataclass(frozen=True)
class WebResearchConfig:
    enabled: bool = True
    max_results_per_query: int = 4
    max_fetch_pages: int = 2


class WebResearchService:
    def __init__(
        self,
        *,
        web_search_service: WebSearchService,
        config: WebResearchConfig | None = None,
    ) -> None:
        self.web_search_service = web_search_service
        self.config = config or WebResearchConfig()

    def research(self, *, question: str, plan: ResearchPlan) -> EvidenceBundle:
        if not self.config.enabled or not plan.needs_external_info:
            return EvidenceBundle(items=[], sufficient=False, notes="web_disabled_or_not_needed")

        q = " ".join((question or "").split()).strip()
        queries = [item.strip() for item in (plan.search_queries or []) if item.strip()]
        if not queries and q:
            queries = [q]

        max_per_query = max(1, min(self.config.max_results_per_query, 8))
        fetched = 0
        items: list[EvidenceItem] = []
        seen_urls: set[str] = set()
        fetched_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

        for query in queries:
            results = self.web_search_service.search(query, max_results=max_per_query)
            for result in results[:max_per_query]:
                url = (result.url or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                snippet = (result.snippet or "").strip()
                title = (result.title or "").strip() or url

                if fetched < self.config.max_fetch_pages:
                    page_text = fetch_url(url)
                    invalid_prefixes = (
                        "HTTP ",
                        "讀取逾時",
                        "無效的 URL",
                        "不支援的 Content-Type",
                    )
                    if page_text and not page_text.startswith(invalid_prefixes):
                        # Keep a compact page excerpt.
                        snippet = (page_text[:1200] + "…") if len(page_text) > 1200 else page_text
                        fetched += 1

                items.append(
                    EvidenceItem(
                        kind="web",
                        title=title,
                        source=url,
                        snippet=snippet,
                        score=None,
                        fetched_at=fetched_at,
                    )
                )

        # Conservative sufficiency: require at least 2 distinct sources.
        sufficient = len(items) >= 2
        notes = "web_ok" if sufficient else "web_insufficient"
        return EvidenceBundle(items=items, sufficient=sufficient, notes=notes)

