from __future__ import annotations

from linebot_app.db.sqlite import init_db
from linebot_app.repositories.llm_log_repository import LLMLogRepository
from linebot_app.repositories.message_repository import MessageRepository
from linebot_app.repositories.prompt_repository import PromptRepository
from linebot_app.repositories.session_repository import SessionRepository
from linebot_app.services.bot_service import BotService
from linebot_app.services.llm_service import LLMReply, LLMServiceError
from linebot_app.services.prompt_service import PromptService
from linebot_app.services.rag_service import RetrievedChunk
from linebot_app.services.session_service import SessionService


class _FakeLLMService:
    chat_model = "fake-model"

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises

    def generate_reply(self, *, system_prompt: str, conversation: list[dict[str, str]]) -> LLMReply:
        if self.raises:
            raise self.raises
        return LLMReply(
            text="測試回覆",
            model_name="fake-model",
            latency_ms=120,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
        )


class _FakeRAGService:
    def search(self, *, query: str, top_k: int) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                source_path="data/knowledge/guide.md",
                chunk_index=0,
                content="LineBot 可整合 LM Studio。",
                score=0.9,
            )
        ]


def _build_service(tmp_path) -> tuple[BotService, LLMLogRepository, MessageRepository]:
    db_path = str(tmp_path / "app.db")
    init_db(db_path)

    session_repo = SessionRepository(db_path)
    message_repo = MessageRepository(db_path)
    prompt_repo = PromptRepository(db_path)
    llm_log_repo = LLMLogRepository(db_path)

    session_service = SessionService(
        session_repository=session_repo,
        message_repository=message_repo,
        max_turns=8,
    )
    prompt_service = PromptService(prompt_repository=prompt_repo, default_prompt="請使用繁體中文")
    bot_service = BotService(
        session_service=session_service,
        message_repository=message_repo,
        llm_log_repository=llm_log_repo,
        llm_service=_FakeLLMService(),
        prompt_service=prompt_service,
        rag_service=None,
        rag_enabled=False,
        rag_top_k=3,
        max_context_chars=6000,
    )
    return bot_service, llm_log_repo, message_repo


def test_bot_service_persists_messages_and_logs(tmp_path) -> None:
    service, log_repo, message_repo = _build_service(tmp_path)

    reply = service.handle_user_message(line_user_id="u1", text="你好")

    assert reply == "測試回覆"
    logs = log_repo.get_recent(limit=1)
    assert logs[0].status == "success"
    messages = message_repo.get_recent_messages(session_id=1, limit=10)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"


def test_bot_service_handles_llm_error(tmp_path) -> None:
    db_path = str(tmp_path / "app.db")
    init_db(db_path)

    session_repo = SessionRepository(db_path)
    message_repo = MessageRepository(db_path)
    prompt_repo = PromptRepository(db_path)
    llm_log_repo = LLMLogRepository(db_path)

    session_service = SessionService(
        session_repository=session_repo,
        message_repository=message_repo,
        max_turns=8,
    )
    prompt_service = PromptService(prompt_repository=prompt_repo, default_prompt="請使用繁體中文")
    service = BotService(
        session_service=session_service,
        message_repository=message_repo,
        llm_log_repository=llm_log_repo,
        llm_service=_FakeLLMService(raises=LLMServiceError("bad request")),
        prompt_service=prompt_service,
        rag_service=None,
        rag_enabled=False,
        rag_top_k=3,
        max_context_chars=6000,
    )

    reply = service.handle_user_message(line_user_id="u2", text="你好")

    assert "暫時無法" in reply
    logs = llm_log_repo.get_recent(limit=1)
    assert logs[0].status == "error"


def test_bot_service_adds_rag_citations(tmp_path) -> None:
    db_path = str(tmp_path / "app.db")
    init_db(db_path)

    session_repo = SessionRepository(db_path)
    message_repo = MessageRepository(db_path)
    prompt_repo = PromptRepository(db_path)
    llm_log_repo = LLMLogRepository(db_path)

    session_service = SessionService(
        session_repository=session_repo,
        message_repository=message_repo,
        max_turns=8,
    )
    prompt_service = PromptService(prompt_repository=prompt_repo, default_prompt="請使用繁體中文")
    service = BotService(
        session_service=session_service,
        message_repository=message_repo,
        llm_log_repository=llm_log_repo,
        llm_service=_FakeLLMService(),
        prompt_service=prompt_service,
        rag_service=_FakeRAGService(),
        rag_enabled=True,
        rag_top_k=3,
        max_context_chars=6000,
    )

    reply = service.handle_user_message(line_user_id="u3", text="說明 LM Studio")

    assert "參考來源" in reply
    assert "guide.md#0" in reply
