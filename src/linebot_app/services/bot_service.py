from __future__ import annotations

import logging
from pathlib import Path
import re
from uuid import uuid4

from linebot_app.repositories.llm_log_repository import LLMLogRepository
from linebot_app.repositories.message_repository import MessageRepository
from linebot_app.repositories.session_memory_repository import SessionMemoryRepository
from linebot_app.repositories.session_task_repository import SessionTaskRepository

from .factcheck_service import FactCheckService
from .external_llm_service import ExternalLLMService
from .llm_service import LLMService, LLMServiceError, LMStudioTimeoutError, LMStudioUnavailableError
from .prompt_service import PromptService
from .rag_service import RAGService
from .response_guard_service import ResponseGuardService
from .source_scoring_service import SourceScoringService
from .task_memory_service import TaskMemoryService
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

_CODING_HINTS = (
    "寫程式",
    "寫一段程式",
    "幫我寫 code",
    "幫我改 code",
    "debug",
    "除錯",
    "程式碼",
    "source code",
    "python",
    "javascript",
    "typescript",
    "java",
    "c++",
    "c#",
    "sql",
    "regex",
    "api",
    "github",
    "git",
)

_RUNTIME_CAPABILITY_PROMPT = (
    "\n\n[系統能力說明]\n"
    "- 你是 LINE Bot，可處理文字訊息。\n"
    "- 你可處理使用者透過 LINE 上傳的圖片（OCR 後文字）與檔案（PDF/DOCX/XLSX/PPTX/TXT 類）內容。\n"
    "- 若使用者詢問你是否能讀取文件，應如實回答可透過 LINE 上傳檔案進行解析。\n"
    "- 僅當解析失敗、格式不支援或權限不足時，才說明限制，不要一概宣稱無法讀取檔案。\n"
)

_MEMORY_SUMMARY_PROMPT = (
    "你是對話摘要器。請將使用者偏好、目標、已知限制、待辦事項整理成精簡摘要。"
    "只輸出摘要內容，不要多餘前言。"
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
        session_memory_repository: SessionMemoryRepository | None = None,
        session_memory_enabled: bool = False,
        session_memory_trigger_messages: int = 6,
        session_memory_window_messages: int = 12,
        session_memory_max_chars: int = 1200,
        coding_assistance_enabled: bool = False,
        response_guard_service: ResponseGuardService | None = None,
        source_scoring_service: SourceScoringService | None = None,
        session_task_repository: SessionTaskRepository | None = None,
        task_memory_service: TaskMemoryService | None = None,
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
        self.session_memory_repository = session_memory_repository
        self.session_memory_enabled = session_memory_enabled
        self.session_memory_trigger_messages = max(2, session_memory_trigger_messages)
        self.session_memory_window_messages = max(4, session_memory_window_messages)
        self.session_memory_max_chars = max(200, session_memory_max_chars)
        self.coding_assistance_enabled = coding_assistance_enabled
        self.response_guard_service = response_guard_service
        self.source_scoring_service = source_scoring_service or SourceScoringService()
        self.session_task_repository = session_task_repository
        self.task_memory_service = task_memory_service or TaskMemoryService()
        self.factcheck_service = factcheck_service
        self.external_llm_service = external_llm_service
        self.agent_enabled: bool = True

    def _looks_like_coding_request(self, text: str) -> bool:
        normalized = re.sub(r"\s+", "", text.lower())
        return any(hint.replace(" ", "") in normalized for hint in _CODING_HINTS)

    def _build_memory_summary(self, *, existing_summary: str, dialog_text: str) -> str:
        prompt = (
            f"舊摘要：\n{existing_summary or '（目前無）'}\n\n"
            f"新增對話：\n{dialog_text}\n\n"
            "請輸出更新後摘要，最多 8 點，每點 1 行。"
        )
        reply = self.llm_service.generate_reply(
            system_prompt=_MEMORY_SUMMARY_PROMPT,
            conversation=[{"role": "user", "content": prompt}],
        )
        return reply.text[: self.session_memory_max_chars].strip()

    def _try_update_session_memory(self, *, session_id: int) -> None:
        if not self.session_memory_enabled or self.session_memory_repository is None:
            return

        memory = self.session_memory_repository.get(session_id)
        last_message_id = memory.last_message_id if memory is not None else 0
        existing_summary = memory.summary if memory is not None else ""
        new_messages = self.message_repository.get_messages_after_id(
            session_id=session_id,
            after_id=last_message_id,
            limit=self.session_memory_window_messages,
        )
        if len(new_messages) < self.session_memory_trigger_messages:
            return

        dialog_text = "\n".join(
            f"{item.role}: {item.content}"
            for item in new_messages
            if item.role in {"user", "assistant"} and item.content.strip()
        )
        if not dialog_text:
            return

        try:
            updated_summary = self._build_memory_summary(
                existing_summary=existing_summary,
                dialog_text=dialog_text,
            )
        except (LMStudioUnavailableError, LMStudioTimeoutError, LLMServiceError):
            return

        if not updated_summary:
            return

        self.session_memory_repository.upsert(
            session_id=session_id,
            summary=updated_summary,
            last_message_id=new_messages[-1].id,
        )

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

    def _handle_task_command(self, *, session_id: int, text: str) -> str | None:
        if self.session_task_repository is None or self.task_memory_service is None:
            return None

        parsed = self.task_memory_service.parse_command(text)
        if parsed is None:
            return None

        action, idx = parsed
        open_tasks = self.session_task_repository.get_by_session(session_id=session_id, status="open")

        if action == "list":
            if not open_tasks:
                return "目前沒有待辦事項。"
            lines = [f"{i}. {item.task_text}" for i, item in enumerate(open_tasks[:10], start=1)]
            return "目前待辦如下：\n" + "\n".join(lines)

        if idx is None or idx <= 0:
            return "請提供正確的項目編號，例如：完成第1項。"
        if idx > len(open_tasks):
            return f"找不到第 {idx} 項待辦，請先輸入『查看待辦』確認編號。"

        target = open_tasks[idx - 1]
        if action == "done":
            self.session_task_repository.update_status(task_id=target.id, status="done")
            return f"已完成待辦：{target.task_text}"
        if action == "in_progress":
            self.session_task_repository.update_status(task_id=target.id, status="in_progress")
            return f"已標記進行中：{target.task_text}"

        return None

    def handle_user_message(self, *, line_user_id: str, text: str) -> str:
        incoming_text = text.strip()
        if not incoming_text:
            return "請輸入文字訊息，我才能協助你。"

        if not self.coding_assistance_enabled and self._looks_like_coding_request(incoming_text):
            return (
                "我目前不提供程式碼撰寫、修改或除錯服務。"
                "若你願意，我可以改用白話方式說明觀念、學習路線或幫你整理需求規格。"
            )

        session = self.session_service.get_or_create_session(line_user_id)
        context = self.session_service.get_recent_context(session.id)
        request_id = str(uuid4())

        self.message_repository.add_message(
            session_id=session.id,
            role="user",
            content=incoming_text,
            source="line",
        )

        task_command_reply = self._handle_task_command(session_id=session.id, text=incoming_text)
        if task_command_reply is not None:
            self.message_repository.add_message(
                session_id=session.id,
                role="assistant",
                content=task_command_reply,
                source="line",
            )
            self.session_service.mark_activity(session.id)
            return task_command_reply

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

        if self.session_memory_enabled and self.session_memory_repository is not None:
            memory = self.session_memory_repository.get(session.id)
            if memory is not None and memory.summary.strip():
                system_prompt += (
                    "\n\n[對話長期記憶摘要]\n"
                    f"{memory.summary.strip()}"
                )

        if self.session_task_repository is not None:
            open_tasks = self.session_task_repository.get_by_session(session_id=session.id, status="open")
            if open_tasks:
                task_block = "\n".join(f"- {item.task_text}" for item in open_tasks[:10])
                system_prompt += f"\n\n[使用者待辦事項]\n{task_block}"

        if self.rag_enabled and self.rag_service is not None:
            references = self.rag_service.search(query=incoming_text, top_k=self.rag_top_k)
            if references:
                reference_block = "\n\n".join(
                    (
                        f"- [{Path(item.source_path).name}#{item.chunk_index}]"
                        f"(信心:{self.source_scoring_service.confidence_label(item.score)}) {item.content}"
                    )
                    for item in references
                )
                source_markers = [
                    (
                        f"{Path(item.source_path).name}#{item.chunk_index}"
                        f"(信心:{self.source_scoring_service.confidence_label(item.score)})"
                    )
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

            if self.response_guard_service is not None:
                guard_result = self.response_guard_service.review(
                    question=incoming_text,
                    draft_answer=reply.text,
                    has_sources=bool(source_markers),
                )
                if guard_result.final_answer.strip() and guard_result.final_answer != reply.text:
                    from .llm_service import LLMReply

                    reply = LLMReply(
                        text=guard_result.final_answer,
                        model_name=reply.model_name,
                        latency_ms=reply.latency_ms,
                        prompt_tokens=reply.prompt_tokens,
                        completion_tokens=reply.completion_tokens,
                        total_tokens=reply.total_tokens,
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
        self._try_update_session_memory(session_id=session.id)
        if self.session_task_repository is not None and self.task_memory_service is not None:
            for task in self.task_memory_service.extract_tasks(incoming_text):
                self.session_task_repository.add_task(session_id=session.id, task_text=task)
        if source_markers:
            return f"{reply.text}\n\n參考來源：{', '.join(dict.fromkeys(source_markers))}"
        return reply.text