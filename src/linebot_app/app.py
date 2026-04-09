from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from .bot import handle_webhook
from .config import get_settings
from .db import init_db
from .repositories import (
    KnowledgeRepository,
    LLMLogRepository,
    MessageRepository,
    PromptRepository,
    SessionMemoryRepository,
    SessionRepository,
    SessionTaskRepository,
)
from .services import (
    BotService,
    FactCheckConfig,
    FactCheckService,
    HealthService,
    LLMService,
    ProfileMemoryService,
    PromptService,
    RAGService,
    ResponseGuardService,
    SessionService,
    SourceScoringService,
    TaskMemoryService,
)

settings = get_settings()
init_db(settings.sqlite_path)


def _get_default_search_fn():
    """Return the project's default web-search function."""
    try:
        from .services.web_search_service import WebSearchService

        service = WebSearchService.from_settings(settings)
        return lambda query: service.search(query=query)
    except Exception:
        return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db(settings.sqlite_path)
    llm_service.try_start_lm_studio(max_wait_seconds=30)
    yield


app = FastAPI(title="LineBot", version="0.1.0", lifespan=lifespan)

session_repository = SessionRepository(settings.sqlite_path)
message_repository = MessageRepository(settings.sqlite_path)
prompt_repository = PromptRepository(settings.sqlite_path)
llm_log_repository = LLMLogRepository(settings.sqlite_path)
knowledge_repository = KnowledgeRepository(settings.sqlite_path)
session_memory_repository = SessionMemoryRepository(settings.sqlite_path)
session_task_repository = SessionTaskRepository(settings.sqlite_path)
llm_service = LLMService(
    base_url=settings.lm_studio_base_url,
    chat_model=settings.lm_studio_chat_model,
    embed_model=settings.lm_studio_embed_model,
    timeout_seconds=settings.lm_studio_timeout_seconds,
    max_tokens=settings.lm_studio_max_tokens,
    temperature=settings.lm_studio_temperature,
    exe_path=settings.lm_studio_exe_path,
)
prompt_service = PromptService(
    prompt_repository=prompt_repository,
    default_prompt=settings.system_prompt,
)
rags_service = RAGService(
    llm_service=llm_service,
    knowledge_repository=knowledge_repository,
    knowledge_dir=settings.knowledge_dir,
    chunk_size=settings.rag_chunk_size,
    chunk_overlap=settings.rag_chunk_overlap,
)
session_service = SessionService(
    session_repository=session_repository,
    message_repository=message_repository,
    max_turns=settings.session_max_turns,
)
response_guard_service = ResponseGuardService(
    llm_service=llm_service,
    enabled=settings.response_guard_enabled,
    rewrite_enabled=settings.response_guard_rewrite_enabled,
    max_input_chars=settings.response_guard_max_input_chars,
    timeout_seconds=settings.lm_studio_guard_timeout_seconds,
)
source_scoring_service = SourceScoringService()
profile_memory_service = ProfileMemoryService()
task_memory_service = TaskMemoryService()
bot_service = BotService(
    session_service=session_service,
    message_repository=message_repository,
    llm_log_repository=llm_log_repository,
    llm_service=llm_service,
    prompt_service=prompt_service,
    rag_service=rags_service,
    rag_enabled=settings.rag_enabled,
    rag_top_k=settings.rag_top_k,
    max_context_chars=settings.max_context_chars,
    session_memory_repository=session_memory_repository,
    session_memory_enabled=settings.session_memory_enabled,
    session_memory_trigger_messages=settings.session_memory_trigger_messages,
    session_memory_window_messages=settings.session_memory_window_messages,
    session_memory_max_chars=settings.session_memory_max_chars,
    response_guard_service=response_guard_service,
    source_scoring_service=source_scoring_service,
    session_task_repository=session_task_repository,
    task_memory_service=task_memory_service,
    factcheck_service=(
        FactCheckService(
            llm_service=llm_service,
            search_fn=_get_default_search_fn(),
            config=FactCheckConfig(
                max_search_queries=settings.factcheck_max_search_queries,
                max_results_per_query=settings.factcheck_max_results_per_query,
            ),
        )
        if settings.factcheck_enabled
        else None
    ),
)
bot_service.agent_enabled = settings.agent_enabled
health_service = HealthService(
    llm_service=llm_service,
    llm_log_repository=llm_log_repository,
    sqlite_path=settings.sqlite_path,
    line_configured=settings.line_ready,
)


@app.get("/health")
def health() -> dict[str, object]:
    return health_service.basic()


@app.get("/")
def index() -> dict[str, object]:
    return {
        "name": "linebot-app",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health/detail")
def health_detail() -> dict[str, object]:
    return health_service.detail()


@app.post("/admin/reload-prompt")
def reload_prompt(payload: dict[str, str] | None = None) -> dict[str, object]:
    prompt = (payload or {}).get("prompt")
    return {"ok": True, "active_prompt": prompt_service.reload(prompt)}


@app.get("/admin/session/{line_user_id}")
def admin_session(line_user_id: str) -> dict[str, object]:
    session = session_repository.get_by_line_user_id(line_user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = message_repository.get_recent_messages(
        session_id=session.id,
        limit=settings.session_max_turns * 2,
    )
    return {
        "session": asdict(session),
        "messages": [asdict(message) for message in messages],
    }


@app.get("/admin/session/{line_user_id}/memory")
def admin_session_memory(line_user_id: str) -> dict[str, object]:
    session = session_repository.get_by_line_user_id(line_user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    memory = session_memory_repository.get(session.id)
    if memory is None:
        return {
            "ok": True,
            "session_id": session.id,
            "summary": "",
            "last_message_id": 0,
        }

    return {
        "ok": True,
        "session_id": memory.session_id,
        "summary": memory.summary,
        "last_message_id": memory.last_message_id,
    }


@app.get("/admin/session/{line_user_id}/profile")
def admin_session_profile(line_user_id: str) -> dict[str, object]:
    session = session_repository.get_by_line_user_id(line_user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    memory = session_memory_repository.get(session.id)
    summary = memory.summary if memory is not None else ""
    profile = profile_memory_service.extract(summary)
    return {
        "ok": True,
        "session_id": session.id,
        "profile": profile,
    }


@app.get("/admin/session/{line_user_id}/tasks")
def admin_session_tasks(line_user_id: str, status: str = "open") -> dict[str, object]:
    session = session_repository.get_by_line_user_id(line_user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    tasks = session_task_repository.get_by_session(session_id=session.id, status=status or None)
    return {
        "ok": True,
        "session_id": session.id,
        "count": len(tasks),
        "items": [asdict(item) for item in tasks],
    }


@app.post("/admin/knowledge/reindex")
def admin_knowledge_reindex() -> dict[str, object]:
    result = rags_service.reindex_knowledge()
    return {"ok": True, **result}


@app.get("/admin/knowledge/status")
def admin_knowledge_status() -> dict[str, object]:
    return {
        "ok": True,
        "rag_enabled": settings.rag_enabled,
        "knowledge_dir": settings.knowledge_dir,
        **rags_service.status(),
    }


@app.get("/admin/llm-logs")
def admin_llm_logs(limit: int = 20) -> dict[str, object]:
    safe_limit = min(max(limit, 1), 200)
    logs = [asdict(item) for item in llm_log_repository.get_recent(limit=safe_limit)]
    return {
        "ok": True,
        "count": len(logs),
        "items": logs,
    }


@app.get("/admin/metrics")
def admin_metrics(limit: int = 200) -> dict[str, object]:
    safe_limit = min(max(limit, 10), 1000)
    return {
        "ok": True,
        **health_service.metrics(limit=safe_limit),
        "policy": bot_service.get_policy_metrics(),
    }


@app.get("/admin/model")
def admin_model_get() -> dict[str, object]:
    return {
        "ok": True,
        **llm_service.get_models(),
    }


@app.post("/admin/model")
def admin_model_set(payload: dict[str, str] | None = None) -> dict[str, object]:
    data = payload or {}
    updated = llm_service.set_models(
        chat_model=data.get("chat_model"),
        embed_model=data.get("embed_model"),
    )
    return {
        "ok": True,
        **updated,
    }


@app.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_line_signature: str = Header(default="", alias="X-Line-Signature"),
) -> dict[str, bool]:
    if not settings.line_ready:
        raise HTTPException(status_code=503, detail="LINE credentials are not configured")

    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    body = (await request.body()).decode("utf-8")

    try:
        handle_webhook(
            body=body,
            signature=x_line_signature,
            bot_service=bot_service,
            schedule_background_task=background_tasks.add_task,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"ok": True}
