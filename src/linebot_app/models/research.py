from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ResearchLabel = Literal[
    "unknown",
    "weather_disaster",
    "traffic_transit",
    "finance_price",
    "store_service_status",
    "sports_live",
    "news_public_events",
    "gov_policy_notice",
    "entertainment_events",
    "travel_ticketing",
    "person_company_product_status",
    "inventory_local_availability",
    "health_service_availability",
    "platform_system_status",
    "shopping_discount_comparison",
]


class ResearchPlan(BaseModel):
    route: Literal["knowledge_direct", "search_then_answer", "direct_reasoning"] = Field(
        description="High-level decision for how to answer this user turn."
    )
    needs_external_info: bool = Field(
        default=False, description="Whether web research is required to answer safely."
    )
    needs_knowledge_base: bool = Field(
        default=True, description="Whether the local knowledge base should be queried first."
    )
    freshness: Literal["none", "recent", "today", "realtime"] = Field(
        default="none", description="Freshness requirement for external information."
    )
    label: ResearchLabel = Field(
        default="unknown",
        description="Domain label for routing / query planning / evidence sufficiency.",
    )
    official_source_preferred: bool = Field(
        default=False,
        description="Whether official/high-trust sources are preferred/required for this question.",
    )
    search_queries: list[str] = Field(default_factory=list, description="Search queries to run.")
    forbid_unverified_claims: bool = Field(
        default=True,
        description="Whether the answer must avoid unverified factual claims without evidence.",
    )
    answer_style: Literal["concise", "balanced", "deep"] = Field(
        default="balanced", description="Desired response depth for this turn."
    )


class EvidenceItem(BaseModel):
    kind: Literal["knowledge", "web"] = Field(description="Evidence source type.")
    title: str = Field(default="", description="Human-friendly title for the evidence.")
    source: str = Field(default="", description="File path or URL.")
    snippet: str = Field(default="", description="Extracted snippet/summary of evidence.")
    score: float | None = Field(default=None, description="Optional relevance score.")
    fetched_at: str | None = Field(default=None, description="Optional ISO timestamp.")


class EvidenceBundle(BaseModel):
    items: list[EvidenceItem] = Field(default_factory=list)
    sufficient: bool = Field(default=False, description="Whether evidence is sufficient to answer.")
    notes: str = Field(default="", description="Internal notes for composer/guard.")


class AnswerDraft(BaseModel):
    text: str = Field(default="", description="Draft answer before final guarding.")
    used_evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = Field(default="low")
    refusal: bool = Field(default=False, description="Whether the assistant refuses/declines.")

