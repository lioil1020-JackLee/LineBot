from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from linebot_app.repositories.knowledge_repository import KnowledgeRepository

from .llm_service import LLMService, LLMServiceError, LMStudioTimeoutError, LMStudioUnavailableError


def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    dot = sum(a * b for a, b in zip(vec1, vec2, strict=False))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


@dataclass(frozen=True)
class RetrievedChunk:
    source_path: str
    chunk_index: int
    content: str
    score: float


class RAGService:
    def __init__(
        self,
        *,
        llm_service: LLMService,
        knowledge_repository: KnowledgeRepository,
        knowledge_dir: str,
        chunk_size: int,
        chunk_overlap: int,
    ) -> None:
        self.llm_service = llm_service
        self.knowledge_repository = knowledge_repository
        self.knowledge_dir = Path(knowledge_dir)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def _chunk_text(self, text: str) -> list[str]:
        content = text.strip()
        if not content:
            return []

        chunks: list[str] = []
        start = 0
        step = max(1, self.chunk_size - self.chunk_overlap)
        while start < len(content):
            chunks.append(content[start : start + self.chunk_size])
            start += step
        return chunks

    def reindex_knowledge(self) -> dict[str, int]:
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        indexed_files = 0
        indexed_chunks = 0

        for path in self.knowledge_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".txt", ".md"}:
                continue

            text = path.read_text(encoding="utf-8", errors="ignore")
            chunks = self._chunk_text(text)
            if not chunks:
                continue

            records: list[tuple[int, str, list[float]]] = []
            for idx, chunk in enumerate(chunks):
                embedding = self.llm_service.embed_text(chunk)
                records.append((idx, chunk, embedding))

            self.knowledge_repository.replace_chunks_for_source(
                source_path=str(path.as_posix()),
                chunks=records,
            )
            indexed_files += 1
            indexed_chunks += len(records)

        return {"files": indexed_files, "chunks": indexed_chunks}

    def search(self, *, query: str, top_k: int) -> list[RetrievedChunk]:
        query_text = query.strip()
        if not query_text:
            return []

        try:
            query_embedding = self.llm_service.embed_text(query_text)
        except (LMStudioUnavailableError, LMStudioTimeoutError, LLMServiceError):
            return []

        scored: list[RetrievedChunk] = []
        for chunk in self.knowledge_repository.get_all_chunks():
            score = _cosine_similarity(query_embedding, chunk.embedding)
            scored.append(
                RetrievedChunk(
                    source_path=chunk.source_path,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    score=score,
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        return [item for item in scored[:top_k] if item.content]

    def status(self) -> dict[str, int]:
        return {"chunks": self.knowledge_repository.count_chunks()}
