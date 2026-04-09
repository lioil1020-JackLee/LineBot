from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from linebot_app.repositories.llm_log_repository import LLMLogRepository
from linebot_app.repositories.message_repository import MessageRepository
from linebot_app.repositories.session_memory_repository import SessionMemoryRepository
from linebot_app.repositories.session_task_repository import SessionTaskRepository

from ..config import get_settings
from ..policies_loader import load_trusted_domains
from .answer_policy import AnswerMode, decide_intent
from .canned_reply_service import (
    build_capability_inquiry_reply,
    build_self_intro_reply,
)
from .factcheck_service import FactCheckService
from .grounded_reply_service import GroundedReplyService
from .knowledge_answer_service import KnowledgeAnswerService
from .llm_service import LLMService, LLMServiceError, LMStudioTimeoutError, LMStudioUnavailableError
from .market_service import MarketService
from .prompt_service import PromptService
from .rag_service import RAGService
from .response_guard_service import ResponseGuardService
from .session_service import SessionService
from .source_scoring_service import SourceScoringService
from .task_memory_service import TaskMemoryService
from .weather_service import WeatherService
from .web_search_service import WebSearchService

logger = logging.getLogger(__name__)

_FAST_FALLBACK_SEARCH_HINTS = (
    "天氣",
    "最新",
    "即時",
    "新聞",
    "交通",
    "weather",
    "forecast",
)

_SEARCH_BLOCKED_KEYWORDS = (
    "貸款",
    "信貸",
    "借款",
    "融資",
    "免審",
    "快速過件",
    "免費諮詢",
)

_SEARCH_BLOCKED_DOMAINS = (
    "doubleclick.net",
    "googlesyndication.com",
    "adservice.google.com",
)

_INSTRUCTION_LEAK_LINE_HINTS = (
    "明確比較差異",
    "先給結論，再指出怎麼選",
    "先給結論，再指出",
    "請按順序帶使用者",
    "每步保持明確、可執行",
    "這題偏簡單",
    "這題中等複雜",
    "這題較複雜",
    "使用者目前仍在延續同一主題",
    "這輪請承接前文",
    "若前面已經有方向",
    "請直接推定最常見且最實用的意圖開始回答",
    "只在最後補 1 個最關鍵追問",
    "如果資訊不足，應先說明缺少什麼",
    "在未確認事實之前，不要隨意編造",
)

_REALTIME_SENSITIVE_HINTS = (
    "最新",
    "即時",
    "剛剛",
    "新聞",
    "地震",
    "颱風",
    "疫情",
    "匯率",
    "油價",
    "real-time",
    "latest",
    "breaking",
)

_TRUSTED_SOURCE_DOMAINS = (
    "gov.tw",
    "cwa.gov.tw",
    "twse.com.tw",
    "yahoo.com",
    "reuters.com",
    "bloomberg.com",
    "bbc.com",
    "cna.com.tw",
)

_FRESHNESS_HINTS = (
    "分鐘前",
    "小時前",
    "today",
    "latest",
    "breaking",
    "更新",
    "即時",
)

_CLARIFY_GENERIC = "你可以先告訴我你要的地區或時間範圍嗎？例如：台北、今天下午、最新 1 小時。"
_CLARIFY_WEATHER = "你可以補一個地點與時段嗎？例如：台北今天晚上、淡水明天早上。"
_CLARIFY_NEWS = "你想看哪個地區或主題的最新資訊？例如：台灣地震、台北交通、國際科技新聞。"

_OVERCONFIDENT_PHRASES = (
    "一定",
    "百分之百",
    "絕對",
    "保證",
    "完全沒錯",
    "毫無疑問",
)

_FOLLOWUP_PREFIX_HINTS = (
    "那",
    "所以",
    "然後",
    "可是",
    "但",
    "另外",
    "順便",
    "接著",
    "再來",
    "如果",
    "那我",
    "那要",
    "要怎麼",
    "怎麼做",
)

_FOLLOWUP_HINTS_BY_MODE = {
    AnswerMode.GENERAL: "如果你願意，我可以再幫你整理成 3 點重點與可執行下一步。",
    AnswerMode.WEATHER: "如果你要，我可以接著給你出門時段建議（早上/下午/晚上）。",
    AnswerMode.MARKET: "如果你要，我可以再補盤勢重點與風險提醒。",
    AnswerMode.REALTIME_SENSITIVE: "如果你要，我可以再幫你追下一次更新並整理差異。",
}

_INTENT_AMBIGUOUS_HINTS = (
    "那",
    "那呢",
    "那要",
    "那這個",
    "這個",
    "那個",
    "要怎麼",
    "怎麼做",
    "然後",
    "接著",
)

_DEPTH_COMPLEXITY_HINTS = (
    "比較",
    "差異",
    "分析",
    "規劃",
    "計畫",
    "步驟",
    "風險",
    "原因",
    "優缺點",
    "策略",
)

_DEPTH_CONCISE_PROMPT = (
    "\n\n[回答深度: concise]\n"
    "- 這題偏簡單，請用 2-4 句直接回答。\n"
    "- 避免冗長背景與過多延伸。\n"
)

_DEPTH_BALANCED_PROMPT = (
    "\n\n[回答深度: balanced]\n"
    "- 這題中等複雜，先給結論，再補 2-3 個重點。\n"
    "- 保持實用，不需過度展開。\n"
)

_DEPTH_DEEP_PROMPT = (
    "\n\n[回答深度: deep]\n"
    "- 這題較複雜，請先給整體結論，再條理化說明關鍵步驟與風險。\n"
    "- 可提供精簡執行建議，避免空泛。\n"
)

_TERSE_QUERY_GUIDANCE_PROMPT = (
    "\n\n[短主題提問處理]\n"
    "- 使用者這題像是簡短主題或關鍵字提問。\n"
    "- 請直接推定最常見且最實用的意圖開始回答，不要只回『請再具體一點』。\n"
    "- 先給一個可用起手式，再補 2-3 個最重要重點。\n"
    "- 若真的需要補資訊，只在最後補 1 個最關鍵追問。\n"
)

_TOPIC_TRAILING_PARTICLES = "呢嗎啊呀吧啦喔哦"
_TOPIC_STOP_TERMS = {
    "這個",
    "那個",
    "這題",
    "那題",
    "一下",
    "一下子",
    "幫我",
    "請問",
    "可以",
    "怎麼",
    "如何",
}

_GENERIC_FOLLOWUP_QUESTIONS = (
    "怎麼開始",
    "要怎麼開始",
    "怎麼做",
    "要怎麼做",
    "如何開始",
    "下一步",
)

_GENERIC_FOLLOWUP_ASPECTS = {
    "預算",
    "費用",
    "行程",
    "時間",
    "交通",
    "住宿",
    "飲食",
    "景點",
    "安排",
}

_GROUNDING_SYSTEM_PROMPT = (
    "你是即時資訊整理助理。"
    "只能根據提供的來源摘要回答，不可自行補完未出現的事實。"
    "若來源不足，必須明確說明不足。"
)

_RUNTIME_CAPABILITY_PROMPT = (
    "\n\n[系統能力說明]\n"
    "- 你是 LINE Bot，可處理文字訊息。\n"
    "- 你可處理使用者透過 LINE 上傳的圖片（OCR 後文字）與檔案（PDF/DOCX/XLSX/PPTX/TXT 類）內容。\n"
    "- 若使用者詢問你是否能讀取文件，應如實回答可透過 LINE 上傳檔案進行解析。\n"
    "- 僅當解析失敗、格式不支援或權限不足時，才說明限制，不要一概宣稱無法讀取檔案。\n"
)

_SEARCH_FIRST_PROMPT = (
    "\n\n[即時與後截止資料策略]\n"
    "- 遇到即時性或可能超過模型知識截止時間的問題"
    "（例如：今日股價、最新新聞、匯率、油價、地震、颱風），"
    "優先使用工具查詢再回答。\n"
    "- 回答時先直接給結論，再用 2-4 點整理關鍵數據；不要只貼網址。\n"
    "- 若查不到足夠資料，明確說明缺口與已查到的內容，不要編造。\n"
)

_MEMORY_SUMMARY_PROMPT = (
    "你是對話摘要器。請將使用者偏好、目標、已知限制、待辦事項整理成精簡摘要。"
    "只輸出摘要內容，不要多餘前言。"
)

_RESPONSE_STYLE_TEMPLATE_PROMPT = (
    "\n\n[回答風格模板]\n"
    "- 請以繁體中文作答，回覆要清楚、實用、自然。\n"
    "- 若資訊不足，先明確說明缺少什麼，再提供可行替代方案。\n"
    "- 若未查證，不要自行編造具體商品代號、法規、日期、價格、來源或專業資格。\n"
    "- 若使用者已提供可用材料、預算、地點或限制，請優先依那些條件回答，不要隨意腦補額外前提。\n"
)

_RESPONSE_STYLE_COMPACT_PROMPT = (
    "\n\n[回答風格: compact]\n"
    "- 回覆總長盡量控制在 6-10 行。\n"
    "- 先給一句結論，再給最多 3 點重點。\n"
    "- 最後只給 1 個最重要下一步。\n"
    "- 省略不必要背景描述。\n"
)

_RESPONSE_STYLE_DETAILED_PROMPT = (
    "\n\n[回答風格: detailed]\n"
    "- 優先用自然語氣回答，不必每次都固定成『結論/重點/下一步』格式。\n"
    "- 視問題複雜度決定長度；簡單問題短答、複雜問題再條列。\n"
    "- 需要時再使用清單或小標，不要過度模板化。\n"
    "- 不要輸出固定標籤如『一句結論：』『重點整理：』『可執行下一步：』。\n"
)

_QA_STYLE_PROMPT = (
    "\n\n[題型模板: 一般問答]\n"
    "- 先直接回答問題。\n"
    "- 再補 2-3 個最重要重點。\n"
    "- 若有不確定處，明確標示並給可驗證方式。\n"
    "- 請自然敘述，不要套固定標題格式。\n"
)

_GENERAL_INTENT_PROMPTS = {
    "answer": (
        "\n\n[回答任務: direct_answer]\n"
        "- 直接回答使用者目前的問題。\n"
        "- 補最重要的背景或限制即可，不要繞太遠。\n"
    ),
    "explain": (
        "\n\n[回答任務: explain]\n"
        "- 把概念講清楚，先白話定義，再補 2-3 個關鍵點。\n"
        "- 優先降低理解門檻，不要堆術語。\n"
    ),
    "compare": (
        "\n\n[回答任務: compare]\n"
        "- 明確比較差異、優缺點、適用情境。\n"
        "- 先給結論，再指出怎麼選比較實際。\n"
    ),
    "plan": (
        "\n\n[回答任務: plan]\n"
        "- 將問題拆成階段、步驟或可執行順序。\n"
        "- 優先給使用者現在就能做的下一步。\n"
    ),
    "recommend": (
        "\n\n[回答任務: recommend]\n"
        "- 明確給出建議與選擇依據。\n"
        "- 若沒有單一最佳答案，請說明依情境怎麼選。\n"
    ),
    "troubleshoot": (
        "\n\n[回答任務: troubleshoot]\n"
        "- 先判斷最可能原因，再提供排查順序。\n"
        "- 優先給低成本、可立即驗證的檢查步驟。\n"
    ),
}

_GENERAL_EXPLAIN_HINTS = (
    "是什麼",
    "什麼是",
    "意思",
    "原理",
    "為什麼",
)

_GENERAL_COMPARE_HINTS = (
    "比較",
    "差異",
    "差別",
    "哪個好",
    "哪個比較",
    "vs",
)

_GENERAL_PLAN_HINTS = (
    "規劃",
    "計畫",
    "安排",
    "步驟",
    "流程",
    "怎麼開始",
    "下一步",
)

_GENERAL_RECOMMEND_HINTS = (
    "推薦",
    "建議",
    "該買",
    "該選",
    "適合",
)

_GENERAL_TROUBLESHOOT_HINTS = (
    "壞掉",
    "沒反應",
    "失敗",
    "不能",
    "無法",
    "卡住",
    "問題",
    "怎麼辦",
)

_GENERAL_SEARCH_EXPLICIT_HINTS = (
    "查一下",
    "幫我查",
    "幫我找",
    "搜尋",
    "找資料",
    "資料來源",
    "來源",
)

_GENERAL_FINANCE_HINTS = (
    "etf",
    "定存",
    "投資",
    "理財",
    "基金",
    "股票",
    "股息",
    "資產配置",
    "配置",
    "閒錢",
    "存款",
    "債券",
    "報酬",
    "風險",
)

_GENERAL_HEALTH_HINTS = (
    "胃脹氣",
    "脹氣",
    "喉嚨痛",
    "發燒",
    "退燒",
    "咳嗽",
    "鼻塞",
    "肚子痛",
    "頭痛",
    "腹瀉",
    "過敏",
    "不舒服",
    "症狀",
    "舒緩",
    "就醫",
    "小孩發燒",
)

_GENERAL_TRAVEL_HINTS = (
    "旅遊",
    "旅行",
    "自由行",
    "東京",
    "日本",
    "行程",
    "住宿",
    "景點",
    "兩天一夜",
    "三天兩夜",
    "出國",
    "交通",
    "機票",
)

_GENERAL_PRACTICAL_HINTS = (
    "晚餐",
    "早餐",
    "午餐",
    "冰箱",
    "浴室",
    "發霉",
    "整理",
    "收納",
    "垃圾",
    "小朋友",
    "小孩",
    "不睡",
    "家裡",
    "打掃",
    "清潔",
    "食材",
    "雞蛋",
    "豆腐",
    "青菜",
    "吐司",
    "香蕉",
)

_GENERAL_DOMAIN_PROMPTS = {
    "finance": (
        "\n\n[題型補強: 理財]\n"
        "- 只提供一般性理財比較與風險提醒，不做個人化投資指示。\n"
        "- 不要列講座、課程、熱門商品、商品代號、報酬保證或未查證數字。\n"
        "- 比較時聚焦風險、波動、流動性、資金用途與期限，直接講怎麼選比較穩。\n"
    ),
    "health": (
        "\n\n[題型補強: 健康]\n"
        "- 先給保守、常見且低風險的居家處理與觀察重點。\n"
        "- 不要推薦特定品牌、保健品、療程或處方，也不要編造病因與專業判斷。\n"
        "- 若症狀持續、加重，或出現高燒、呼吸困難、脫水、劇痛、吞嚥困難等紅旗，明確建議就醫。\n"
    ),
    "travel": (
        "\n\n[題型補強: 旅遊]\n"
        "- 不要列搜尋結果標題、文章名稱或網址清單當成回答主體。\n"
        "- 若有預算、天數、地點，直接拆成可落地安排、注意事項或預算分配。\n"
        "- 優先回答交通、住宿區域、行程節奏、證件與天氣準備，避免空泛文化介紹。\n"
    ),
    "practical": (
        "\n\n[題型補強: 家庭日常]\n"
        "- 這類是餐食、清潔、整理、育兒或家務題，先直接給最可行做法，再補 2-4 步。\n"
        "- 優先用現有材料、低成本、今天就能做的方法，不要太快叫使用者購買專用品或找人處理。\n"
        "- 語氣像會做事的家庭助理，不要像百科條目。\n"
    ),
}

_RESPONSE_PACING_PROMPTS = {
    "default": "",
    "conclusion_first": (
        "\n\n[回答節奏: conclusion_first]\n"
        "- 先用 1-2 句講結論或最推薦做法。\n"
        "- 再補必要原因與限制，不要先鋪陳太久。\n"
    ),
    "step_by_step": (
        "\n\n[回答節奏: step_by_step]\n"
        "- 請按順序帶使用者一步一步做。\n"
        "- 每步保持明確、可執行，避免一次塞太多資訊。\n"
    ),
    "options_first": (
        "\n\n[回答節奏: options_first]\n"
        "- 先列出 2-3 個可行選項，再說各自適用情境。\n"
        "- 最後補你最建議的選擇。\n"
    ),
}

_PACING_CONCLUSION_HINTS = (
    "直接講重點",
    "先講結論",
    "簡單講",
    "一句話",
    "重點就好",
)

_PACING_STEP_BY_STEP_HINTS = (
    "一步一步",
    "手把手",
    "慢慢來",
    "帶我做",
    "教我怎麼做",
)

_PACING_OPTIONS_HINTS = (
    "有哪些選擇",
    "有哪些方案",
    "給我選項",
    "怎麼選",
)

_AGENT_FAST_MODE = True
_AGENT_AUTO_SEARCH = True
_AGENT_MAX_TOOL_ROUNDS = 2

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
        self.agent_enabled: bool = True
        self.weather_service = WeatherService()
        self.market_service = MarketService()
        self.grounded_reply_service = GroundedReplyService()
        self.knowledge_answer_service = KnowledgeAnswerService()
        self.trusted_source_domains = load_trusted_domains(defaults=_TRUSTED_SOURCE_DOMAINS)
        self._policy_metrics = {
            "mode.general": 0,
            "mode.weather": 0,
            "mode.market": 0,
            "mode.realtime_sensitive": 0,
            "route.market.grounded": 0,
            "route.market.fail_closed": 0,
            "route.realtime.grounded": 0,
            "route.realtime.fail_closed": 0,
            "route.realtime.clarify": 0,
            "route.realtime.confidence.high": 0,
            "route.realtime.confidence.medium": 0,
            "route.realtime.confidence.low": 0,
            "route.weather.grounded": 0,
            "guard.coding_blocked": 0,
            "guard.overconfidence_softened": 0,
            "route.intent_gate.general": 0,
            "route.intent_gate.weather": 0,
            "route.intent_gate.market": 0,
            "route.intent_gate.realtime_sensitive": 0,
            "route.intent_gate.self_intro": 0,
            "route.intent_gate.coding_blocked": 0,
            "route.intent_gate.capability_inquiry": 0,
            "route.capability_inquiry": 0,
            "route.general.self_intro": 0,
            "route.general.vehicle_specs_grounded": 0,
            "route.general.lookup_grounded": 0,
            "route.knowledge.local_first": 0,
            "route.knowledge.web_fallback": 0,
            "route.general.continuity_applied": 0,
            "route.general.intent_augmented": 0,
            "route.general.topic_shift_detected": 0,
            "route.general.topic_shift_hard_reset": 0,
            "route.general.terse_query_guided": 0,
            "route.general.goal_continuity_guided": 0,
            "route.general.response_pacing.conclusion_first": 0,
            "route.general.response_pacing.step_by_step": 0,
            "route.general.response_pacing.options_first": 0,
            "route.general.response_intent.answer": 0,
            "route.general.response_intent.explain": 0,
            "route.general.response_intent.compare": 0,
            "route.general.response_intent.plan": 0,
            "route.general.response_intent.recommend": 0,
            "route.general.response_intent.troubleshoot": 0,
            "route.general.depth.concise": 0,
            "route.general.depth.balanced": 0,
            "route.general.depth.deep": 0,
        }

    def _bump_metric(self, key: str) -> None:
        self._policy_metrics[key] = self._policy_metrics.get(key, 0) + 1

    def get_policy_metrics(self) -> dict[str, object]:
        return {
            "counters": dict(self._policy_metrics),
            "trusted_source_domains": sorted(self.trusted_source_domains),
            "trusted_source_count": len(self.trusted_source_domains),
        }

    def _build_self_intro_reply(self) -> str:
        return build_self_intro_reply(
            model_name=getattr(self.llm_service, "chat_model", "")
        )

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

    def _supports_fast_search_fallback(self, text: str) -> bool:
        normalized = re.sub(r"\s+", "", text.lower())
        return any(hint.replace(" ", "") in normalized for hint in _FAST_FALLBACK_SEARCH_HINTS)

    def _build_fast_fallback_reply(self, *, incoming_text: str) -> str | None:
        if not self._supports_fast_search_fallback(incoming_text):
            return None

        settings = get_settings()
        if not getattr(settings, "web_search_enabled", False):
            return None

        try:
            results = self._search_web_results(query=incoming_text, max_results=3)
        except Exception:
            return None

        results = self._filter_trusted_search_results(results)

        if not results:
            return None

        lines = ["我先給你快速整理重點（精簡版）："]
        for idx, item in enumerate(results[:3], start=1):
            title = item.title.strip() or "未命名來源"
            snippet = item.snippet.strip()
            if snippet:
                snippet = snippet[:120]
                lines.append(f"{idx}. {title}：{snippet}")
            else:
                lines.append(f"{idx}. {title}")

        source_lines = [item.url.strip() for item in results[:3] if item.url.strip()]
        if source_lines:
            lines.append("來源：")
            lines.extend(f"- {url}" for url in source_lines)

        lines.append("若你要，我可以再幫你整理成一句結論 + 三點建議。")
        return "\n".join(lines)

    def _search_web_results(self, *, query: str, max_results: int) -> list[object]:
        settings = get_settings()
        service = WebSearchService.from_settings(settings)
        return service.search(query=query, max_results=max_results)

    def _is_trusted_source_url(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        if not host:
            return False
        return any(
            host == domain or host.endswith(f".{domain}")
            for domain in self.trusted_source_domains
        )

    def _looks_fresh_result(self, item: object) -> bool:
        title = (getattr(item, "title", "") or "").strip().lower()
        snippet = (getattr(item, "snippet", "") or "").strip().lower()
        url = (getattr(item, "url", "") or "").strip().lower()
        joined = f"{title} {snippet} {url}"
        if any(hint in joined for hint in _FRESHNESS_HINTS):
            return True

        current_year = str(datetime.now().year)
        return current_year in joined

    def _assess_realtime_confidence(self, trusted_results: list[object]) -> str:
        if not trusted_results:
            return "low"

        score = 0
        count = len(trusted_results[:3])
        if count >= 3:
            score += 2
        elif count >= 2:
            score += 1

        fresh_count = 0
        for item in trusted_results[:3]:
            if self._looks_fresh_result(item):
                score += 1
                fresh_count += 1

            snippet = (getattr(item, "snippet", "") or "").strip()
            if len(snippet) >= 30:
                score += 1

        if fresh_count == 0:
            return "low"
        if fresh_count >= 2 and count >= 2 and score >= 3:
            return "medium"
        if score >= 6:
            return "high"
        if score >= 4:
            return "medium"
        return "low"

    def _filter_trusted_search_results(self, results: list[object]) -> list[object]:
        trusted: list[object] = []
        for item in results:
            title = (getattr(item, "title", "") or "").strip().lower()
            snippet = (getattr(item, "snippet", "") or "").strip().lower()
            url = (getattr(item, "url", "") or "").strip().lower()
            joined = f"{title} {snippet} {url}"

            if any(keyword in joined for keyword in _SEARCH_BLOCKED_KEYWORDS):
                continue
            if any(domain in url for domain in _SEARCH_BLOCKED_DOMAINS):
                continue
            if not self._is_trusted_source_url(url):
                continue

            trusted.append(item)
        return trusted

    def _looks_realtime_sensitive_query(self, text: str) -> bool:
        normalized = re.sub(r"\s+", "", text.lower())
        return any(hint.replace(" ", "") in normalized for hint in _REALTIME_SENSITIVE_HINTS)

    def _build_grounded_realtime_reply(self, *, incoming_text: str) -> str | None:
        if not self._looks_realtime_sensitive_query(incoming_text):
            return None

        settings = get_settings()
        if not getattr(settings, "web_search_enabled", False):
            return "這題需要即時查證，但目前未連線到查詢服務，請稍後再試。"

        try:
            results = self._search_web_results(query=incoming_text, max_results=6)
        except Exception:
            return "目前即時查詢服務暫時不可用，請稍後再試。"

        trusted = self._filter_trusted_search_results(results)
        if len(trusted) < 2:
            return (
                "目前查不到足夠可信的即時來源，所以我先不直接下結論，"
                "以免誤導你。請稍後再問一次，或改問更具體的時間/地點。"
            )

        confidence = self._assess_realtime_confidence(trusted)
        self._bump_metric(f"route.realtime.confidence.{confidence}")
        if confidence == "low":
            source_urls = [
                (getattr(item, "url", "") or "").strip()
                for item in trusted[:3]
                if (getattr(item, "url", "") or "").strip()
            ]
            clarify_prompt = self._build_clarification_prompt(incoming_text)
            lines = [
                "我有找到一些來源，但新鮮度或一致性還不夠，先不直接下結論。",
                clarify_prompt,
            ]
            if source_urls:
                lines.append("目前可參考來源：")
                lines.extend(f"- {url}" for url in source_urls)
            return "\n".join(lines)

        evidence_lines: list[str] = []
        for idx, item in enumerate(trusted[:3], start=1):
            title = (getattr(item, "title", "") or "未命名來源").strip()
            snippet = (getattr(item, "snippet", "") or "").strip()[:180]
            url = (getattr(item, "url", "") or "").strip()
            evidence_lines.append(f"{idx}. {title}")
            if snippet:
                evidence_lines.append(f"   摘要: {snippet}")
            evidence_lines.append(f"   URL: {url}")

        prompt = (
            "請根據以下來源回答使用者問題，先給結論，再列 2-3 點重點。\n"
            f"使用者問題: {incoming_text}\n"
            "來源資料:\n"
            f"{'\n'.join(evidence_lines)}\n"
            "禁止加入來源中不存在的事實。"
        )

        try:
            reply = self.llm_service.generate_reply(
                system_prompt=_GROUNDING_SYSTEM_PROMPT,
                conversation=[{"role": "user", "content": prompt}],
                timeout_seconds=min(10, self.llm_service.timeout_seconds),
                max_tokens=min(420, self.llm_service.max_tokens),
            )
            answer = self._normalize_templated_reply(reply.text)
        except Exception:
            answer = "我已找到可信來源，但整理摘要暫時失敗，先附來源給你。"

        confidence_label = {"high": "高", "medium": "中"}.get(confidence, "低")
        answer = f"（信心等級：{confidence_label}）\n{answer}"

        sources = [
            (getattr(item, "url", "") or "").strip()
            for item in trusted[:3]
            if (getattr(item, "url", "") or "").strip()
        ]
        if not sources:
            return answer
        return answer + "\n\n來源：\n" + "\n".join(f"- {url}" for url in sources)

    def _build_clarification_prompt(self, text: str) -> str:
        normalized = re.sub(r"\s+", "", text.lower())
        if any(hint in normalized for hint in ("weather", "天氣", "降雨", "氣溫")):
            return _CLARIFY_WEATHER
        if any(hint in normalized for hint in ("news", "新聞", "地震", "颱風", "疫情")):
            return _CLARIFY_NEWS
        return _CLARIFY_GENERIC

    def _soften_overconfident_reply(
        self,
        *,
        text: str,
        mode: AnswerMode,
        has_sources: bool,
    ) -> str:
        if mode != AnswerMode.GENERAL or has_sources:
            return text

        lowered = text.lower()
        if not any(phrase in text or phrase in lowered for phrase in _OVERCONFIDENT_PHRASES):
            return text

        softened = text
        replacements = {
            "百分之百": "目前看起來",
            "絕對": "大多情況下",
            "一定": "通常",
            "保證": "較有機會",
            "毫無疑問": "整體而言",
            "完全沒錯": "方向上可行",
        }
        for old, new in replacements.items():
            softened = softened.replace(old, new)

        if "依目前可得資訊" not in softened:
            softened = "依目前可得資訊，" + softened

        self._bump_metric("guard.overconfidence_softened")
        return softened

    def _with_followup_hint(
        self,
        *,
        text: str,
        mode: AnswerMode,
        incoming_text: str,
        response_intent: str | None = None,
    ) -> str:
        stripped = (text or "").strip()
        if not stripped:
            return stripped

        # Keep existing CTA untouched.
        existing_markers = ("如果你要", "若你要", "你也可以", "若你願意", "要不要我")
        if any(marker in stripped for marker in existing_markers):
            return stripped

        # Avoid adding hints to fail-closed / clarification responses.
        blocked_markers = (
            "先不直接下結論",
            "需要即時查證",
            "暫時不可用",
            "目前沒有查到可用的即時天氣來源",
            "請先告訴我",
            "你可以補一個",
            "你想看哪個地區或主題",
        )
        if any(marker in stripped for marker in blocked_markers):
            return stripped

        # Keep short replies concise.
        if len(stripped) < 24:
            return stripped

        hint = _FOLLOWUP_HINTS_BY_MODE.get(mode, _FOLLOWUP_HINTS_BY_MODE[AnswerMode.GENERAL])
        if mode == AnswerMode.GENERAL:
            normalized = re.sub(r"\s+", "", incoming_text.lower())
            if response_intent not in {"compare", "plan", "recommend"}:
                return stripped
            if any(token in normalized for token in ("比較", "差異", "哪個好")):
                hint = "如果你要，我可以直接幫你做一個表格比較。"
            elif any(token in normalized for token in ("規劃", "計畫", "安排")):
                hint = "如果你要，我可以把它拆成今天就能開始的行動清單。"

        return stripped + "\n\n" + hint

    def _is_followup_turn(self, *, incoming_text: str, context: list[object]) -> bool:
        prev_user_message = ""
        for item in reversed(context):
            role = str(getattr(item, "role", "") or "")
            content = str(getattr(item, "content", "") or "").strip()
            if role == "user" and content:
                prev_user_message = content
                break

        if not prev_user_message:
            return False

        normalized = incoming_text.strip().lower()
        if any(normalized.startswith(hint) for hint in _FOLLOWUP_PREFIX_HINTS):
            if self._looks_like_topic_shift(incoming_text=incoming_text, context=context):
                self._bump_metric("route.general.topic_shift_detected")
                return False
            return True

        if len(normalized) <= 12 and any(
            token in normalized for token in ("這個", "那個", "這題", "那題")
        ):
            return True

        return False

    def _with_conversation_continuity(
        self,
        *,
        text: str,
        mode: AnswerMode,
        incoming_text: str,
        context: list[object],
    ) -> str:
        if mode != AnswerMode.GENERAL:
            return text

        stripped = (text or "").strip()
        if not stripped:
            return stripped

        if not self._is_followup_turn(incoming_text=incoming_text, context=context):
            return stripped

        if stripped.startswith(("延續你剛剛那題", "承接你剛剛那題", "延續上一題")):
            return stripped

        self._bump_metric("route.general.continuity_applied")
        return "延續你剛剛那題，" + stripped

    def _extract_previous_user_turn(self, context: list[object]) -> str:
        for item in reversed(context):
            role = str(getattr(item, "role", "") or "")
            content = str(getattr(item, "content", "") or "").strip()
            if role == "user" and content:
                return content
        return ""

    def _normalize_topic_text(self, text: str) -> str:
        normalized = re.sub(r"\s+", "", text.lower())
        for hint in sorted(_FOLLOWUP_PREFIX_HINTS, key=len, reverse=True):
            if normalized.startswith(hint):
                normalized = normalized[len(hint):]
                break

        normalized = normalized.strip("，。！？?!.:：；、")
        while normalized and normalized[-1] in _TOPIC_TRAILING_PARTICLES:
            normalized = normalized[:-1]
        return normalized

    def _extract_topic_terms(self, text: str) -> set[str]:
        normalized = self._normalize_topic_text(text)
        if not normalized:
            return set()

        terms: set[str] = set()
        for token in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", normalized):
            cleaned = token.strip()
            if not cleaned or cleaned in _TOPIC_STOP_TERMS:
                continue

            if re.fullmatch(r"[a-z0-9]+", cleaned):
                if len(cleaned) >= 2:
                    terms.add(cleaned)
                continue

            upper = min(4, len(cleaned))
            for size in range(2, upper + 1):
                for idx in range(0, len(cleaned) - size + 1):
                    candidate = cleaned[idx : idx + size]
                    if candidate not in _TOPIC_STOP_TERMS:
                        terms.add(candidate)

        return terms

    def _looks_like_topic_shift(self, *, incoming_text: str, context: list[object]) -> bool:
        previous_user_turn = self._extract_previous_user_turn(context)
        if not previous_user_turn:
            return False

        normalized_incoming = self._normalize_topic_text(incoming_text)
        if any(question in normalized_incoming for question in _GENERIC_FOLLOWUP_QUESTIONS):
            return False

        incoming_terms = self._extract_topic_terms(incoming_text)
        previous_terms = self._extract_topic_terms(previous_user_turn)
        if not incoming_terms or not previous_terms:
            return False

        if incoming_terms.issubset(_GENERIC_FOLLOWUP_ASPECTS):
            return False

        return incoming_terms.isdisjoint(previous_terms)

    def _build_intent_completed_user_turn(
        self,
        *,
        incoming_text: str,
        context: list[object],
    ) -> tuple[str, bool]:
        normalized = incoming_text.strip()
        if not normalized:
            return normalized, False

        if self._looks_like_topic_shift(incoming_text=incoming_text, context=context):
            return normalized, False

        compact = re.sub(r"\s+", "", normalized)
        is_short = len(compact) <= 12
        has_ambiguous_hint = any(hint in normalized for hint in _INTENT_AMBIGUOUS_HINTS)
        if not is_short and not has_ambiguous_hint:
            return normalized, False

        previous_user_turn = self._extract_previous_user_turn(context)
        if not previous_user_turn:
            return normalized, False

        previous_trimmed = previous_user_turn.strip()
        if len(previous_trimmed) > 80:
            previous_trimmed = previous_trimmed[:80].rstrip() + "..."

        augmented = (
            f"【補充脈絡】上一題你問：{previous_trimmed}\n"
            f"【這一題】{normalized}"
        )
        return augmented, True

    def _should_add_terse_query_guidance(
        self,
        *,
        incoming_text: str,
        mode: AnswerMode,
        context: list[object],
    ) -> bool:
        if mode != AnswerMode.GENERAL:
            return False

        compact = re.sub(r"\s+", "", incoming_text.lower())
        if not compact or len(compact) > 14:
            return False

        if self._is_followup_turn(incoming_text=incoming_text, context=context):
            return False

        if any(
            token in compact
            for token in (
                "怎麼",
                "如何",
                "為什麼",
                "原因",
                "比較",
                "差異",
                "步驟",
                "可以",
                "能否",
                "是不是",
                "嗎",
                "呢",
                "要怎麼",
            )
        ):
            return False

        return True

    def _decide_general_response_intent(self, *, incoming_text: str, context: list[object]) -> str:
        compact = re.sub(r"\s+", "", incoming_text.lower())

        if any(hint in compact for hint in _GENERAL_COMPARE_HINTS):
            return "compare"
        if any(hint in compact for hint in _GENERAL_PLAN_HINTS):
            return "plan"
        if any(hint in compact for hint in _GENERAL_RECOMMEND_HINTS):
            return "recommend"
        if any(hint in compact for hint in _GENERAL_TROUBLESHOOT_HINTS):
            return "troubleshoot"
        if any(hint in compact for hint in _GENERAL_EXPLAIN_HINTS):
            return "explain"

        if self._is_followup_turn(incoming_text=incoming_text, context=context):
            previous_user_turn = self._extract_previous_user_turn(context)
            previous_compact = re.sub(r"\s+", "", previous_user_turn.lower())
            if any(hint in previous_compact for hint in _GENERAL_COMPARE_HINTS):
                return "compare"
            if any(hint in previous_compact for hint in _GENERAL_PLAN_HINTS):
                return "plan"
            if any(hint in previous_compact for hint in _GENERAL_RECOMMEND_HINTS):
                return "recommend"
            if any(hint in previous_compact for hint in _GENERAL_TROUBLESHOOT_HINTS):
                return "troubleshoot"

        return "answer"

    def _classify_general_domain(self, *, incoming_text: str) -> str:
        compact = re.sub(r"\s+", "", incoming_text.lower())

        if any(hint.replace(" ", "") in compact for hint in _GENERAL_FINANCE_HINTS):
            return "finance"
        if any(hint.replace(" ", "") in compact for hint in _GENERAL_HEALTH_HINTS):
            return "health"
        if any(hint.replace(" ", "") in compact for hint in _GENERAL_TRAVEL_HINTS):
            return "travel"
        if any(hint.replace(" ", "") in compact for hint in _GENERAL_PRACTICAL_HINTS):
            return "practical"
        return "default"

    def _should_use_agent_loop(
        self,
        *,
        incoming_text: str,
        mode: AnswerMode,
        general_domain: str,
    ) -> bool:
        if not self.agent_enabled or not _AGENT_LOOP_AVAILABLE:
            return False
        return True

    def _build_goal_continuity_prompt(self, *, incoming_text: str, context: list[object]) -> str:
        if not self._is_followup_turn(incoming_text=incoming_text, context=context):
            return ""

        previous_user_turn = self._extract_previous_user_turn(context)
        if not previous_user_turn:
            return ""

        previous_trimmed = previous_user_turn.strip()
        if len(previous_trimmed) > 80:
            previous_trimmed = previous_trimmed[:80].rstrip() + "..."

        return (
            "\n\n[對話目標延續]\n"
            f"- 使用者目前仍在延續同一主題，上一題焦點是：{previous_trimmed}\n"
            "- 這輪請承接前文，不要把回答重置成從頭介紹。\n"
            "- 若前面已經有方向，這輪優先補下一步、差異、細節或判斷依據。\n"
        )

    def _decide_response_pacing(self, *, incoming_text: str, context: list[object]) -> str:
        compact = re.sub(r"\s+", "", incoming_text.lower())

        if any(hint in compact for hint in _PACING_STEP_BY_STEP_HINTS):
            return "step_by_step"
        if any(hint in compact for hint in _PACING_OPTIONS_HINTS):
            return "options_first"
        if any(hint in compact for hint in _PACING_CONCLUSION_HINTS):
            return "conclusion_first"

        if self._is_followup_turn(incoming_text=incoming_text, context=context):
            previous_user_turn = self._extract_previous_user_turn(context)
            previous_compact = re.sub(r"\s+", "", previous_user_turn.lower())
            if any(hint in previous_compact for hint in _PACING_STEP_BY_STEP_HINTS):
                return "step_by_step"
            if any(hint in previous_compact for hint in _PACING_OPTIONS_HINTS):
                return "options_first"
            if any(hint in previous_compact for hint in _PACING_CONCLUSION_HINTS):
                return "conclusion_first"

        return "default"

    def _estimate_query_complexity(self, text: str) -> int:
        compact = re.sub(r"\s+", "", text.lower())
        score = 0

        length = len(compact)
        if length >= 60:
            score += 3
        elif length >= 30:
            score += 2
        elif length >= 15:
            score += 1

        if any(hint in compact for hint in _DEPTH_COMPLEXITY_HINTS):
            score += 2
        if any(token in compact for token in ("以及", "並且", "同時", "另外", "如果")):
            score += 1
        if any(token in text for token in ("？", "?")) and len(compact) >= 20:
            score += 1

        return score

    def _build_adaptive_depth_prompt(self, incoming_text: str) -> tuple[str, str]:
        score = self._estimate_query_complexity(incoming_text)
        if score <= 2:
            return _DEPTH_CONCISE_PROMPT, "concise"
        if score >= 4:
            return _DEPTH_DEEP_PROMPT, "deep"
        return _DEPTH_BALANCED_PROMPT, "balanced"

    def _looks_weather_query(self, text: str) -> bool:
        return self.grounded_reply_service.looks_weather_query(text)

    def _looks_market_query(self, text: str) -> bool:
        return self.grounded_reply_service.looks_market_query(text)

    def _build_grounded_market_reply(self, *, incoming_text: str) -> str | None:
        return self.grounded_reply_service.build_grounded_market_reply(
            incoming_text=incoming_text,
            market_service=self.market_service,
        )

    def _build_grounded_weather_reply(self, *, incoming_text: str) -> str | None:
        return self.grounded_reply_service.build_grounded_weather_reply(
            incoming_text=incoming_text,
            weather_service=self.weather_service,
        )

    def _looks_vehicle_spec_query(self, text: str) -> bool:
        return self.grounded_reply_service.looks_vehicle_spec_query(text)

    def _build_grounded_vehicle_specs_reply(self, *, incoming_text: str) -> str | None:
        return self.grounded_reply_service.build_grounded_vehicle_specs_reply(
            incoming_text=incoming_text,
            search_web_results=self._search_web_results,
        )

    def _build_grounded_general_lookup_reply(self, *, incoming_text: str) -> str | None:
        return self.grounded_reply_service.build_grounded_general_lookup_reply(
            incoming_text=incoming_text,
            search_web_results=self._search_web_results,
            llm_service=self.llm_service,
            normalize_reply=self._normalize_templated_reply,
        )

    def _build_unified_knowledge_reply(
        self,
        *,
        incoming_text: str,
        mode: AnswerMode,
    ) -> str | None:
        if not self.knowledge_answer_service.should_attempt(
            incoming_text=incoming_text,
            mode=mode.value,
        ):
            return None

        result = self.knowledge_answer_service.answer(
            incoming_text=incoming_text,
            rag_enabled=self.rag_enabled,
            rag_service=self.rag_service,
            rag_top_k=self.rag_top_k,
            market_service=self.market_service,
            web_search_enabled=bool(get_settings().web_search_enabled),
            search_web_results=self._search_web_results,
            llm_service=self.llm_service,
            normalize_reply=self._normalize_templated_reply,
            confidence_label=self.source_scoring_service.confidence_label,
        )
        if result is None:
            return None

        if result.used_local:
            self._bump_metric("route.knowledge.local_first")
        if result.used_web:
            self._bump_metric("route.knowledge.web_fallback")
        return result.text

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

    def _build_system_prompt(self, *, session_id: int, incoming_text: str) -> tuple[str, list[str]]:
        source_markers: list[str] = []
        system_prompt = self.prompt_service.get_active_prompt()

        system_prompt += _RUNTIME_CAPABILITY_PROMPT
        system_prompt += _SEARCH_FIRST_PROMPT
        system_prompt += _RESPONSE_STYLE_TEMPLATE_PROMPT
        system_prompt += _RESPONSE_STYLE_DETAILED_PROMPT

        if self.session_memory_enabled and self.session_memory_repository is not None:
            memory = self.session_memory_repository.get(session_id)
            if memory is not None and memory.summary.strip():
                system_prompt += "\n\n[對話長期記憶摘要]\n" + memory.summary.strip()

        if self.session_task_repository is not None:
            open_tasks = self.session_task_repository.get_by_session(
                session_id=session_id,
                status="open",
            )
            if open_tasks:
                task_block = "\n".join(f"- {item.task_text}" for item in open_tasks[:10])
                system_prompt += f"\n\n[使用者待辦事項]\n{task_block}"

        if self.rag_enabled and self.rag_service is not None:
            references = self.rag_service.search(query=incoming_text, top_k=self.rag_top_k)
            if references:
                reference_block = "\n\n".join(
                    (
                        f"- [{Path(item.source_path).name}#{item.chunk_index}]"
                        "(信心:"
                        f"{self.source_scoring_service.confidence_label(item.score)}) "
                        f"{item.content}"
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

        general_domain = self._classify_general_domain(incoming_text=incoming_text)
        domain_prompt = _GENERAL_DOMAIN_PROMPTS.get(general_domain, "")
        if domain_prompt:
            system_prompt += domain_prompt

        return system_prompt, source_markers

    def _normalize_templated_reply(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return raw

        raw = re.sub(r"</?solution>", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(
            r"<tool_call>.*?</tool_call>",
            "",
            raw,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        raw = re.sub(
            r"<code[-_]?call>.*?</code[-_]?call>",
            "",
            raw,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        raw = re.sub(r"</?final[^>]*>", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"</?final[-_]?answer>", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"</?user_question>", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"</?user/question>", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"</?draft_answer>", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"</?revision_focus>", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"</?user-question>", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"</?draft-answer>", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"</?revision-focus>", "", raw, flags=re.IGNORECASE).strip()

        # Remove leaked internal scaffold labels from model outputs.
        raw = re.sub(r"^\s*【補充脈絡】.*$", "", raw, flags=re.MULTILINE).strip()
        raw = re.sub(r"^\s*【這一題】", "", raw, flags=re.MULTILINE).strip()
        raw = re.sub(r"^\s*【上一題】", "", raw, flags=re.MULTILINE).strip()
        raw = re.sub(r"^\s*\[\s*系統能力說明\s*\]\s*$", "", raw, flags=re.MULTILINE).strip()
        raw = re.sub(r"^\s*\[\s*回答風格模板\s*\]\s*$", "", raw, flags=re.MULTILINE).strip()
        raw = re.sub(r"^\s*\[\s*回答風格\s*:.*\]\s*$", "", raw, flags=re.MULTILINE).strip()
        raw = re.sub(
            r"^\s*\[\s*(回答任務|回答節奏|回答深度|短主題提問處理|對話目標延續|題型補強)\s*:.*\]\s*$",
            "",
            raw,
            flags=re.MULTILINE,
        ).strip()
        raw = re.sub(
            (
                r"^\s*-\s*(你是 LINE Bot|我是 LINE Bot|你可處理|我可處理|"
                r"若使用者詢問|如果使用者詢問|"
                r"僅當解析失敗|請以繁體中文作答|若資訊不足|若未查證|"
                r"若使用者已提供可用材料).*$"
            ),
            "",
            raw,
            flags=re.MULTILINE,
        ).strip()
        raw = re.sub(
            (
                r"^\s*-\s*(請使用繁體中文回覆|優先用自然語氣回答|視問題複雜度決定長度|"
                r"需要時再使用清單或小標|不要輸出固定標籤).*$"
            ),
            "",
            raw,
            flags=re.MULTILINE,
        ).strip()

        cleaned_instruction_lines: list[str] = []
        for line in raw.splitlines():
            stripped_line = line.strip()
            if any(hint in stripped_line for hint in _INSTRUCTION_LEAK_LINE_HINTS):
                continue
            if re.match(r"^在上一次的對話中", stripped_line):
                continue
            cleaned_instruction_lines.append(line)
        raw = "\n".join(cleaned_instruction_lines).strip()

        raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
        raw = re.sub(r"^\s*(?:使用者)?問題\s*[:：]\s*", "", raw).strip()

        markers = ("一句結論", "重點整理", "可執行下一步")
        if not any(marker in raw for marker in markers):
            return raw

        cleaned_lines: list[str] = []
        for line in [item.strip() for item in raw.splitlines() if item.strip()]:
            if line in {"重點整理："}:
                continue
            if line.startswith("一句結論"):
                line = line.split("：", 1)[-1].strip()
            elif line.startswith("可執行下一步"):
                line = line.split("：", 1)[-1].strip()
            elif re.match(r"^\d+[\.、)]\s*", line):
                line = re.sub(r"^\d+[\.、)]\s*", "", line).strip()

            if not line:
                continue
            if line not in cleaned_lines:
                cleaned_lines.append(line)

        return "\n".join(cleaned_lines) if cleaned_lines else raw

    def _compact_overlong_general_reply(self, *, text: str, mode: AnswerMode) -> str:
        stripped = (text or "").strip()
        if mode != AnswerMode.GENERAL or len(stripped) <= 700:
            return stripped

        lines = [line for line in stripped.splitlines() if line.strip()]
        # Remove markdown-like table lines that often bloat accidental long outputs.
        lines = [line for line in lines if "|" not in line]
        compact = "\n".join(lines).strip()

        if len(compact) <= 680:
            return compact

        kept: list[str] = []
        budget = 620
        used = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            cost = len(line) + 1
            if used + cost > budget:
                break
            kept.append(line)
            used += cost

        if not kept:
            compact = compact[:620].rstrip("，。；、 ")
        else:
            compact = "\n".join(kept)

        tail = "\n\n如果你要，我可以再幫你拆成更細的步驟。"
        merged = (compact + tail).strip()
        if len(merged) > 700:
            merged = merged[:680].rstrip("，。；、 ") + "…"
        return merged

    def _strip_question_echo(self, *, text: str, incoming_text: str) -> str:
        stripped = (text or "").strip()
        if not stripped:
            return stripped

        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if len(lines) < 2:
            return stripped

        normalized_question = re.sub(r"\s+", "", incoming_text).strip("？?！!。")
        normalized_first_line = re.sub(r"\s+", "", lines[0]).strip("？?！!。")

        if normalized_first_line in {"問題", "使用者問題"} and len(lines) >= 2:
            normalized_second_line = re.sub(r"\s+", "", lines[1]).strip("？?！!。")
            if normalized_second_line == normalized_question:
                return "\n".join(lines[2:]).strip()

        if normalized_question and normalized_first_line == normalized_question:
            return "\n".join(lines[1:]).strip()

        return stripped

    def _should_run_guard(self, *, incoming_text: str, draft_answer: str) -> bool:
        if self.response_guard_service is None:
            return False
        return self.response_guard_service.should_review(
            question=incoming_text,
            draft_answer=draft_answer,
        )

    def _handle_task_command(self, *, session_id: int, text: str) -> str | None:
        if self.session_task_repository is None or self.task_memory_service is None:
            return None

        parsed = self.task_memory_service.parse_command(text)
        if parsed is None:
            return None

        action, idx = parsed
        open_tasks = self.session_task_repository.get_by_session(
            session_id=session_id,
            status="open",
        )

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

    def _build_capability_inquiry_reply(self, *, incoming_text: str) -> str:
        return build_capability_inquiry_reply(incoming_text=incoming_text)

    def handle_user_message(
        self,
        *,
        line_user_id: str,
        text: str,
        schedule_background_task: Callable[..., object] | None = None,
    ) -> str:
        incoming_text = text.strip()
        if not incoming_text:
            return "請輸入文字訊息，我才能協助你。"

        intent_decision = decide_intent(incoming_text)
        self._bump_metric(f"route.intent_gate.{intent_decision.kind.value}")

        if intent_decision.block_coding:
            self._bump_metric("guard.coding_blocked")
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

        if intent_decision.is_capability_inquiry:
            self._bump_metric("route.capability_inquiry")
            capability_reply = self._build_capability_inquiry_reply(
                incoming_text=incoming_text
            )
            self.message_repository.add_message(
                session_id=session.id,
                role="assistant",
                content=capability_reply,
                source="line",
            )
            self.session_service.mark_activity(session.id)
            return capability_reply

        if intent_decision.is_self_intro:
            self._bump_metric("route.general.self_intro")
            self_intro_reply = self._build_self_intro_reply()
            self.message_repository.add_message(
                session_id=session.id,
                role="assistant",
                content=self_intro_reply,
                source="line",
            )
            self.session_service.mark_activity(session.id)
            return self_intro_reply

        mode = intent_decision.mode
        self._bump_metric(f"mode.{mode.value}")

        if self.agent_enabled and mode in {AnswerMode.WEATHER, AnswerMode.MARKET}:
            grounded_reply = None
            if mode == AnswerMode.WEATHER:
                grounded_reply = self._build_grounded_weather_reply(incoming_text=incoming_text)
                if grounded_reply is not None:
                    self._bump_metric("route.weather.grounded")
            elif mode == AnswerMode.MARKET:
                grounded_reply = self._build_grounded_market_reply(incoming_text=incoming_text)
                if grounded_reply is not None:
                    self._bump_metric("route.market.grounded")

            if grounded_reply is not None:
                self.message_repository.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=grounded_reply,
                    source="line",
                )
                self.session_service.mark_activity(session.id)
                return grounded_reply

        if self.agent_enabled and mode == AnswerMode.REALTIME_SENSITIVE:
            grounded_realtime_reply = self._build_grounded_realtime_reply(
                incoming_text=incoming_text
            )
            if grounded_realtime_reply is not None:
                self._bump_metric("route.realtime.grounded")
                self.message_repository.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=grounded_realtime_reply,
                    source="line",
                )
                self.session_service.mark_activity(session.id)
                return grounded_realtime_reply

        if self.agent_enabled and mode == AnswerMode.GENERAL:
            vehicle_specs_reply = self._build_grounded_vehicle_specs_reply(
                incoming_text=incoming_text
            )
            if vehicle_specs_reply is not None:
                self._bump_metric("route.general.vehicle_specs_grounded")
                self.message_repository.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=vehicle_specs_reply,
                    source="line",
                )
                self.session_service.mark_activity(session.id)
                return vehicle_specs_reply

            unified_knowledge_reply = self._build_unified_knowledge_reply(
                incoming_text=incoming_text,
                mode=mode,
            )
            if unified_knowledge_reply is not None:
                self.message_repository.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=unified_knowledge_reply,
                    source="line",
                )
                self.session_service.mark_activity(session.id)
                return unified_knowledge_reply

            grounded_general_reply = self._build_grounded_general_lookup_reply(
                incoming_text=incoming_text
            )
            if grounded_general_reply is not None:
                self._bump_metric("route.general.lookup_grounded")
                self.message_repository.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=grounded_general_reply,
                    source="line",
                )
                self.session_service.mark_activity(session.id)
                return grounded_general_reply

        if self.agent_enabled and mode == AnswerMode.MARKET:
            unified_knowledge_reply = self._build_unified_knowledge_reply(
                incoming_text=incoming_text,
                mode=mode,
            )
            if unified_knowledge_reply is not None:
                self.message_repository.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=unified_knowledge_reply,
                    source="line",
                )
                self.session_service.mark_activity(session.id)
                return unified_knowledge_reply

        topic_shift = self._looks_like_topic_shift(incoming_text=incoming_text, context=context)
        conversation = (
            []
            if topic_shift
            else [
                {"role": message.role, "content": message.content}
                for message in context
                if message.role in {"user", "assistant"}
            ]
        )
        if topic_shift:
            self._bump_metric("route.general.topic_shift_hard_reset")
        user_turn_for_model, intent_augmented = self._build_intent_completed_user_turn(
            incoming_text=incoming_text,
            context=context,
        )
        conversation.append({"role": "user", "content": user_turn_for_model})
        conversation = self._truncate_conversation(conversation)
        system_prompt, source_markers = self._build_system_prompt(
            session_id=session.id,
            incoming_text=incoming_text,
        )
        general_domain = self._classify_general_domain(incoming_text=incoming_text)
        response_intent = self._decide_general_response_intent(
            incoming_text=incoming_text,
            context=context,
        )
        system_prompt += _GENERAL_INTENT_PROMPTS[response_intent]
        self._bump_metric(f"route.general.response_intent.{response_intent}")

        response_pacing = self._decide_response_pacing(
            incoming_text=incoming_text,
            context=context,
        )
        pacing_prompt = _RESPONSE_PACING_PROMPTS[response_pacing]
        if pacing_prompt:
            system_prompt += pacing_prompt
            self._bump_metric(f"route.general.response_pacing.{response_pacing}")

        goal_continuity_prompt = self._build_goal_continuity_prompt(
            incoming_text=incoming_text,
            context=context,
        )
        if goal_continuity_prompt:
            system_prompt += goal_continuity_prompt
            self._bump_metric("route.general.goal_continuity_guided")

        depth_prompt, depth_label = self._build_adaptive_depth_prompt(incoming_text)
        system_prompt += depth_prompt
        self._bump_metric(f"route.general.depth.{depth_label}")
        if intent_augmented:
            self._bump_metric("route.general.intent_augmented")
        elif self._should_add_terse_query_guidance(
            incoming_text=incoming_text,
            mode=mode,
            context=context,
        ):
            system_prompt += _TERSE_QUERY_GUIDANCE_PROMPT
            self._bump_metric("route.general.terse_query_guided")

        try:
            if self._should_use_agent_loop(
                incoming_text=incoming_text,
                mode=mode,
                general_domain=general_domain,
            ):
                loop_result = run_agent_loop(
                    llm_service=self.llm_service,
                    system_prompt=system_prompt,
                    conversation=conversation,
                    fast_mode=_AGENT_FAST_MODE,
                    auto_search_enabled=_AGENT_AUTO_SEARCH,
                    max_tool_rounds=_AGENT_MAX_TOOL_ROUNDS,
                    raw_user_query=incoming_text,
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

            if self._should_run_guard(incoming_text=incoming_text, draft_answer=reply.text):
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
            fallback = self._build_fast_fallback_reply(incoming_text=incoming_text)
            if fallback is not None:
                return fallback
            return "目前回應較慢，請稍後再試一次。"
        except LLMServiceError as exc:
            # Some LM Studio model templates intermittently return
            # "No user query found in messages" on complex multi-turn payloads.
            # Retry once with a minimal user-only conversation before failing.
            if "No user query found in messages" in str(exc):
                try:
                    retry_reply = self.llm_service.generate_reply(
                        system_prompt="請使用繁體中文直接回答，先給結論，再給 2-3 個重點。",
                        conversation=[{"role": "user", "content": incoming_text}],
                        timeout_seconds=min(8, self.llm_service.timeout_seconds),
                        max_tokens=min(320, self.llm_service.max_tokens),
                    )
                    self.message_repository.add_message(
                        session_id=session.id,
                        role="assistant",
                        content=self._normalize_templated_reply(retry_reply.text),
                        source="line",
                        token_count=retry_reply.total_tokens,
                    )
                    self.llm_log_repository.add_log(
                        request_id=request_id,
                        session_id=session.id,
                        model_name=retry_reply.model_name,
                        latency_ms=retry_reply.latency_ms,
                        prompt_tokens=retry_reply.prompt_tokens,
                        completion_tokens=retry_reply.completion_tokens,
                        total_tokens=retry_reply.total_tokens,
                        status="success",
                        error_message="Recovered from template error via minimal retry",
                    )
                    self.session_service.mark_activity(session.id)
                    return self._normalize_templated_reply(retry_reply.text)
                except Exception:
                    pass

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

            fallback = self._build_fast_fallback_reply(incoming_text=incoming_text)
            if fallback is not None:
                return fallback

            return "目前暫時無法產生回覆，請稍後再試。"

        normalized_reply_text = self._normalize_templated_reply(reply.text)
        normalized_reply_text = self._strip_question_echo(
            text=normalized_reply_text,
            incoming_text=incoming_text,
        )
        normalized_reply_text = self._soften_overconfident_reply(
            text=normalized_reply_text,
            mode=mode,
            has_sources=bool(source_markers),
        )
        normalized_reply_text = self._with_conversation_continuity(
            text=normalized_reply_text,
            mode=mode,
            incoming_text=incoming_text,
            context=context,
        )
        normalized_reply_text = self._compact_overlong_general_reply(
            text=normalized_reply_text,
            mode=mode,
        )
        normalized_reply_text = self._with_followup_hint(
            text=normalized_reply_text,
            mode=mode,
            incoming_text=incoming_text,
            response_intent=response_intent,
        )
        normalized_reply_text = self._compact_overlong_general_reply(
            text=normalized_reply_text,
            mode=mode,
        )
        if mode == AnswerMode.GENERAL and not normalized_reply_text.strip():
            normalized_reply_text = (
                "我先給你精簡建議：先從一個最可行的小步驟開始，"
                "如果你願意，我可以再依你的情況細化成可執行清單。"
            )

        self.message_repository.add_message(
            session_id=session.id,
            role="assistant",
            content=(
                normalized_reply_text
                if not source_markers
                else (
                    f"{normalized_reply_text}\n\n參考來源："
                    f"{', '.join(dict.fromkeys(source_markers))}"
                )
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
        if schedule_background_task is not None:
            schedule_background_task(self._try_update_session_memory, session_id=session.id)
        else:
            self._try_update_session_memory(session_id=session.id)
        if self.session_task_repository is not None and self.task_memory_service is not None:
            for task in self.task_memory_service.extract_tasks(incoming_text):
                self.session_task_repository.add_task(session_id=session.id, task_text=task)
        if source_markers:
            return (
                f"{normalized_reply_text}\n\n參考來源："
                f"{', '.join(dict.fromkeys(source_markers))}"
            )
        return normalized_reply_text
