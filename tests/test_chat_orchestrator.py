from __future__ import annotations

from linebot_app.services.answer_composer_service import AnswerComposerService
from linebot_app.services.chat_orchestrator import ChatOrchestrator
from linebot_app.services.knowledge_first_service import KnowledgeFirstService
from linebot_app.services.research_planner_service import ResearchPlannerService
from linebot_app.services.response_guard_service import ResponseGuardService


class _NoopRepo:
    def add_log(self, **kwargs) -> None:  # noqa: ANN003
        return None


class _MemMessageRepo:
    def __init__(self) -> None:
        self.rows: list[tuple[int, str, str]] = []

    def add_message(
        self,
        *,
        session_id: int,
        role: str,
        content: str,
        source: str,
        token_count=None,
    ) -> None:
        self.rows.append((session_id, role, content))

    def get_recent_messages(self, *, session_id: int, limit: int):
        return []


class _MemSessionService:
    class _Session:
        def __init__(self, sid: int) -> None:
            self.id = sid

    def __init__(self) -> None:
        self._sid = 1

    def get_or_create_session(self, _line_user_id: str):
        return self._Session(self._sid)

    def get_recent_context(self, _session_id: int):
        return []

    def mark_activity(self, _session_id: int) -> None:
        return None


class _StubLLM:
    def __init__(self, text: str) -> None:
        self.text = text
        self.timeout_seconds = 10
        self.max_tokens = 700
        self.chat_model = "stub"

    def generate_reply(self, *, system_prompt: str, conversation: list[dict[str, str]], **kwargs):
        class _Reply:
            def __init__(self, text: str) -> None:
                self.text = text

        return _Reply(self.text)


def test_orchestrator_returns_guarded_answer_without_web_sources() -> None:
    llm = _StubLLM(
        """
        {"approved": true, "score": 100, "issues": []}
        """
    )
    planner = ResearchPlannerService(
        llm_service=_StubLLM(
            '{"route":"direct_reasoning","needs_external_info":false,'
            '"needs_knowledge_base":false,"freshness":"none","search_queries":[],'
            '"forbid_unverified_claims":false,"answer_style":"balanced"}'
        )
    )
    knowledge = KnowledgeFirstService(llm_service=_StubLLM(""), rag_service=None)

    class _Web:
        def research(self, *, question, plan):
            from linebot_app.models.research import EvidenceBundle

            return EvidenceBundle(items=[], sufficient=False, notes="disabled")

    composer = AnswerComposerService(llm_service=_StubLLM("這是一個回答。"))
    guard = ResponseGuardService(llm_service=llm, enabled=True, rewrite_enabled=False)

    orchestrator = ChatOrchestrator(
        session_service=_MemSessionService(),
        message_repository=_MemMessageRepo(),
        llm_log_repository=_NoopRepo(),
        planner=planner,
        knowledge_first=knowledge,
        web_research=_Web(),  # type: ignore[arg-type]
        composer=composer,
        response_guard=guard,
    )

    text = orchestrator.handle_user_message(line_user_id="u1", text="什麼是鋼骨結構？")
    assert "回答" in text

