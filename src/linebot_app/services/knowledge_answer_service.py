from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..tools.fetch_url import fetch_url


@dataclass(frozen=True)
class KnowledgeAnswerResult:
    text: str
    used_local: bool
    used_web: bool
    source_markers: list[str]


_CHITCHAT_HINTS = (
    "你好",
    "哈囉",
    "謝謝",
    "晚安",
    "早安",
    "掰掰",
    "再見",
    "你在嗎",
    "陪我",
)

_QUESTION_HINTS = (
    "是什麼",
    "什麼是",
    "多少",
    "價格",
    "票價",
    "費用",
    "重點",
    "介紹",
    "背景",
    "歷史",
    "差別",
    "差異",
    "比較",
    "規格",
    "班次",
    "時間",
    "怎麼去",
    "怎麼搭",
    "誰是",
    "哪裡",
    "哪個",
    "如何",
    "為什麼",
    "嗎",
    "？",
    "?",
    "what is",
    "who is",
    "price",
    "fare",
    "cost",
    "schedule",
)

_THSR_FARE_URL = "https://jp.thsrc.com.tw/ArticleContent/33f25094-3ba1-4c36-8a1c-981080efa422"
_THSR_STATION_ALIASES = {
    "南港": "南港",
    "台北": "台北",
    "臺北": "台北",
    "板橋": "板橋",
    "桃園": "桃園",
    "新竹": "新竹",
    "苗栗": "苗栗",
    "台中": "台中",
    "臺中": "台中",
    "彰化": "彰化",
    "雲林": "雲林",
    "嘉義": "嘉義",
    "台南": "台南",
    "臺南": "台南",
    "左營": "左營",
}

_LOW_TRUST_WEB_DOMAINS = (
    "zhihu.com",
    "baidu.com",
    "discuss.com.hk",
    "gmail.com",
    "google.com",
)


class KnowledgeAnswerService:
    def _is_allowed_web_source(self, url: str) -> bool:
        host = url.split("://", 1)[-1].split("/", 1)[0].lower()
        if not host:
            return False
        return not any(
            host == domain or host.endswith(f".{domain}")
            for domain in _LOW_TRUST_WEB_DOMAINS
        )

    def should_attempt(self, *, incoming_text: str, mode: str) -> bool:
        normalized = " ".join((incoming_text or "").lower().split())
        compact = normalized.replace(" ", "")
        if not compact or len(compact) < 4:
            return False
        if any(hint in normalized or hint in compact for hint in _CHITCHAT_HINTS):
            return False
        if mode != "general":
            return True
        return (
            any(hint in normalized or hint in compact for hint in _QUESTION_HINTS)
            or len(compact) >= 8
        )

    def answer(
        self,
        *,
        incoming_text: str,
        rag_enabled: bool,
        rag_service: Any | None,
        rag_top_k: int,
        market_service: Any | None,
        web_search_enabled: bool,
        search_web_results,
        llm_service: Any,
        normalize_reply,
        confidence_label,
    ) -> KnowledgeAnswerResult | None:
        local_references = []
        if rag_enabled and rag_service is not None:
            try:
                local_references = rag_service.search(query=incoming_text, top_k=rag_top_k)
            except Exception:
                local_references = []

        strong_local = [
            item
            for item in local_references
            if getattr(item, "score", 0.0) >= 0.45 and (getattr(item, "content", "") or "").strip()
        ]

        structured_result = None
        if not strong_local:
            structured_result = self._try_structured_provider(
                incoming_text=incoming_text,
                market_service=market_service,
            )
        if structured_result is not None:
            return structured_result

        web_results = []
        if not strong_local and web_search_enabled:
            try:
                web_results = [
                    item
                    for item in search_web_results(query=incoming_text, max_results=5)
                    if self._is_allowed_web_source((getattr(item, "url", "") or "").strip())
                ]
            except Exception:
                web_results = []

        if not strong_local and not web_results:
            fallback = self._build_general_knowledge_fallback_reply(
                incoming_text=incoming_text,
                llm_service=llm_service,
                normalize_reply=normalize_reply,
            )
            if not fallback:
                return None
            return KnowledgeAnswerResult(
                text=(
                    f"{fallback}\n\n"
                    "註：這題目前沒有抓到理想的外部來源，所以先以一般知識整理回答。"
                ),
                used_local=False,
                used_web=False,
                source_markers=[],
            )

        prompt, source_markers = self._build_prompt(
            incoming_text=incoming_text,
            local_references=strong_local,
            web_results=web_results,
            confidence_label=confidence_label,
        )

        answer = ""
        try:
            reply = llm_service.generate_reply(
                system_prompt=(
                    "你是知識整合助理。"
                    "先使用本地知識庫內容；若本地資料不足，再使用提供的網路來源。"
                    "不可憑空補充未出現的事實。"
                    "回答要直接、精簡、用繁體中文。"
                    "若資料不足，必須明說不足。"
                ),
                conversation=[{"role": "user", "content": prompt}],
                timeout_seconds=min(10, getattr(llm_service, "timeout_seconds", 10)),
                max_tokens=min(500, getattr(llm_service, "max_tokens", 500)),
            )
            answer = normalize_reply(getattr(reply, "text", "") or "").strip()
        except Exception:
            answer = ""

        if not answer:
            answer = self._build_fallback_answer(
                local_references=strong_local,
                web_results=web_results,
            )

        if not answer:
            return None

        if source_markers:
            answer += "\n\n來源：\n" + "\n".join(f"- {marker}" for marker in source_markers)

        return KnowledgeAnswerResult(
            text=answer,
            used_local=bool(strong_local),
            used_web=bool(web_results),
            source_markers=source_markers,
        )

    def _build_prompt(
        self,
        *,
        incoming_text: str,
        local_references: list[object],
        web_results: list[object],
        confidence_label,
    ) -> tuple[str, list[str]]:
        sections: list[str] = [f"問題：{incoming_text}"]
        source_markers: list[str] = []

        if local_references:
            local_lines: list[str] = []
            for item in local_references[:4]:
                marker = (
                    f"{Path(str(getattr(item, 'source_path', 'local'))).name}"
                    f"#{getattr(item, 'chunk_index', 0)}"
                    f"(信心:{confidence_label(getattr(item, 'score', 0.0))})"
                )
                source_markers.append(marker)
                local_lines.append(f"- {marker} {getattr(item, 'content', '')}")
            sections.append("[本地知識庫]\n" + "\n".join(local_lines))

        if web_results:
            web_lines: list[str] = []
            for item in web_results[:4]:
                title = (getattr(item, "title", "") or "未命名來源").strip()
                url = (getattr(item, "url", "") or "").strip()
                snippet = (getattr(item, "snippet", "") or "").strip()
                source_markers.append(url or title)
                web_lines.append(f"- {title}")
                if snippet:
                    web_lines.append(f"  摘要：{snippet}")
                if url:
                    web_lines.append(f"  URL: {url}")
            sections.append("[網路來源]\n" + "\n".join(web_lines))

        sections.append(
            "回答規則：\n"
            "- 優先使用本地知識庫。\n"
            "- 本地知識不足時，再補充網路來源。\n"
            "- 先直接回答，再補 2-4 句重點。\n"
            "- 如果只有部分資訊，就說明目前能確認的部分。"
        )
        return "\n\n".join(sections), list(dict.fromkeys(source_markers))

    def _build_fallback_answer(
        self,
        *,
        local_references: list[object],
        web_results: list[object],
    ) -> str:
        if local_references:
            top = local_references[0]
            source_name = Path(str(getattr(top, "source_path", "local"))).name
            content = (getattr(top, "content", "") or "").strip()
            if content:
                return (
                    f"我先從本地知識庫找到較接近的內容，來源是 {source_name}。\n"
                    f"{content[:320]}"
                )

        if web_results:
            top = web_results[0]
            title = (getattr(top, "title", "") or "未命名來源").strip()
            snippet = (getattr(top, "snippet", "") or "").strip()
            lines = [f"我先查到較接近的外部資料是「{title}」。"]
            if snippet:
                lines.append(snippet[:320])
            return "\n".join(lines)

        return ""

    def _build_general_knowledge_fallback_reply(
        self,
        *,
        incoming_text: str,
        llm_service: Any,
        normalize_reply,
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

    def _try_structured_provider(
        self,
        *,
        incoming_text: str,
        market_service: Any | None,
    ) -> KnowledgeAnswerResult | None:
        market_result = self._try_market_summary_provider(
            incoming_text=incoming_text,
            market_service=market_service,
        )
        if market_result is not None:
            return market_result

        thsr_result = self._try_thsr_fare_provider(incoming_text=incoming_text)
        if thsr_result is not None:
            return thsr_result

        return None

    def _try_market_summary_provider(
        self,
        *,
        incoming_text: str,
        market_service: Any | None,
    ) -> KnowledgeAnswerResult | None:
        compact = "".join(incoming_text.lower().split())
        if not any(token in compact for token in ("盤勢", "類股", "大盤")):
            return None
        if market_service is None:
            return None

        try:
            snapshot = market_service.query_taiwan_weighted_index()
        except Exception:
            snapshot = None
        if snapshot is None:
            return None

        lines = [f"今天台股加權指數目前約 {snapshot.price:,.2f} 點。"]
        if snapshot.change is not None and snapshot.change_percent is not None:
            direction = "上漲" if snapshot.change >= 0 else "下跌"
            lines.append(
                f"相較前一交易時段{direction} {abs(snapshot.change):,.2f} 點"
                f"（{abs(snapshot.change_percent):.2f}%）。"
            )
        lines.append("目前這份回答先以大盤即時變化作為盤勢重點摘要。")
        lines.append("如果你要，我可以再補電子、金融、航運等族群角度。")
        lines.append("\n來源：\n- TWSE MIS API")
        return KnowledgeAnswerResult(
            text="\n".join(lines),
            used_local=False,
            used_web=True,
            source_markers=["TWSE MIS API"],
        )

    def _try_thsr_fare_provider(self, *, incoming_text: str) -> KnowledgeAnswerResult | None:
        compact = "".join(incoming_text.split())
        if "高鐵" not in compact:
            return None
        if not any(token in compact for token in ("票價", "車票", "fare", "ticket")):
            return None

        stations = [
            alias
            for alias in _THSR_STATION_ALIASES
            if alias in incoming_text
        ]
        canonical_stations = list(
            dict.fromkeys(_THSR_STATION_ALIASES[item] for item in stations)
        )
        if len(canonical_stations) < 2:
            return None

        origin = canonical_stations[0]
        destination = canonical_stations[1]
        forward_fare = self._fetch_thsr_standard_fare(origin=origin, destination=destination)
        reverse_fare = self._fetch_thsr_standard_fare(origin=destination, destination=origin)
        candidates = [fare for fare in (forward_fare, reverse_fare) if fare is not None]
        if not candidates:
            return None
        fare = min(candidates)

        text = (
            f"高鐵 {origin} 到 {destination} 的標準車廂對號座全票目前約 {fare:,} 元。\n"
            "如果你要，我也可以再補自由座票價或查詢時刻。"
            "\n\n來源：\n"
            f"- {_THSR_FARE_URL}"
        )
        return KnowledgeAnswerResult(
            text=text,
            used_local=False,
            used_web=True,
            source_markers=[_THSR_FARE_URL],
        )

    def _fetch_thsr_standard_fare(self, *, origin: str, destination: str) -> int | None:
        station_order = [
            "南港",
            "台北",
            "板橋",
            "桃園",
            "新竹",
            "苗栗",
            "台中",
            "彰化",
            "雲林",
            "嘉義",
            "台南",
            "左營",
        ]
        if origin not in station_order or destination not in station_order:
            return None

        text = fetch_url(_THSR_FARE_URL)
        if not text or text.startswith(("HTTP ", "抓取逾時", "無效網址", "不支援的 Content-Type")):
            return None

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        row_map: dict[str, list[str]] = {}
        for idx, line in enumerate(lines):
            for station in station_order:
                if station in row_map:
                    continue
                if line != f"{station}駅":
                    continue
                candidate = lines[idx + 1 : idx + 1 + len(station_order)]
                if len(candidate) < len(station_order):
                    continue
                if not all(
                    token == "-" or token.replace(",", "").replace("*", "").isdigit()
                    for token in candidate
                ):
                    continue
                row_map[station] = candidate
                break

        fare_tokens = row_map.get(origin)
        if fare_tokens is None:
            return None

        destination_index = station_order.index(destination)
        raw_fare = fare_tokens[destination_index].replace(",", "").replace("*", "")
        if raw_fare == "-" or not raw_fare.isdigit():
            return None
        return int(raw_fare)
