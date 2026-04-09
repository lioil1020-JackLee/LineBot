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
    SessionRepository,
)
from .services import (
    AnswerComposerService,
    ChatOrchestrator,
    HealthService,
    KnowledgeFirstService,
    LLMService,
    RAGService,
    ResearchPlannerService,
    ResponseGuardService,
    SessionService,
    WebResearchService,
    WebSearchService,
)

settings = get_settings()
init_db(settings.sqlite_path)

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db(settings.sqlite_path)
    llm_service.try_start_lm_studio(max_wait_seconds=30)
    yield


app = FastAPI(title="LineBot", version="0.1.0", lifespan=lifespan)

session_repository = SessionRepository(settings.sqlite_path)
message_repository = MessageRepository(settings.sqlite_path)
llm_log_repository = LLMLogRepository(settings.sqlite_path)
knowledge_repository = KnowledgeRepository(settings.sqlite_path)
llm_service = LLMService(
    base_url=settings.lm_studio_base_url,
    chat_model=settings.lm_studio_chat_model,
    embed_model=settings.lm_studio_embed_model,
    timeout_seconds=settings.lm_studio_timeout_seconds,
    max_tokens=settings.lm_studio_max_tokens,
    temperature=settings.lm_studio_temperature,
    exe_path=settings.lm_studio_exe_path,
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
planner = ResearchPlannerService(llm_service=llm_service)
knowledge_first = KnowledgeFirstService(
    llm_service=llm_service,
    rag_service=rags_service if settings.rag_enabled else None,
)
web_search_service = WebSearchService.from_settings(settings)
web_research = WebResearchService(web_search_service=web_search_service)
composer = AnswerComposerService(llm_service=llm_service)
chat_orchestrator = ChatOrchestrator(
    session_service=session_service,
    message_repository=message_repository,
    llm_log_repository=llm_log_repository,
    planner=planner,
    knowledge_first=knowledge_first,
    web_research=web_research,
    composer=composer,
    response_guard=response_guard_service,
    web_search_enabled=settings.web_search_enabled,
    web_search_backend=settings.web_search_backend,
)
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


@app.post("/admin/knowledge/reindex")
def admin_knowledge_reindex() -> dict[str, object]:
    result = rags_service.reindex_knowledge()
    return {"ok": True, **result}


@app.get("/admin/llm-logs")
def admin_llm_logs(limit: int = 20) -> dict[str, object]:
    safe_limit = min(max(limit, 1), 200)
    logs = [asdict(item) for item in llm_log_repository.get_recent(limit=safe_limit)]
    return {
        "ok": True,
        "count": len(logs),
        "items": logs,
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
            chat_orchestrator=chat_orchestrator,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"ok": True}
