from __future__ import annotations

import logging
from uuid import uuid4

from ..repositories.llm_log_repository import LLMLogRepository
from ..repositories.message_repository import MessageRepository
from ..services.llm_service import (
    LLMServiceError,
    LMStudioTimeoutError,
    LMStudioUnavailableError,
)
from .answer_composer_service import AnswerComposerService
from .knowledge_first_service import KnowledgeFirstService
from .research_planner_service import ResearchPlannerService
from .response_guard_service import ResponseGuardService
from .session_service import SessionService
from .web_research_service import WebResearchService

logger = logging.getLogger(__name__)

_CAPABILITY_QUERY_HINTS = (
    "能上網嗎",
    "可以上網嗎",
    "可以查網路嗎",
    "能查網路嗎",
    "可以訪問網路嗎",
    "能訪問網路嗎",
    "有訪問網路的工具嗎",
    "有上網工具嗎",
    "有網路工具嗎",
    "可以查資料嗎",
)


class ChatOrchestrator:
    def __init__(
        self,
        *,
        session_service: SessionService,
        message_repository: MessageRepository,
        llm_log_repository: LLMLogRepository,
        planner: ResearchPlannerService,
        knowledge_first: KnowledgeFirstService,
        web_research: WebResearchService,
        composer: AnswerComposerService,
        response_guard: ResponseGuardService,
        web_search_enabled: bool,
        web_search_backend: str,
    ) -> None:
        self.session_service = session_service
        self.message_repository = message_repository
        self.llm_log_repository = llm_log_repository
        self.planner = planner
        self.knowledge_first = knowledge_first
        self.web_research = web_research
        self.composer = composer
        self.response_guard = response_guard
        self.web_search_enabled = web_search_enabled
        self.web_search_backend = web_search_backend.strip().lower()

    def handle_user_message(self, *, line_user_id: str, text: str) -> str:
        incoming_text = (text or "").strip()
        if not incoming_text:
            return "請輸入文字訊息，我才能協助你。"

        compact = incoming_text.replace(" ", "")
        if any(hint.replace(" ", "") in compact for hint in _CAPABILITY_QUERY_HINTS):
            if self.web_search_enabled:
                return (
                    "我有『網路研究』能力：會先用搜尋找到可能的來源，再擷取頁面文字做整理與回答。"
                    f"（目前後端：{self.web_search_backend}）\n"
                    "不過我不保證每次都能找到足夠可靠的即時證據；若來源不足，我會明講不足並請你縮小範圍。"
                )
            return (
                "我目前的『網路搜尋/研究』功能在此環境未啟用，所以無法即時查網路。"
                "如果你願意，我可以改用一般知識先說明概念，或你貼上來源內容我再幫你整理。"
            )

        session = self.session_service.get_or_create_session(line_user_id)
        context_records = self.session_service.get_recent_context(session.id)
        context = [
            {"role": item.role, "content": item.content}
            for item in context_records
            if item.content
        ]

        request_id = str(uuid4())
        self.message_repository.add_message(
            session_id=session.id,
            role="user",
            content=incoming_text,
            source="line",
        )

        try:
            plan = self.planner.plan(question=incoming_text, context=context)
            logger.info(
                "planner route=%s needs_external=%s freshness=%s queries=%s",
                plan.route,
                plan.needs_external_info,
                plan.freshness,
                plan.search_queries,
            )

            knowledge_bundle = self.knowledge_first.retrieve(question=incoming_text)
            knowledge_draft = ""
            if (
                plan.route in {"knowledge_direct", "direct_reasoning"}
                and knowledge_bundle.sufficient
            ):
                knowledge_draft = self.knowledge_first.draft_grounded_answer(
                    question=incoming_text,
                    evidence=knowledge_bundle,
                )

            web_bundle = None
            if plan.route == "search_then_answer" or plan.needs_external_info:
                web_bundle = self.web_research.research(question=incoming_text, plan=plan)
                logger.info(
                    "web_research items=%s sufficient=%s notes=%s",
                    len(web_bundle.items) if web_bundle else 0,
                    getattr(web_bundle, "sufficient", None),
                    getattr(web_bundle, "notes", ""),
                )
                if web_bundle:
                    hosts = []
                    urls = []
                    for item in web_bundle.items[:6]:
                        urls.append(item.source)
                        hosts.append(item.source.split("://", 1)[-1].split("/", 1)[0].lower())
                    logger.info(
                        "web_evidence hosts=%s urls=%s",
                        list(dict.fromkeys(hosts)),
                        urls,
                    )

            draft = self.composer.compose(
                question=incoming_text,
                plan=plan,
                knowledge=knowledge_bundle,
                web=web_bundle,
                knowledge_draft=knowledge_draft,
            )

            guarded = draft.text
            guard_result = self.response_guard.review(
                question=incoming_text,
                draft_answer=guarded,
                has_sources=bool(draft.used_evidence),
            )
            guarded = guard_result.final_answer.strip() or guarded

            self.message_repository.add_message(
                session_id=session.id,
                role="assistant",
                content=guarded,
                source="line",
                token_count=None,
            )

            self.llm_log_repository.add_log(
                request_id=request_id,
                session_id=session.id,
                model_name=None,  # composer/planner use same llm; keep optional
                latency_ms=None,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                status="success",
                error_message=None,
            )
            self.session_service.mark_activity(session.id)
            return guarded
        except LMStudioUnavailableError:
            self.llm_log_repository.add_log(
                request_id=request_id,
                session_id=session.id,
                model_name=None,
                latency_ms=None,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                status="unavailable",
                error_message="LM Studio unavailable",
            )
            return "本地模型目前未啟動，請稍後再試。"
        except LMStudioTimeoutError:
            self.llm_log_repository.add_log(
                request_id=request_id,
                session_id=session.id,
                model_name=None,
                latency_ms=None,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                status="timeout",
                error_message="LM Studio timeout",
            )
            return "目前回應較慢，請稍後再試一次。"
        except LLMServiceError as exc:
            self.llm_log_repository.add_log(
                request_id=request_id,
                session_id=session.id,
                model_name=None,
                latency_ms=None,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                status="error",
                error_message=str(exc),
            )
            return "目前暫時無法產生回覆，請稍後再試。"

