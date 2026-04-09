from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from ..models.research import ResearchPlan
from .llm_service import LLMService, LLMServiceError, LMStudioTimeoutError, LMStudioUnavailableError


@dataclass(frozen=True)
class PlannerConfig:
    enabled: bool = True
    max_queries: int = 4


_REALTIME_HINTS = (
    "今天",
    "現在",
    "目前",
    "最新",
    "即時",
    "剛剛",
    "新聞",
    "地震",
    "颱風",
    "比分",
    "賽程",
    "股價",
    "匯率",
    "油價",
    "天氣",
    "價格",
    "CEO",
    "總統",
    "上市",
    "上映",
    "活動時間",
    "schedule",
    "score",
    "price",
    "weather",
    "latest",
    "breaking",
    "today",
)


def _extract_json_object(text: str) -> dict[str, object] | None:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _heuristic_plan(question: str) -> ResearchPlan:
    q = " ".join((question or "").split()).strip()
    compact = q.lower().replace(" ", "")
    needs_external = any(hint.lower().replace(" ", "") in compact for hint in _REALTIME_HINTS)
    freshness = "today" if any(token in compact for token in ("今天", "today")) else "realtime"
    if not needs_external:
        freshness = "none"
    route = "search_then_answer" if needs_external else "knowledge_direct"
    return ResearchPlan(
        route=route,
        needs_external_info=needs_external,
        needs_knowledge_base=True,
        freshness=freshness,  # type: ignore[arg-type]
        search_queries=[q] if needs_external and q else [],
        forbid_unverified_claims=needs_external,
        answer_style="balanced",
    )


class ResearchPlannerService:
    def __init__(self, *, llm_service: LLMService, config: PlannerConfig | None = None) -> None:
        self.llm_service = llm_service
        self.config = config or PlannerConfig()

    def plan(self, *, question: str, context: list[dict[str, str]] | None = None) -> ResearchPlan:
        q = " ".join((question or "").split()).strip()
        if not q:
            return _heuristic_plan(q)

        if not self.config.enabled:
            return _heuristic_plan(q)

        today = date.today().isoformat()
        system_prompt = (
            "你是 Research Planner。你的任務不是回答問題，而是產生『研究計畫』。\n"
            "請只輸出 JSON（不要輸出多餘文字）。\n"
            'JSON schema:\n'
            "{\n"
            '  "route": "knowledge_direct" | "search_then_answer" | "direct_reasoning",\n'
            '  "needs_external_info": boolean,\n'
            '  "needs_knowledge_base": boolean,\n'
            '  "freshness": "none" | "recent" | "today" | "realtime",\n'
            '  "search_queries": string[],\n'
            '  "forbid_unverified_claims": boolean,\n'
            '  "answer_style": "concise" | "balanced" | "deep"\n'
            "}\n\n"
            f"今天日期：{today}\n"
            "- 若題目涉及即時/最新/今天/目前狀態/價格/比分/賽程/天氣/職位身分，"
            "通常 needs_external_info=true。\n"
            "- 預設先查本地知識庫：needs_knowledge_base=true。\n"
            "- 若 needs_external_info=true，請提供 2-4 組 search_queries（中英都可）。\n"
            "- 不要在 JSON 裡放註解或多餘欄位。"
        )

        conversation: list[dict[str, str]] = [{"role": "user", "content": q}]
        if context:
            # Keep planner context light to reduce noise.
            tail = [item for item in context if item.get("role") in {"user", "assistant"}][-6:]
            if tail:
                conversation = [
                    {
                        "role": "user",
                        "content": "以下是最近對話（供你判斷是否追問或延續主題，不要直接回答）：\n"
                        + "\n".join(f'{m["role"]}: {m["content"]}' for m in tail),
                    },
                    {"role": "user", "content": f"本輪問題：{q}"},
                ]

        try:
            reply = self.llm_service.generate_reply(
                system_prompt=system_prompt,
                conversation=conversation,
                timeout_seconds=min(10, self.llm_service.timeout_seconds),
                max_tokens=min(320, self.llm_service.max_tokens),
            )
            parsed = _extract_json_object(reply.text)
            if not parsed:
                return _heuristic_plan(q)
            plan = ResearchPlan.model_validate(parsed)
        except (LMStudioUnavailableError, LMStudioTimeoutError, LLMServiceError, ValueError):
            return _heuristic_plan(q)

        # Normalize / cap query count.
        queries = [item.strip() for item in plan.search_queries if item.strip()]
        plan.search_queries = list(dict.fromkeys(queries))[: max(0, self.config.max_queries)]
        if plan.needs_external_info and not plan.search_queries:
            plan.search_queries = [q]
        return plan

