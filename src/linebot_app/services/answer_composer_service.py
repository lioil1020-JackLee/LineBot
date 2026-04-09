from __future__ import annotations

from dataclasses import dataclass

from ..models.research import AnswerDraft, EvidenceBundle, EvidenceItem, ResearchPlan
from .llm_service import LLMService, LLMServiceError, LMStudioTimeoutError, LMStudioUnavailableError


@dataclass(frozen=True)
class AnswerComposerConfig:
    enabled: bool = True
    max_evidence_items: int = 6


def _format_evidence(items: list[EvidenceItem]) -> str:
    lines: list[str] = []
    for idx, item in enumerate(items, start=1):
        title = (item.title or "").strip() or "未命名來源"
        source = (item.source or "").strip()
        snippet = (item.snippet or "").strip()
        lines.append(f"{idx}. {title}")
        if source:
            lines.append(f"   來源: {source}")
        if snippet:
            lines.append(f"   摘要: {snippet[:400]}")
    return "\n".join(lines).strip()


class AnswerComposerService:
    def __init__(
        self,
        *,
        llm_service: LLMService,
        config: AnswerComposerConfig | None = None,
    ) -> None:
        self.llm_service = llm_service
        self.config = config or AnswerComposerConfig()

    def compose(
        self,
        *,
        question: str,
        plan: ResearchPlan,
        knowledge: EvidenceBundle | None,
        web: EvidenceBundle | None,
        knowledge_draft: str | None = None,
    ) -> AnswerDraft:
        q = " ".join((question or "").split()).strip()
        knowledge_items = (
            (knowledge.items if knowledge else []) if plan.needs_knowledge_base else []
        )
        web_items = (web.items if web else []) if plan.needs_external_info else []
        combined = (knowledge_items + web_items)[: max(0, self.config.max_evidence_items)]

        if plan.forbid_unverified_claims and plan.needs_external_info:
            if not (web and web.sufficient):
                return AnswerDraft(
                    text=(
                        "這題需要即時外部資料才能安全回答，但我目前查到的來源不足以確認。"
                        "你可以告訴我更精確的範圍（例如地區/聯盟/時間），我就能用更聚焦的查詢再整理一次。"
                    ),
                    used_evidence=combined,
                    confidence="low",
                )

        if knowledge_draft and knowledge and knowledge.sufficient and not plan.needs_external_info:
            return AnswerDraft(
                text=knowledge_draft.strip(),
                used_evidence=combined,
                confidence="high",
            )

        evidence_block = _format_evidence(combined)
        style_hint = {
            "concise": "回覆 2-5 句，直接講結論。",
            "balanced": "先給結論，再用 2-4 點條列補充。",
            "deep": "先給結論，再條列分析與比較，必要時提供下一步建議。",
        }.get(plan.answer_style, "先給結論，再用 2-4 點條列補充。")

        system_prompt = (
            "你是博學、條理清楚、重視證據的研究助理。請使用繁體中文。\n"
            "規則：\n"
            "- 只能根據提供的 evidence 做事實性結論；沒有 evidence 的部分要用保守語氣並說明限制。\n"
            "- 不要把『查不到』說成『沒有』。\n"
            "- 遇到需要即時資訊但 evidence 不足時，明確說明不足並建議縮小範圍。\n"
            f"- 風格：{style_hint}\n"
        )

        user_prompt = f"問題：{q}\n\n"
        if evidence_block:
            user_prompt += "[evidence]\n" + evidence_block + "\n\n"
        if knowledge_draft:
            user_prompt += "[knowledge_draft]\n" + knowledge_draft.strip() + "\n\n"
        user_prompt += "請輸出可直接回覆給使用者的最終答案。"

        if not self.config.enabled:
            # Minimal fallback when composer is disabled.
            return AnswerDraft(
                text=(
                    knowledge_draft.strip()
                    if knowledge_draft
                    else "我目前無法產生回覆，請稍後再試。"
                ),
                used_evidence=combined,
                confidence="low",
            )

        try:
            reply = self.llm_service.generate_reply(
                system_prompt=system_prompt,
                conversation=[{"role": "user", "content": user_prompt}],
                timeout_seconds=min(12, self.llm_service.timeout_seconds),
                max_tokens=min(700, self.llm_service.max_tokens),
            )
            text = (reply.text or "").strip()
        except (LMStudioUnavailableError, LMStudioTimeoutError, LLMServiceError):
            text = ""

        if not text:
            text = (
                knowledge_draft.strip()
                if knowledge_draft
                else "我目前暫時無法整理出可靠回覆，請稍後再試。"
            )

        confidence: str = "medium" if combined else "low"
        if web and web.sufficient:
            confidence = "high"
        elif knowledge and knowledge.sufficient:
            confidence = "high"

        return AnswerDraft(text=text, used_evidence=combined, confidence=confidence)  # type: ignore[arg-type]

