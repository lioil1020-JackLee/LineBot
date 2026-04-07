from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from linebot_app.repositories.llm_log_repository import LLMLogRepository
from linebot_app.repositories.message_repository import MessageRepository

from .llm_service import LLMService, LLMServiceError, LMStudioTimeoutError, LMStudioUnavailableError
from .prompt_service import PromptService
from .rag_service import RAGService
from .session_service import SessionService

logger = logging.getLogger(__name__)

# agent_loop 在 linebot_app package 層級
try:
    from ..agent_loop import run_agent_loop
    _AGENT_LOOP_AVAILABLE = True
except ImportError:
    _AGENT_LOOP_AVAILABLE = False


class BotService:
    def __init__(
        self,
        *,
        session_service: SessionService,
        message_repository: MessageRepository,
        llm_log_repository: LLMLogRepository,
        llm_service: LLMService,
        prompt_service: PromptService,
        rag_service: RAGService | None,
        rag_enabled: bool,
        rag_top_k: int,
        max_context_chars: int,
    ) -> None:
        self.session_service = session_service
        self.message_repository = message_repository
        self.llm_log_repository = llm_log_repository
        self.llm_service = llm_service
        self.prompt_service = prompt_service
        self.rag_service = rag_service
        self.rag_enabled = rag_enabled
        self.rag_top_k = rag_top_k
        self.max_context_chars = max_context_chars
        self.agent_enabled: bool = True

    def _truncate_conversation(self, conversation: list[dict[str, str]]) -> list[dict[str, str]]:
        total = 0
        kept: list[dict[str, str]] = []
        for item in reversed(conversation):
            content = item.get("content", "")
            length = len(content)
            if kept and total + length > self.max_context_chars:
                break
            kept.append(item)
            total += length
        kept.reverse()
        return kept

    def handle_user_message(self, *, line_user_id: str, text: str) -> str:
        incoming_text = text.strip()
        if not incoming_text:
            return "請輸入文字訊息，我才能協助你。"

        session = self.session_service.get_or_create_session(line_user_id)
        context = self.session_service.get_recent_context(session.id)
        request_id = str(uuid4())

        self.message_repository.add_message(
            session_id=session.id,
            role="user",
            content=incoming_text,
            source="line",
        )

        conversation = [
            {"role": message.role, "content": message.content}
            for message in context
            if message.role in {"user", "assistant"}
        ]
        conversation.append({"role": "user", "content": incoming_text})
        conversation = self._truncate_conversation(conversation)
        system_prompt = self.prompt_service.get_active_prompt()
        source_markers: list[str] = []

        if self.rag_enabled and self.rag_service is not None:
            references = self.rag_service.search(query=incoming_text, top_k=self.rag_top_k)
            if references:
                reference_block = "\n\n".join(
                    f"- [{Path(item.source_path).name}#{item.chunk_index}] {item.content}"
                    for item in references
                )
                source_markers = [
                    f"{Path(item.source_path).name}#{item.chunk_index}"
                    for item in references
                ]
                system_prompt += (
                    "\n\n以下為可參考的本地知識庫內容，請優先依此回答，"
                    "若內容不足請明確說明限制：\n"
                    f"{reference_block}"
                )

        try:
            if self.agent_enabled and _AGENT_LOOP_AVAILABLE:
                loop_result = run_agent_loop(
                    llm_service=self.llm_service,
                    system_prompt=system_prompt,
                    conversation=conversation,
                )
                reply_text = loop_result.final_answer
                if loop_result.tool_steps:
                    logger.debug(
                        "agent used %d tool(s): %s",
                        len(loop_result.tool_steps),
                        [s.tool for s in loop_result.tool_steps],
                    )
                # 包裝為 LLMReply-like 物件供後續 log 使用
                from .llm_service import LLMReply
                reply = LLMReply(
                    text=reply_text,
                    model_name=self.llm_service.chat_model,
                    latency_ms=0,
                    prompt_tokens=None,
                    completion_tokens=None,
                    total_tokens=None,
                )
            else:
                reply = self.llm_service.generate_reply(
                    system_prompt=system_prompt,
                    conversation=conversation,
                )
        except LMStudioUnavailableError:
            self.llm_log_repository.add_log(
                request_id=request_id,
                session_id=session.id,
                model_name=self.llm_service.chat_model,
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
                model_name=self.llm_service.chat_model,
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
                model_name=self.llm_service.chat_model,
                latency_ms=None,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                status="error",
                error_message=str(exc),
            )
            return "目前暫時無法產生回覆，請稍後再試。"

        self.message_repository.add_message(
            session_id=session.id,
            role="assistant",
            content=(
                reply.text
                if not source_markers
                else f"{reply.text}\n\n參考來源：{', '.join(dict.fromkeys(source_markers))}"
            ),
            source="line",
            token_count=reply.total_tokens,
        )
        self.llm_log_repository.add_log(
            request_id=request_id,
            session_id=session.id,
            model_name=reply.model_name,
            latency_ms=reply.latency_ms,
            prompt_tokens=reply.prompt_tokens,
            completion_tokens=reply.completion_tokens,
            total_tokens=reply.total_tokens,
            status="success",
            error_message=None,
        )
        self.session_service.mark_activity(session.id)
        if source_markers:
            return f"{reply.text}\n\n參考來源：{', '.join(dict.fromkeys(source_markers))}"
        return reply.text