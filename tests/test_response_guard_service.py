from __future__ import annotations

from linebot_app.services.llm_service import LLMReply
from linebot_app.services.response_guard_service import ResponseGuardService


class _StubLLM:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[str, list[dict[str, str]]]] = []
        self.chat_model = "stub-model"

    def generate_reply(
        self,
        *,
        system_prompt: str,
        conversation: list[dict[str, str]],
        timeout_seconds=None,
    ) -> LLMReply:
        self.calls.append((system_prompt, conversation))
        text = self._replies[len(self.calls) - 1]
        return LLMReply(
            text=text,
            model_name="stub-model",
            latency_ms=1,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )


def test_response_guard_rewrite_prompt_blocks_editor_leakage() -> None:
    llm = _StubLLM(
        [
            '{"approved": false, "score": 40, "issues": ["回答過度武斷"]}',
            "這是修正後的最終答案。",
        ]
    )
    service = ResponseGuardService(
        llm_service=llm,
        enabled=True,
        rewrite_enabled=True,
        max_input_chars=4000,
    )

    result = service.review(
        question="請問這個說法正確嗎？",
        draft_answer="這一定百分之百正確。",
        has_sources=False,
    )

    rewrite_prompt, rewrite_conversation = llm.calls[1]
    assert "Return only the revised answer" in rewrite_prompt
    assert "Do not leak XML tags" in rewrite_prompt
    assert "<user_question>" in rewrite_conversation[0]["content"]
    assert "<draft_answer>" in rewrite_conversation[0]["content"]
    assert result.final_answer == "這是修正後的最終答案。"
