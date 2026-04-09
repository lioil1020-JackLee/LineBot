from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models.research import EvidenceBundle, EvidenceItem
from .llm_service import LLMService, LLMServiceError, LMStudioTimeoutError, LMStudioUnavailableError
from .rag_service import RAGService, RetrievedChunk


@dataclass(frozen=True)
class KnowledgeFirstConfig:
    enabled: bool = True
    top_k: int = 3
    min_score: float = 0.45


class KnowledgeFirstService:
    def __init__(
        self,
        *,
        llm_service: LLMService,
        rag_service: RAGService | None,
        config: KnowledgeFirstConfig | None = None,
    ) -> None:
        self.llm_service = llm_service
        self.rag_service = rag_service
        self.config = config or KnowledgeFirstConfig()

    def retrieve(self, *, question: str) -> EvidenceBundle:
        if not self.config.enabled or self.rag_service is None:
            return EvidenceBundle(items=[], sufficient=False, notes="knowledge_disabled")

        q = " ".join((question or "").split()).strip()
        if not q:
            return EvidenceBundle(items=[], sufficient=False, notes="empty_question")

        chunks: list[RetrievedChunk] = []
        try:
            chunks = self.rag_service.search(query=q, top_k=max(1, self.config.top_k))
        except Exception:
            chunks = []

        strong = [
            c
            for c in chunks
            if (c.content or "").strip() and c.score >= self.config.min_score
        ]
        items: list[EvidenceItem] = []
        for c in strong:
            source_name = Path(c.source_path).name
            marker_title = f"{source_name}#{c.chunk_index}"
            items.append(
                EvidenceItem(
                    kind="knowledge",
                    title=marker_title,
                    source=c.source_path,
                    snippet=(c.content or "").strip(),
                    score=c.score,
                )
            )

        # Conservative: only mark sufficient when we have at least 1 strong chunk.
        sufficient = bool(items)
        return EvidenceBundle(
            items=items,
            sufficient=sufficient,
            notes="rag_hit" if sufficient else "rag_miss",
        )

    def draft_grounded_answer(self, *, question: str, evidence: EvidenceBundle) -> str:
        q = " ".join((question or "").split()).strip()
        if not q or not evidence.items:
            return ""

        refs = []
        for item in evidence.items[:6]:
            refs.append(f"- [{item.title}] {item.snippet}")
        prompt = (
            "你是知識庫優先的研究助理。請只根據下列『本地知識庫片段』回答。\n"
            "- 先給結論，再用 2-4 點條列補充。\n"
            "- 若片段不足以支持結論，請明確說『本地知識庫目前不足以回答』並說明缺口。\n\n"
            f"問題：{q}\n\n"
            "[本地知識庫片段]\n"
            + "\n".join(refs)
        )

        try:
            reply = self.llm_service.generate_reply(
                system_prompt="請使用繁體中文作答，禁止捏造片段中不存在的事實。",
                conversation=[{"role": "user", "content": prompt}],
                timeout_seconds=min(10, self.llm_service.timeout_seconds),
                max_tokens=min(520, self.llm_service.max_tokens),
            )
            return (reply.text or "").strip()
        except (LMStudioUnavailableError, LMStudioTimeoutError, LLMServiceError):
            return ""

