from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .llm_service import LLMService, LLMServiceError, LMStudioTimeoutError, LMStudioUnavailableError


@dataclass(frozen=True)
class ResponseGuardResult:
    approved: bool
    score: int
    issues: list[str]
    final_answer: str


class ResponseGuardService:
    def __init__(
        self,
        *,
        llm_service: LLMService,
        enabled: bool,
        rewrite_enabled: bool,
        max_input_chars: int = 4000,
        timeout_seconds: int | None = None,
    ) -> None:
        self.llm_service = llm_service
        self.enabled = enabled
        self.rewrite_enabled = rewrite_enabled
        self.max_input_chars = max(0, max_input_chars)
        self.timeout_seconds = timeout_seconds

    def _extract_json(self, text: str) -> dict[str, object] | None:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return data

    def should_review(self, *, question: str, draft_answer: str) -> bool:
        if not self.enabled:
            return False
        if self.max_input_chars <= 0:
            return True
        return (len(question) + len(draft_answer)) <= self.max_input_chars

    def _evaluate(
        self,
        *,
        question: str,
        draft_answer: str,
        has_sources: bool,
    ) -> tuple[bool, int, list[str]]:
        prompt = (
            "你是回答品質審核員。請根據使用者問題與草稿回答，給出品質評估。"
            "僅輸出 JSON。\n"
            "JSON schema: {\"approved\": bool, \"score\": int, \"issues\": [string]}\n"
            "評分 0-100。70 以下視為不通過。\n"
            "若回答與問題不相符、過度自信、未說明限制、條列混亂，應列為 issues。\n"
            f"has_sources={str(has_sources).lower()}"
        )
        reply = self.llm_service.generate_reply(
            system_prompt=prompt,
            conversation=[
                {
                    "role": "user",
                    "content": f"問題：\n{question}\n\n草稿回答：\n{draft_answer}",
                }
            ],
            timeout_seconds=self.timeout_seconds,
        )
        parsed = self._extract_json(reply.text)
        if parsed is None:
            return True, 100, []

        approved = bool(parsed.get("approved", True))
        score = int(parsed.get("score", 100))
        raw_issues = parsed.get("issues", [])
        if isinstance(raw_issues, list):
            issues = [str(item).strip() for item in raw_issues if str(item).strip()]
        else:
            issues = []
        if score < 70:
            approved = False
        return approved, score, issues

    def _rewrite(
        self,
        *,
        question: str,
        draft_answer: str,
        issues: list[str],
        has_sources: bool,
    ) -> str:
        issues_text = "\n".join(f"- {item}" for item in issues) if issues else "- 請提升整體品質"
        prompt = (
            "你是資深內容編輯。請在不編造事實前提下重寫回答。"
            "保持繁體中文、結構清楚、直接解決問題。"
            "若資訊不足，需明確說明限制。"
            f"has_sources={str(has_sources).lower()}"
        )
        reply = self.llm_service.generate_reply(
            system_prompt=prompt,
            conversation=[
                {
                    "role": "user",
                    "content": (
                        f"問題：\n{question}\n\n原回答：\n{draft_answer}\n\n"
                        f"需修正問題：\n{issues_text}\n\n"
                        "請輸出重寫後最終回答。"
                    ),
                }
            ],
            timeout_seconds=self.timeout_seconds,
        )
        return reply.text.strip() or draft_answer

    def review(self, *, question: str, draft_answer: str, has_sources: bool) -> ResponseGuardResult:
        if not self.should_review(question=question, draft_answer=draft_answer):
            return ResponseGuardResult(
                approved=True,
                score=100,
                issues=[],
                final_answer=draft_answer,
            )

        try:
            approved, score, issues = self._evaluate(
                question=question,
                draft_answer=draft_answer,
                has_sources=has_sources,
            )
            if approved or not self.rewrite_enabled:
                return ResponseGuardResult(
                    approved=approved,
                    score=score,
                    issues=issues,
                    final_answer=draft_answer,
                )

            revised = self._rewrite(
                question=question,
                draft_answer=draft_answer,
                issues=issues,
                has_sources=has_sources,
            )
            return ResponseGuardResult(
                approved=True,
                score=max(score, 75),
                issues=issues,
                final_answer=revised,
            )
        except (LMStudioUnavailableError, LMStudioTimeoutError, LLMServiceError, ValueError):
            return ResponseGuardResult(
                approved=True,
                score=100,
                issues=[],
                final_answer=draft_answer,
            )
