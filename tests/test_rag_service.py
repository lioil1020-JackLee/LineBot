from __future__ import annotations

from linebot_app.db.sqlite import init_db
from linebot_app.repositories.knowledge_repository import KnowledgeRepository
from linebot_app.services.rag_service import RAGService


class _FakeEmbedLLM:
    def embed_text(self, text: str) -> list[float]:
        score = float(len(text) % 10)
        return [score, 1.0]


def test_rag_reindex_and_search(tmp_path) -> None:
    db_path = str(tmp_path / "app.db")
    init_db(db_path)

    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    (knowledge_dir / "doc1.md").write_text(
        "這是第一份文件。\nLineBot 使用 LM Studio。",
        encoding="utf-8",
    )
    (knowledge_dir / "doc2.txt").write_text("第二份文件包含 SQLite 與 session。", encoding="utf-8")

    rag_service = RAGService(
        llm_service=_FakeEmbedLLM(),
        knowledge_repository=KnowledgeRepository(db_path),
        knowledge_dir=str(knowledge_dir),
        chunk_size=20,
        chunk_overlap=5,
    )

    result = rag_service.reindex_knowledge()
    assert result["files"] == 2
    assert result["chunks"] > 0

    hits = rag_service.search(query="LM Studio", top_k=2)
    assert len(hits) > 0
