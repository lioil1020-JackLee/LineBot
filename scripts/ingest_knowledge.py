from __future__ import annotations

from linebot_app.config import get_settings
from linebot_app.repositories.knowledge_repository import KnowledgeRepository
from linebot_app.services.llm_service import LLMService
from linebot_app.services.rag_service import RAGService


def main() -> None:
    settings = get_settings()
    llm_service = LLMService(
        base_url=settings.lm_studio_base_url,
        chat_model=settings.lm_studio_chat_model,
        embed_model=settings.lm_studio_embed_model,
        timeout_seconds=settings.lm_studio_timeout_seconds,
        max_tokens=settings.lm_studio_max_tokens,
        temperature=settings.lm_studio_temperature,
    )
    rag_service = RAGService(
        llm_service=llm_service,
        knowledge_repository=KnowledgeRepository(settings.sqlite_path),
        knowledge_dir=settings.knowledge_dir,
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
    )
    result = rag_service.reindex_knowledge()
    print(f"Indexed {result['files']} files and {result['chunks']} chunks")


if __name__ == "__main__":
    main()
