from __future__ import annotations

import logging
from pathlib import Path
import re
from uuid import uuid4

from linebot_app.repositories.llm_log_repository import LLMLogRepository
from linebot_app.repositories.message_repository import MessageRepository

from .factcheck_service import FactCheckService
from .external_llm_service import ExternalLLMService
from .llm_service import LLMService, LLMServiceError, LMStudioTimeoutError, LMStudioUnavailableError
from .prompt_service import PromptService
from .rag_service import RAGService
from .session_service import SessionService

logger = logging.getLogger(__name__)

_UNCERTAIN_HINTS = (
    "不知道",
    "不清楚",
    "無法確認",
    "無法判斷",
    "不確定",
    "資訊不足",
    "我無法",
    "抱歉，我無法",
)

_RUNTIME_CAPABILITY_PROMPT = (
    "\n\n[系統能力說明]\n"
    "- 你是 LINE Bot，可處理文字訊息。\n"
    "- 你可處理使用者透過 LINE 上傳的圖片（OCR 後文字）與檔案（PDF/DOCX/XLSX/PPTX/TXT 類）內容。\n"
    "- 若使用者詢問你是否能讀取文件，應如實回答可透過 LINE 上傳檔案進行解析。\n"
    "- 僅當解析失敗、格式不支援或權限不足時，才說明限制，不要一概宣稱無法讀取檔案。\n"
)

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
        factcheck_service: FactCheckService | None = None,
        external_llm_service: ExternalLLMService | None = None,
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
        self.factcheck_service = factcheck_service
        self.external_llm_service = external_llm_service
        self.agent_enabled: bool = True

    def _looks_uncertain(self, text: str) -> bool:
        normalized = re.sub(r"\s+", "", text.lower())
        return any(hint.replace(" ", "") in normalized for hint in _UNCERTAIN_HINTS)

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

        # 假訊息查證路由：若訊息屬可查證主張，進入查證流程並提早回傳
        if self.factcheck_service is not None:
            factcheck_result = self.factcheck_service.try_factcheck(incoming_text)
            if factcheck_result is not None:
                self.message_repository.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=factcheck_result,
                    source="line",
                )
                self.session_service.mark_activity(session.id)
                return factcheck_result

        conversation = [
            {"role": message.role, "content": message.content}
            for message in context
            if message.role in {"user", "assistant"}
        ]
        conversation.append({"role": "user", "content": incoming_text})
        conversation = self._truncate_conversation(conversation)
        system_prompt = self.prompt_service.get_active_prompt() + _RUNTIME_CAPABILITY_PROMPT
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

            # 第二層備援：若本地模型最終回答仍不確定，嘗試外部模型（可選）。
            if (
                self.external_llm_service is not None
                and self.external_llm_service.enabled
                and self._looks_uncertain(reply.text)
            ):
                external_reply = self.external_llm_service.generate_reply(
                    system_prompt=system_prompt,
                    conversation=conversation,
                    max_tokens=self.llm_service.max_tokens,
                    temperature=self.llm_service.temperature,
                )
                if external_reply is not None:
                    from .llm_service import LLMReply

                    reply = LLMReply(
                        text=external_reply.text,
                        model_name=external_reply.model_name,
                        latency_ms=0,
                        prompt_tokens=None,
                        completion_tokens=None,
                        total_tokens=None,
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