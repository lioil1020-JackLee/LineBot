from __future__ import annotations

import re
from collections.abc import Callable

from ..config import get_settings
from ..tools.fetch_url import fetch_url
from .market_service import MarketService
from .weather_service import WeatherService
from .web_search_service import WebSearchService

_WEATHER_QUERY_HINTS = (
    "天氣",
    "氣溫",
    "降雨",
    "會下雨",
    "weather",
    "forecast",
)

_WEATHER_CONDITION_HINTS = (
    "晴",
    "多雲",
    "陰",
    "陣雨",
    "雷雨",
    "雨",
)

_WEATHER_QUERY_REFINEMENTS = (
    "即時天氣 溫度 降雨機率",
    "每小時天氣 溫度 風速",
    "鄉鎮預報 逐3小時",
)

_MARKET_QUERY_HINTS = (
    "股價",
    "股市",
    "股票",
    "台股",
    "盤勢",
    "類股",
    "加權指數",
    "大盤",
    "收盤",
    "盤中",
    "twii",
    "twse",
    "stock",
    "price",
    "quote",
)

_GENERAL_LOOKUP_HINTS = (
    "是什麼",
    "什麼是",
    "誰是",
    "誰創辦",
    "哪一年",
    "哪個國家",
    "哪裡",
    "多少",
    "介紹",
    "背景",
    "歷史",
    "用途",
    "功能",
    "原理",
    "規則",
    "差別",
    "差異",
    "比較",
    "評價",
    "review",
    "history",
    "difference",
    "what is",
    "who is",
)

_GENERAL_LOOKUP_BLOCK_HINTS = (
    "你好",
    "哈囉",
    "謝謝",
    "早安",
    "晚安",
    "掰掰",
    "再見",
    "你在嗎",
    "你覺得",
    "我想",
    "我最近",
    "我失眠",
    "我失戀",
    "陪我",
)

_GENERAL_LOOKUP_BLOCKED_DOMAINS = (
    "zhihu.com",
)


class GroundedReplyService:
    def _is_generic_lookup_source_allowed(self, url: str) -> bool:
        host = url.split("://", 1)[-1].split("/", 1)[0].lower()
        if not host:
            return False
        return not any(
            host == domain or host.endswith(f".{domain}")
            for domain in _GENERAL_LOOKUP_BLOCKED_DOMAINS
        )

    def looks_general_lookup_query(self, text: str) -> bool:
        normalized = " ".join(text.lower().split())
        compact = normalized.replace(" ", "")
        if len(compact) < 4:
            return False
        if any(
            hint in normalized or hint.replace(" ", "") in compact
            for hint in _GENERAL_LOOKUP_BLOCK_HINTS
        ):
            return False
        return any(
            hint in normalized or hint.replace(" ", "") in compact
            for hint in _GENERAL_LOOKUP_HINTS
        )

    def looks_weather_query(self, text: str) -> bool:
        normalized = re.sub(r"\s+", "", text.lower())
        return any(hint.replace(" ", "") in normalized for hint in _WEATHER_QUERY_HINTS)

    def looks_market_query(self, text: str) -> bool:
        normalized = re.sub(r"\s+", "", text.lower())
        return any(hint.replace(" ", "") in normalized for hint in _MARKET_QUERY_HINTS)

    def looks_vehicle_spec_query(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text.lower())
        has_spec_hint = any(
            token in normalized for token in ("規格", "性能", "馬力", "扭力", "spec", "specs")
        )
        has_vehicle_hint = any(
            token in normalized for token in ("vw", "volkswagen", "t-roc", "車款", "汽車")
        )
        return has_spec_hint and has_vehicle_hint

    def build_grounded_market_reply(
        self,
        *,
        incoming_text: str,
        market_service: MarketService,
    ) -> str | None:
        if not self.looks_market_query(incoming_text):
            return None

        lines: list[str] = []
        stock = market_service.query_taiwan_stock_by_query(incoming_text)
        if stock is not None and stock.symbol.lower() != "t00":
            line = f"{stock.display_name}（{stock.symbol}）目前 {stock.price:,.2f} 元"
            if stock.change is not None and stock.change_percent is not None:
                line += f"，{stock.change:+,.2f}（{stock.change_percent:+.2f}%）"
            lines.append(line)

        if any(token in incoming_text.lower() for token in ("台股", "大盤", "加權指數", "twii")):
            snapshot = market_service.query_taiwan_weighted_index()
            if snapshot is not None:
                line = f"台股加權指數目前 {snapshot.price:,.2f} 點"
                if snapshot.change is not None and snapshot.change_percent is not None:
                    line += f"，{snapshot.change:+,.2f}（{snapshot.change_percent:+.2f}%）"
                lines.append(line)

        if lines:
            lines.append("資料來源：TWSE MIS API")
            lines.append("如果你要，我可以再補成交量、盤中高低點或同族群個股。")
            return "\n".join(lines)

        snapshot = market_service.query_taiwan_weighted_index()
        if snapshot is not None:
            lines = [f"目前台灣加權股價指數約 {snapshot.price:,.2f} 點。"]
            if snapshot.change is not None and snapshot.change_percent is not None:
                direction = "上漲" if snapshot.change >= 0 else "下跌"
                lines.append(
                    "相較前一交易時段"
                    f"{direction} {abs(snapshot.change):,.2f} 點"
                    f"（{abs(snapshot.change_percent):.2f}%）。"
                )
            lines.append(f"資料來源：{snapshot.source}")
            lines.append("若你要，我可以再補『今天盤勢重點（電子/金融/航運）』。")
            return "\n".join(lines)

        return None

    def build_grounded_weather_reply(
        self,
        *,
        incoming_text: str,
        weather_service: WeatherService,
    ) -> str | None:
        if not self.looks_weather_query(incoming_text):
            return None

        snapshot = weather_service.query_today(incoming_text)
        if snapshot is not None:
            lines: list[str] = [snapshot.summary]
            lines.append("資料來源：Open-Meteo 即時預報（每小時）")
            lines.append("若你要，我可以接著補『出門時段建議（上午/下午/晚上）』。")
            return "\n".join(lines)

        settings = get_settings()
        if not getattr(settings, "web_search_enabled", False):
            return None

        try:
            search_service = WebSearchService.from_settings(settings)
            results = self.search_weather_with_refinement(
                service=search_service,
                incoming_text=incoming_text,
            )
        except Exception:
            return None

        if not results:
            return "我目前沒有查到可用的即時天氣來源，建議先看中央氣象署最新資料。"

        top_results = results[:3]
        weather_summary = self.summarize_weather_from_results(top_results)

        lines: list[str] = []
        if weather_summary:
            lines.append(f"目前查到的天氣重點：{weather_summary}")
        else:
            lines.append("目前可查到相關來源，但摘要內缺少可直接引用的即時數值。")

        lines.append("可參考來源：")
        for idx, item in enumerate(top_results, start=1):
            title = item.title.strip() or "未命名來源"
            url = item.url.strip()
            if url:
                lines.append(f"{idx}. {title} - {url}")
            else:
                lines.append(f"{idx}. {title}")

        lines.append("提醒：以上為搜尋摘要整理，若要最準確數值請以中央氣象署頁面為準。")
        return "\n".join(lines)

    def build_grounded_vehicle_specs_reply(
        self,
        *,
        incoming_text: str,
        search_web_results: Callable[..., list[object]],
    ) -> str | None:
        if not self.looks_vehicle_spec_query(incoming_text):
            return None

        settings = get_settings()
        if not getattr(settings, "web_search_enabled", False):
            return None

        try:
            results = search_web_results(query=incoming_text, max_results=3)
        except Exception:
            return None

        if not results:
            return None

        top = results[0]
        title = top.title.strip() or "車款規格頁"
        snippet = top.snippet.strip()
        url = top.url.strip()

        lines = [f"目前查到較接近的車款規格資料：{title}"]
        if snippet:
            lines.append(snippet)
        if url:
            lines.append(f"來源：{url}")
        lines.append("如果你要，我可以再幫你整理成『動力 / 驅動 / 加速』三點摘要。")
        return "\n".join(lines)

    def build_grounded_general_lookup_reply(
        self,
        *,
        incoming_text: str,
        search_web_results: Callable[..., list[object]],
        llm_service: object,
        normalize_reply: Callable[[str], str],
    ) -> str | None:
        if not self.looks_general_lookup_query(incoming_text):
            return None

        settings = get_settings()
        if not getattr(settings, "web_search_enabled", False):
            return None

        try:
            results = search_web_results(query=incoming_text, max_results=5)
        except Exception:
            return None

        usable_results = [
            item
            for item in results
            if (getattr(item, "title", "") or "").strip()
            and (getattr(item, "url", "") or "").strip()
            and self._is_generic_lookup_source_allowed((getattr(item, "url", "") or "").strip())
        ]
        if not usable_results:
            fallback = self._build_general_knowledge_fallback_reply(
                incoming_text=incoming_text,
                llm_service=llm_service,
                normalize_reply=normalize_reply,
            )
            if fallback:
                return (
                    f"{fallback}\n\n"
                    "註：這題目前沒有抓到理想的外部來源，所以先以一般知識整理回答。"
                )
            if results:
                top = results[0]
                title = (getattr(top, "title", "") or "未命名來源").strip()
                snippet = (getattr(top, "snippet", "") or "").strip()
                url = (getattr(top, "url", "") or "").strip()
                lines = [
                    "我目前先查到的多半是社群或討論型內容，可信度不如官方文件，"
                    "所以先把較接近的描述整理給你。",
                    f"線索來源：{title}",
                ]
                if snippet:
                    lines.append(snippet)
                if url:
                    lines.append(f"參考來源：{url}")
                lines.append("如果你要，我可以再改用更具體的關鍵字繼續查官方資料。")
                return "\n".join(lines)
            return None

        evidence_lines: list[str] = []
        source_urls: list[str] = []
        for idx, item in enumerate(usable_results[:3], start=1):
            title = (getattr(item, "title", "") or "未命名來源").strip()
            snippet = (getattr(item, "snippet", "") or "").strip()[:220]
            url = (getattr(item, "url", "") or "").strip()
            evidence_lines.append(f"{idx}. {title}")
            if snippet:
                evidence_lines.append(f"   摘要：{snippet}")
            evidence_lines.append(f"   URL: {url}")
            source_urls.append(url)

        prompt = (
            "請根據下列搜尋摘要回答使用者問題。\n"
            "規則：\n"
            "- 只能使用提供的資料，不要自行補完未出現的事實。\n"
            "- 先直接回答，再補 2-3 句重點整理。\n"
            "- 如果資料不足，要明確說明不足。\n"
            "- 使用繁體中文。\n\n"
            f"問題：{incoming_text}\n\n"
            "搜尋摘要：\n"
            f"{chr(10).join(evidence_lines)}"
        )

        try:
            reply = llm_service.generate_reply(
                system_prompt=(
                    "你是網路資料整理助理。"
                    "只能根據提供的搜尋摘要回答，不可憑空補充。"
                    "若資料不足，請直接說不足。"
                ),
                conversation=[{"role": "user", "content": prompt}],
                timeout_seconds=min(10, getattr(llm_service, "timeout_seconds", 10)),
                max_tokens=min(420, getattr(llm_service, "max_tokens", 420)),
            )
            answer = normalize_reply(getattr(reply, "text", "") or "").strip()
        except Exception:
            answer = ""

        if not answer:
            top = usable_results[0]
            title = (getattr(top, "title", "") or "未命名來源").strip()
            snippet = (getattr(top, "snippet", "") or "").strip()
            answer = f"我先查到較接近的資料是「{title}」。"
            if snippet:
                answer += f"\n{snippet}"

        return answer + "\n\n來源：\n" + "\n".join(f"- {url}" for url in source_urls)

    def _build_general_knowledge_fallback_reply(
        self,
        *,
        incoming_text: str,
        llm_service: object,
        normalize_reply: Callable[[str], str],
    ) -> str:
        try:
            reply = llm_service.generate_reply(
                system_prompt=(
                    "你是知識整理助理。"
                    "請直接用繁體中文回答使用者的概念型問題。"
                    "如果不確定，請明確說明不確定。"
                    "回答控制在 2-4 句。"
                ),
                conversation=[{"role": "user", "content": incoming_text}],
                timeout_seconds=min(8, getattr(llm_service, "timeout_seconds", 8)),
                max_tokens=min(260, getattr(llm_service, "max_tokens", 260)),
            )
        except Exception:
            return ""

        return normalize_reply(getattr(reply, "text", "") or "").strip()

    def search_weather_with_refinement(
        self,
        *,
        service: WebSearchService,
        incoming_text: str,
    ) -> list[object]:
        base_query = incoming_text.strip()
        if not base_query:
            return []

        queries = [base_query]
        for suffix in _WEATHER_QUERY_REFINEMENTS:
            queries.append(f"{base_query} {suffix}")

        collected: list[object] = []
        seen: set[tuple[str, str]] = set()

        for query in queries[:3]:
            candidates = service.search(query=query, max_results=6)
            for item in candidates:
                key = (item.title.strip(), item.url.strip())
                if key in seen:
                    continue
                seen.add(key)
                collected.append(item)

            if self.weather_results_sufficient(collected, incoming_text=incoming_text):
                break

        return collected

    def weather_results_sufficient(self, results: list[object], *, incoming_text: str) -> bool:
        if not results:
            return False

        need_today = "今天" in incoming_text
        best_score = 0
        for item in results[:6]:
            text = (
                f"{getattr(item, 'title', '')} "
                f"{getattr(item, 'snippet', '')} "
                f"{getattr(item, 'url', '')}"
            ).lower()
            score = 0
            if any(token in text for token in ("淡水", "tamsui")):
                score += 2
            if any(token in text for token in ("天氣", "weather", "forecast", "降雨", "氣溫")):
                score += 2
            if re.search(r"(-?\d{1,2})\s*(?:°\s*[Cc]|℃|度)", text):
                score += 2
            if re.search(r"\d{1,3}\s*%", text):
                score += 1
            if any(site in text for site in ("cwa.gov.tw", "accuweather.com", "weather.com")):
                score += 1
            if need_today and any(
                token in text
                for token in ("今日", "今天", "hourly", "每小時", "逐3小時")
            ):
                score += 1
            best_score = max(best_score, score)

        return best_score >= 5

    def summarize_weather_from_results(self, results: list[object]) -> str:
        snippets: list[str] = []
        for item in results:
            title = getattr(item, "title", "") or ""
            snippet = getattr(item, "snippet", "") or ""
            snippets.append(f"{title} {snippet}".strip())

        if results and all(len(item) < 80 for item in snippets if item):
            top_url = (getattr(results[0], "url", "") or "").strip()
            if top_url:
                fetched = fetch_url(top_url)
                invalid_prefixes = (
                    "HTTP ",
                    "抓取逾時",
                    "無效網址",
                    "不支援的 Content-Type",
                )
                if fetched and not fetched.startswith(invalid_prefixes):
                    snippets.append(fetched[:1200])

        text = " ".join(snippets)
        if not text:
            return ""

        conditions = [cond for cond in _WEATHER_CONDITION_HINTS if cond in text]
        condition_text = "、".join(dict.fromkeys(conditions[:2]))

        temp_matches = re.findall(r"(-?\d{1,2})\s*(?:°\s*[Cc]|℃|度)", text)
        temps = [int(value) for value in temp_matches if value.lstrip("-").isdigit()]

        rain_matches = re.findall(r"(\d{1,3})\s*%", text)
        rain_values = [
            int(value) for value in rain_matches if value.isdigit() and 0 <= int(value) <= 100
        ]

        parts: list[str] = []
        if condition_text:
            parts.append(f"天況關鍵字偏向「{condition_text}」")
        if temps:
            parts.append(f"可見溫度資訊約 {min(temps)}-{max(temps)}°C")
        if rain_values:
            parts.append(f"可見降雨機率約 {min(rain_values)}-{max(rain_values)}%")

        if not temps:
            parts.append("目前摘要未抽到明確溫度數值")

        return "；".join(parts)
