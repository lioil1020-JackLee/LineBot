from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from ..models.research import ResearchLabel, ResearchPlan
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
    "報稅",
    "補助",
    "公告",
    "規定",
    "法規",
    "停班停課",
    "罰則",
    "當機",
    "故障",
    "維修",
    "災情",
    "schedule",
    "score",
    "price",
    "weather",
    "latest",
    "breaking",
    "today",
)

_EXTERNAL_BIAS_HINTS = (
    "誰是",
    "董事長",
    "執行長",
    "CEO",
    "老闆",
    "營業",
    "開嗎",
    "幾點",
    "票價",
    "門票",
    "演唱會",
    "場次",
    "行程",
    "規格",
    "評價",
    "比較",
    "review",
    "spec",
    "specs",
)

_SPORTS_TEAM_HINTS = (
    "兄弟",
    "中信兄弟",
    "爪爪",
    "中職",
    "cpbl",
    "mlb",
    "npb",
)

_FILLER_PREFIXES = ("請幫我", "幫我", "請問", "可以幫我", "麻煩", "想問")


def _classify_label(question: str) -> ResearchLabel:
    compact = " ".join((question or "").split()).lower().replace(" ", "")
    if any(token in compact for token in ("天氣", "氣溫", "降雨", "颱風", "地震", "aqi", "紫外線")):
        return "weather_disaster"
    if any(token in compact for token in ("票價", "車票", "機票", "飯店", "門票", "入園", "航班")):
        return "travel_ticketing"
    if any(
        token in compact for token in ("高鐵", "台鐵", "捷運", "公車", "uber", "路況", "塞", "封路")
    ):
        return "traffic_transit"
    if any(
        token in compact
        for token in (
            "股價",
            "股票",
            "匯率",
            "利率",
            "油價",
            "黃金",
            "比特幣",
            "etf",
        )
    ):
        return "finance_price"
    if any(
        token in compact for token in ("營業", "有開", "幾點關", "公休", "排多久", "訂位", "外送")
    ):
        return "store_service_status"
    if any(token in compact for token in ("賽程", "比分", "比賽", "cpbl", "mlb", "npb", "nba")):
        return "sports_live"
    if any(token in compact for token in ("新聞", "快訊", "最新進展", "真的假的", "謠言", "判決")):
        return "news_public_events"
    if any(
        token in compact for token in ("補助", "報稅", "停班停課", "罰則", "公告", "規定", "法規")
    ):
        return "gov_policy_notice"
    if any(token in compact for token in ("演唱會", "場次", "展覽", "市集", "上映", "票賣完")):
        return "entertainment_events"
    if "line" in compact and any(
        token in compact for token in ("壞", "壞掉", "不能用", "當機", "故障")
    ):
        return "platform_system_status"
    if any(token in compact for token in ("ceo", "董事長", "上市", "財報", "新品", "更新", "改版")):
        return "person_company_product_status"
    if any(token in compact for token in ("缺貨", "現貨", "庫存", "買得到", "還有嗎", "補貨")):
        return "inventory_local_availability"
    if any(token in compact for token in ("快篩", "口罩", "退燒藥", "衛生紙", "現貨", "缺貨")):
        return "inventory_local_availability"
    if any(token in compact for token in ("急診", "門診", "掛號", "藥局", "疫苗", "停診")):
        return "health_service_availability"
    if any(
        token in compact for token in ("當機", "故障", "維修", "status", "災情", "伺服器", "vpn")
    ):
        return "platform_system_status"
    if any(token in compact for token in ("特價", "折扣", "比價", "最便宜", "免運")):
        return "shopping_discount_comparison"
    return "unknown"


def _official_source_preferred_for(label: ResearchLabel) -> bool:
    return label in {
        "weather_disaster",
        "finance_price",
        "gov_policy_notice",
        "travel_ticketing",
        "traffic_transit",
        "platform_system_status",
        "health_service_availability",
    }


def _strip_fillers(text: str) -> str:
    s = " ".join((text or "").split()).strip()
    for prefix in _FILLER_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix) :].strip()
    return s


def _rewrite_queries(question: str, *, year: str, max_queries: int) -> list[str]:
    q = _strip_fillers(question)
    compact = q.lower().replace(" ", "")

    queries: list[str] = [q]

    # Sports schedule.
    if any(token in compact for token in ("棒球", "比賽", "賽程", "schedule", "game")) or any(
        token.replace(" ", "") in compact for token in _SPORTS_TEAM_HINTS
    ):
        if "今天" in q or "today" in compact:
            queries.extend(
                [
                    "今日 棒球 賽程",
                    "CPBL 今日賽程",
                    "MLB schedule today",
                    "NPB schedule today",
                ]
            )
        else:
            queries.extend(["棒球 賽程", "CPBL 賽程", "MLB schedule"])

    # Concert / event.
    if any(token in compact for token in ("演唱會", "場次", "tour", "concert")):
        queries.extend(
            [
                f"{q} {year}",
                f"{q} schedule {year}",
                f"{q} 場次 {year}",
            ]
        )

    # Identity / role.
    if any(token in compact for token in ("ceo", "董事長", "執行長", "總經理", "誰是")):
        queries.extend([f"{q} current", f"{q} 現任", f"{q} 董事長"])

    # Store open hours.
    if any(token in compact for token in ("營業", "開嗎", "openinghours", "open")):
        queries.extend([f"{q} 營業時間", f"{q} opening hours", f"{q} 營業中"])

    # Product specs / compare.
    if any(token in compact for token in ("規格", "比較", "評價", "spec", "review")):
        queries.extend([f"{q} specs", f"{q} 規格", f"{q} 比較"])

    # Weather.
    if any(token in compact for token in ("天氣", "降雨", "氣溫", "forecast", "weather")):
        queries.extend([f"{q} 天氣預報", f"{q} 氣象署", f"中央氣象署 {q}"])

    # AQI / air quality.
    if any(token in compact for token in ("aqi", "空氣品質", "紫外線")):
        queries.extend([f"{q} aqicn", f"{q} iqair", f"{q} site:moenv.gov.tw"])

    # Gov policy / notices.
    if any(
        token in compact
        for token in ("報稅", "補助", "停班停課", "罰則", "法規", "公告", "規定", "期限", "截止")
    ):
        queries.extend(
            [
                f"{q} site:gov.tw",
                f"{q} site:law.moj.gov.tw",
                f"{q} site:mof.gov.tw",
                f"{q} {year}",
            ]
        )

    cleaned = [item.strip() for item in queries if item and item.strip()]
    deduped = list(dict.fromkeys(cleaned))
    return deduped[: max(0, max_queries)]


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
    label = _classify_label(q)
    needs_external = any(
        hint.lower().replace(" ", "") in compact for hint in _REALTIME_HINTS
    ) or any(hint.lower().replace(" ", "") in compact for hint in _EXTERNAL_BIAS_HINTS)
    if any(token in compact for token in ("今天", "今日", "today")):
        freshness = "today"
    elif any(token in compact for token in ("最近", "本週", "這週", "thisweek", "recent")):
        freshness = "recent"
    else:
        freshness = "realtime"
    if not needs_external:
        freshness = "none"
    route = "search_then_answer" if needs_external else "knowledge_direct"
    year = str(date.today().year)
    queries = _rewrite_queries(q, year=year, max_queries=4) if needs_external and q else []
    return ResearchPlan(
        route=route,
        needs_external_info=needs_external,
        needs_knowledge_base=True,
        freshness=freshness,  # type: ignore[arg-type]
        label=label,
        official_source_preferred=_official_source_preferred_for(label),
        search_queries=queries,
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
        year = str(date.today().year)
        system_prompt = (
            "你是 Research Planner。你的任務不是回答問題，而是產生『研究計畫』。\n"
            "請只輸出 JSON（不要輸出多餘文字）。\n"
            'JSON schema:\n'
            "{\n"
            '  "route": "knowledge_direct" | "search_then_answer" | "direct_reasoning",\n'
            '  "needs_external_info": boolean,\n'
            '  "needs_knowledge_base": boolean,\n'
            '  "freshness": "none" | "recent" | "today" | "realtime",\n'
            '  "label": '
            '"unknown" | "weather_disaster" | "traffic_transit" | "finance_price" | '
            '"store_service_status" | "sports_live" | "news_public_events" | '
            '"gov_policy_notice" | "entertainment_events" | "travel_ticketing" | '
            '"person_company_product_status" | "inventory_local_availability" | '
            '"health_service_availability" | "platform_system_status" | '
            '"shopping_discount_comparison",\n'
            '  "official_source_preferred": boolean,\n'
            '  "search_queries": string[],\n'
            '  "forbid_unverified_claims": boolean,\n'
            '  "answer_style": "concise" | "balanced" | "deep"\n'
            "}\n\n"
            f"今天日期：{today}\n"
            "- 若題目涉及即時/最新/今天/目前狀態/價格/比分/賽程/天氣/職位身分，"
            "通常 needs_external_info=true。\n"
            "- 若題目是高風險事實題（股價/匯率/票價/法規/停班停課/健康服務/平台狀態），"
            "通常 official_source_preferred=true。\n"
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
        if not plan.label or plan.label == "unknown":
            plan.label = _classify_label(q)
        if plan.official_source_preferred is False:
            plan.official_source_preferred = _official_source_preferred_for(plan.label)
        if plan.label in {
            "weather_disaster",
            "finance_price",
            "gov_policy_notice",
            "travel_ticketing",
            "traffic_transit",
            "platform_system_status",
            "health_service_availability",
        }:
            plan.needs_external_info = True
            plan.forbid_unverified_claims = True
            if plan.freshness == "none":
                plan.freshness = "realtime"
        if plan.needs_external_info:
            if not plan.search_queries:
                plan.search_queries = _rewrite_queries(
                    q,
                    year=year,
                    max_queries=self.config.max_queries,
                )
            else:
                # Add light rewrite expansion as a safety net.
                expanded = _rewrite_queries(
                    q,
                    year=year,
                    max_queries=self.config.max_queries * 2,
                )
                merged = list(dict.fromkeys([*plan.search_queries, *expanded]))
                plan.search_queries = merged[: max(0, self.config.max_queries)]
        return plan

