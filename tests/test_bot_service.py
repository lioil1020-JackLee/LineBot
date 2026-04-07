from __future__ import annotations

from linebot_app.db.sqlite import init_db
from linebot_app.repositories.llm_log_repository import LLMLogRepository
from linebot_app.repositories.message_repository import MessageRepository
from linebot_app.repositories.prompt_repository import PromptRepository
from linebot_app.repositories.session_memory_repository import SessionMemoryRepository
from linebot_app.repositories.session_repository import SessionRepository
from linebot_app.services.bot_service import BotService
from linebot_app.services.external_llm_service import ExternalLLMReply
from linebot_app.services.llm_service import LLMReply, LLMServiceError
from linebot_app.services.prompt_service import PromptService
from linebot_app.services.rag_service import RetrievedChunk
from linebot_app.services.response_guard_service import ResponseGuardResult
from linebot_app.services.session_service import SessionService


class _FakeLLMService:
    chat_model = "fake-model"
    max_tokens = 1024
    temperature = 0.7

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises

    def generate_reply(self, *, system_prompt: str, conversation: list[dict[str, str]]) -> LLMReply:
        if self.raises:
            raise self.raises
        if "你是對話摘要器" in system_prompt:
            return LLMReply(
                text="- 偏好：繁體中文\n- 目標：了解 LM Studio",
                model_name="fake-model",
                latency_ms=80,
                prompt_tokens=8,
                completion_tokens=12,
                total_tokens=20,
            )
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


class _FakeExternalLLMService:
    enabled = True

    def generate_reply(self, **kwargs) -> ExternalLLMReply | None:
        return ExternalLLMReply(text="外部模型補充答案", model_name="openai/gpt-5-mini")


class _FakeResponseGuardService:
    def review(self, **kwargs) -> ResponseGuardResult:
        return ResponseGuardResult(
            approved=True,
            score=78,
            issues=["需要更清楚結構"],
            final_answer="這是守門器重寫後的答案",
        )


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


def test_bot_service_updates_session_memory(tmp_path) -> None:
    db_path = str(tmp_path / "app.db")
    init_db(db_path)

    session_repo = SessionRepository(db_path)
    message_repo = MessageRepository(db_path)
    prompt_repo = PromptRepository(db_path)
    llm_log_repo = LLMLogRepository(db_path)
    memory_repo = SessionMemoryRepository(db_path)

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
        rag_service=None,
        rag_enabled=False,
        rag_top_k=3,
        max_context_chars=6000,
        session_memory_repository=memory_repo,
        session_memory_enabled=True,
        session_memory_trigger_messages=2,
        session_memory_window_messages=6,
        session_memory_max_chars=500,
    )
    service.agent_enabled = False

    service.handle_user_message(line_user_id="u-memory", text="你好")

    memory = memory_repo.get(1)
    assert memory is not None
    assert "偏好" in memory.summary
    assert memory.last_message_id > 0


def test_bot_service_rejects_coding_request_when_disabled(tmp_path) -> None:
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
        rag_service=None,
        rag_enabled=False,
        rag_top_k=3,
        max_context_chars=6000,
        coding_assistance_enabled=False,
    )

    reply = service.handle_user_message(line_user_id="u5", text="幫我寫一段 Python code")

    assert "不提供程式碼" in reply


def test_bot_service_applies_response_guard_rewrite(tmp_path) -> None:
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
        rag_service=None,
        rag_enabled=False,
        rag_top_k=3,
        max_context_chars=6000,
        coding_assistance_enabled=False,
        response_guard_service=_FakeResponseGuardService(),
    )
    service.agent_enabled = False

    reply = service.handle_user_message(line_user_id="u6", text="幫我整理減脂飲食重點")

    assert reply == "這是守門器重寫後的答案"


def test_bot_service_extracts_and_persists_tasks(tmp_path) -> None:
    db_path = str(tmp_path / "app.db")
    init_db(db_path)

    session_repo = SessionRepository(db_path)
    message_repo = MessageRepository(db_path)
    prompt_repo = PromptRepository(db_path)
    llm_log_repo = LLMLogRepository(db_path)
    from linebot_app.repositories.session_task_repository import SessionTaskRepository

    task_repo = SessionTaskRepository(db_path)

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
        rag_service=None,
        rag_enabled=False,
        rag_top_k=3,
        max_context_chars=6000,
        coding_assistance_enabled=True,
        session_task_repository=task_repo,
    )
    service.agent_enabled = False

    service.handle_user_message(line_user_id="u-task", text="我想規劃下週健身菜單")
    tasks = task_repo.get_by_session(session_id=1, status="open")

    assert len(tasks) >= 1
    assert "規劃下週健身菜單" in tasks[0].task_text


def test_bot_service_handles_task_commands(tmp_path) -> None:
    db_path = str(tmp_path / "app.db")
    init_db(db_path)

    session_repo = SessionRepository(db_path)
    message_repo = MessageRepository(db_path)
    prompt_repo = PromptRepository(db_path)
    llm_log_repo = LLMLogRepository(db_path)
    from linebot_app.repositories.session_task_repository import SessionTaskRepository

    task_repo = SessionTaskRepository(db_path)

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
        rag_service=None,
        rag_enabled=False,
        rag_top_k=3,
        max_context_chars=6000,
        coding_assistance_enabled=True,
        session_task_repository=task_repo,
    )
    service.agent_enabled = False

    service.handle_user_message(line_user_id="u-task-cmd", text="我想整理本週採買清單")
    list_reply = service.handle_user_message(line_user_id="u-task-cmd", text="查看待辦")
    done_reply = service.handle_user_message(line_user_id="u-task-cmd", text="完成第1項")

    open_tasks = task_repo.get_by_session(session_id=1, status="open")
    done_tasks = task_repo.get_by_session(session_id=1, status="done")

    assert "目前待辦如下" in list_reply
    assert "已完成待辦" in done_reply
    assert len(open_tasks) == 0
    assert len(done_tasks) == 1


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
    assert "信心" in reply


def test_bot_service_uses_external_fallback_when_uncertain(tmp_path) -> None:
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
    llm = _FakeLLMService()
    # 本地模型先回不確定，應觸發外部模型補答。
    llm.generate_reply = lambda **kwargs: LLMReply(
        text="抱歉，我無法確認這題。",
        model_name="fake-model",
        latency_ms=120,
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
    )

    service = BotService(
        session_service=session_service,
        message_repository=message_repo,
        llm_log_repository=llm_log_repo,
        llm_service=llm,
        prompt_service=prompt_service,
        rag_service=None,
        rag_enabled=False,
        rag_top_k=3,
        max_context_chars=6000,
        external_llm_service=_FakeExternalLLMService(),
    )
    service.agent_enabled = False

    reply = service.handle_user_message(line_user_id="u4", text="GPT-4o 發布時間？")

    assert "外部模型補充答案" in reply
