from __future__ import annotations

import re
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
    PersonaRepository,
    PromptRepository,
    SessionMemoryRepository,
    SessionRepository,
    SessionTaskRepository,
)
from .services import (
    BotService,
    ExternalLLMService,
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

_PERSONA_PRESETS: dict[str, str] = {
    "default": "",
    "virtual_partner": (
        "你現在扮演貼心的虛擬情人，語氣溫柔、主動關心、具陪伴感。"
        "可用親密稱呼，但不得操控、勒索、羞辱、鼓吹極端依賴，"
        "也不得提供露骨成人內容。"
    ),
    "close_friend": (
        "你現在扮演可靠的好友，語氣自然、真誠、有同理心，"
        "多用對話方式陪伴並提供實際建議。"
    ),
    "mentor": (
        "你現在扮演資深導師，回覆要條理化、可執行、重點清楚，"
        "必要時用步驟與清單協助使用者落地行動。"
    ),
    "secretary": (
        "你現在扮演個人秘書，重視效率與準確性，"
        "優先協助安排、整理、提醒與任務拆解。"
    ),
}


def _get_default_search_fn():
    """回傳可用的搜尋函式；若 duckduckgo_search 未安裝則回傳 None。"""
    try:
        from .tools.web_search import web_search
        return web_search
    except Exception:
        return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db(settings.sqlite_path)
    # 嘗試啟動 LM Studio（如果配置了 exe_path）
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
persona_repository = PersonaRepository(settings.sqlite_path)
persona_repository.ensure_builtin_presets(_PERSONA_PRESETS)

if settings.roleplay_enabled and settings.roleplay_persona_prompt.strip():
    env_roleplay = persona_repository.upsert_custom(
        name="env_roleplay",
        prompt=settings.roleplay_persona_prompt.strip(),
        set_active=False,
    )
    active_after_boot = persona_repository.get_active()
    if active_after_boot is None or active_after_boot.name == "default":
        persona_repository.set_active(env_roleplay.name)

active_persona = persona_repository.get_active()
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
external_llm_service = (
    ExternalLLMService(
        base_url=settings.external_llm_base_url,
        api_key=settings.external_llm_api_key,
        model_candidates=[m.strip() for m in settings.external_llm_models.split(",")],
        timeout_seconds=settings.external_llm_timeout_seconds,
    )
    if settings.external_llm_fallback_enabled
    else None
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
    coding_assistance_enabled=settings.coding_assistance_enabled,
    response_guard_service=response_guard_service,
    source_scoring_service=source_scoring_service,
    session_task_repository=session_task_repository,
    task_memory_service=task_memory_service,
    external_llm_service=external_llm_service,
    persona_prompt=(active_persona.prompt if active_persona is not None else ""),
    roleplay_priority_mode=settings.roleplay_priority_mode,
    response_guard_skip_when_persona=settings.response_guard_skip_when_persona,
    agent_fast_mode=settings.agent_fast_mode,
    agent_auto_search=settings.agent_auto_search,
    agent_max_tool_rounds=settings.agent_max_tool_rounds,
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


@app.get("/admin/persona")
def admin_persona_get() -> dict[str, object]:
    active = persona_repository.get_active()
    presets = persona_repository.list_presets()
    return {
        "ok": True,
        "persona_prompt": active.prompt if active is not None else "",
        "active_preset": active.name if active is not None else "default",
        "available_presets": [item.name for item in presets],
    }


@app.post("/admin/persona")
def admin_persona_set(payload: dict[str, str] | None = None) -> dict[str, object]:
    data = payload or {}
    preset = str(data.get("preset", "")).strip().lower()
    custom_prompt = str(data.get("custom_prompt", "")).strip()
    custom_name = str(data.get("name", "custom")).strip().lower()

    if custom_prompt:
        record = persona_repository.upsert_custom(
            name=custom_name or "custom",
            prompt=custom_prompt,
            set_active=True,
        )
        preset_label = record.name
        persona_prompt = record.prompt
    elif preset:
        record = persona_repository.set_active(preset)
        if record is None:
            raise HTTPException(status_code=400, detail=f"Unsupported preset: {preset}")
        preset_label = record.name
        persona_prompt = record.prompt
    else:
        record = persona_repository.set_active("default")
        persona_prompt = record.prompt if record is not None else ""
        preset_label = "default"

    active = bot_service.set_persona_prompt(persona_prompt)
    return {
        "ok": True,
        "persona_prompt": active,
        "preset": preset_label,
    }


@app.get("/admin/persona/presets")
def admin_persona_presets() -> dict[str, object]:
    items = [
        {
            "name": item.name,
            "prompt": item.prompt,
            "is_builtin": item.is_builtin,
            "is_active": item.is_active,
        }
        for item in persona_repository.list_presets()
    ]
    return {
        "ok": True,
        "count": len(items),
        "items": items,
    }


@app.post("/admin/persona/presets")
def admin_persona_presets_upsert(payload: dict[str, object] | None = None) -> dict[str, object]:
    data = payload or {}
    name = str(data.get("name", "")).strip().lower()
    prompt = str(data.get("prompt", "")).strip()
    set_active = bool(data.get("set_active", True))
    if not name or not prompt:
        raise HTTPException(status_code=400, detail="name and prompt are required")

    record = persona_repository.upsert_custom(name=name, prompt=prompt, set_active=set_active)
    if set_active:
        bot_service.set_persona_prompt(record.prompt)

    return {
        "ok": True,
        "item": {
            "name": record.name,
            "prompt": record.prompt,
            "is_builtin": record.is_builtin,
            "is_active": record.is_active,
        },
    }


@app.delete("/admin/persona/presets/{name}")
def admin_persona_presets_delete(name: str) -> dict[str, object]:
    deleted = persona_repository.delete_custom(name)
    if not deleted:
        raise HTTPException(status_code=400, detail="Cannot delete builtin or unknown preset")

    active = persona_repository.get_active()
    bot_service.set_persona_prompt(active.prompt if active is not None else "")
    return {
        "ok": True,
        "deleted": name,
    }


@app.get("/admin/persona/export")
def admin_persona_export() -> dict[str, object]:
    items = [
        {
            "name": item.name,
            "prompt": item.prompt,
            "is_builtin": item.is_builtin,
            "is_active": item.is_active,
        }
        for item in persona_repository.list_presets()
    ]
    return {
        "ok": True,
        "count": len(items),
        "items": items,
    }


@app.post("/admin/persona/import")
def admin_persona_import(payload: dict[str, object] | None = None) -> dict[str, object]:
    data = payload or {}
    raw_items = data.get("items")
    preserve_active = bool(data.get("preserve_active", True))
    if not isinstance(raw_items, list) or not raw_items:
        raise HTTPException(status_code=400, detail="items must be a non-empty list")

    active_name = ""
    imported = 0
    skipped = 0

    for entry in raw_items:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        name = str(entry.get("name", "")).strip().lower()
        prompt = str(entry.get("prompt", "")).strip()
        is_builtin = bool(entry.get("is_builtin", False))
        is_active = bool(entry.get("is_active", False))
        if not name or not prompt:
            skipped += 1
            continue

        # Disallow importing builtin rows to avoid mutating system presets unexpectedly.
        if is_builtin or name in _PERSONA_PRESETS:
            skipped += 1
            continue

        safe_name = re.sub(r"[^a-z0-9_-]+", "_", name).strip("_")
        if not safe_name:
            skipped += 1
            continue

        persona_repository.upsert_custom(name=safe_name, prompt=prompt, set_active=False)
        imported += 1
        if is_active:
            active_name = safe_name

    if active_name and preserve_active:
        active = persona_repository.set_active(active_name)
        bot_service.set_persona_prompt(active.prompt if active is not None else "")
    else:
        active = persona_repository.get_active()
        bot_service.set_persona_prompt(active.prompt if active is not None else "")

    active_after_import = persona_repository.get_active()

    return {
        "ok": True,
        "imported": imported,
        "skipped": skipped,
        "active_preset": active_after_import.name if active_after_import is not None else "default",
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
