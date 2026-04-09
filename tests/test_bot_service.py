from __future__ import annotations

from linebot_app.db.sqlite import init_db
from linebot_app.repositories.llm_log_repository import LLMLogRepository
from linebot_app.repositories.message_repository import MessageRepository
from linebot_app.repositories.prompt_repository import PromptRepository
from linebot_app.repositories.session_repository import SessionRepository
from linebot_app.services.bot_service import BotService
from linebot_app.services.llm_service import LLMReply
from linebot_app.services.market_service import MarketSnapshot
from linebot_app.services.prompt_service import PromptService
from linebot_app.services.session_service import SessionService
from linebot_app.services.weather_service import WeatherSnapshot


class _FakeLLMService:
    chat_model = "fake-model"
    max_tokens = 1024
    temperature = 0.7
    timeout_seconds = 10

    def __init__(self) -> None:
        self.last_system_prompt = ""
        self.last_conversation: list[dict[str, str]] = []

    def generate_reply(
        self,
        *,
        system_prompt: str,
        conversation: list[dict[str, str]],
        timeout_seconds: int | None = None,
        max_tokens: int | None = None,
    ) -> LLMReply:
        self.last_system_prompt = system_prompt
        self.last_conversation = conversation
        return LLMReply(
            text="一般回覆",
            model_name="fake-model",
            latency_ms=120,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
        )


def _build_service(
    tmp_path,
) -> tuple[BotService, LLMLogRepository, MessageRepository, _FakeLLMService]:
    db_path = str(tmp_path / "app.db")
    init_db(db_path)

    session_repo = SessionRepository(db_path)
    message_repo = MessageRepository(db_path)
    prompt_repo = PromptRepository(db_path)
    llm_log_repo = LLMLogRepository(db_path)
    llm_service = _FakeLLMService()

    session_service = SessionService(
        session_repository=session_repo,
        message_repository=message_repo,
        max_turns=8,
    )
    prompt_service = PromptService(prompt_repository=prompt_repo, default_prompt="system")
    bot_service = BotService(
        session_service=session_service,
        message_repository=message_repo,
        llm_log_repository=llm_log_repo,
        llm_service=llm_service,
        prompt_service=prompt_service,
        rag_service=None,
        rag_enabled=False,
        rag_top_k=3,
        max_context_chars=6000,
    )
    return bot_service, llm_log_repo, message_repo, llm_service


def test_bot_service_persists_messages_and_logs(tmp_path) -> None:
    service, log_repo, message_repo, _ = _build_service(tmp_path)

    reply = service.handle_user_message(line_user_id="u1", text="哈囉")

    assert reply == "一般回覆"
    logs = log_repo.get_recent(limit=1)
    assert logs[0].status == "success"
    messages = message_repo.get_recent_messages(session_id=1, limit=10)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"


def test_bot_service_rejects_coding_request_when_disabled(tmp_path) -> None:
    service, _, _, _ = _build_service(tmp_path)

    reply = service.handle_user_message(line_user_id="u5", text="請幫我寫 Python code")

    assert "不提供程式碼撰寫" in reply


def test_bot_service_routes_capability_inquiry(tmp_path) -> None:
    service, _, _, _ = _build_service(tmp_path)

    reply = service.handle_user_message(line_user_id="u-cap", text="你能上網查資料嗎")

    assert "透過網路查詢" in reply
    assert "今天台積電股價" in reply


def test_bot_service_uses_grounded_weather_reply(tmp_path) -> None:
    service, _, _, _ = _build_service(tmp_path)
    service.weather_service = type(
        "_FakeWeatherService",
        (),
        {
            "query_today": lambda self, query: WeatherSnapshot(
                location="台北",
                summary="台北今天天氣多雲，氣溫約 20-26°C",
                temp_min=20,
                temp_max=26,
                rain_max=30,
                wind_max=12.0,
            )
        },
    )()

    reply = service.handle_user_message(line_user_id="u-weather", text="台北今天天氣如何")

    assert "台北今天天氣多雲" in reply
    assert "Open-Meteo" in reply


def test_bot_service_uses_grounded_market_reply(tmp_path) -> None:
    service, _, _, _ = _build_service(tmp_path)
    service.market_service = type(
        "_FakeMarketService",
        (),
        {
            "query_taiwan_stock_by_query": lambda self, query: MarketSnapshot(
                symbol="2330.TW",
                display_name="台積電",
                price=950.0,
                change=10.0,
                change_percent=1.06,
                market_time=None,
                source="TWSE MIS API",
            ),
            "query_taiwan_weighted_index": lambda self: None,
        },
    )()

    reply = service.handle_user_message(line_user_id="u-market", text="今天台積電股價是多少")

    assert "台積電" in reply
    assert "TWSE MIS API" in reply


def test_market_service_uses_twse_order_book_when_latest_price_missing() -> None:
    from linebot_app.services.market_service import _extract_twse_price

    price = _extract_twse_price(
        {
            "z": "-",
            "b": "1935.0000_1930.0000_",
            "a": "1940.0000_1945.0000_",
            "o": "1945.0000",
        }
    )

    assert price == 1937.5


def test_bot_service_uses_grounded_vehicle_specs_reply(tmp_path) -> None:
    service, _, _, _ = _build_service(tmp_path)
    service._search_web_results = lambda **kwargs: [
        type(
            "_FakeSearchResult",
            (),
            {
                "title": "Volkswagen T-Roc R specs & dimensions | Parkers",
                "url": "https://www.parkers.co.uk/volkswagen/t-roc/r-2019/specs/",
                "snippet": (
                    "Parkers 列出的車型為 2.0 TSI 300PS 4Motion DSG，"
                    "馬力約 295 bhp，0-60 mph 約 4.7 secs"
                ),
            },
        )()
    ]

    reply = service.handle_user_message(
        line_user_id="u-car",
        text="VW T-ROC R 2024版 性能規格如何",
    )

    assert "Volkswagen T-Roc R specs" in reply
    assert "295 bhp" in reply


def test_bot_service_uses_grounded_realtime_reply(tmp_path) -> None:
    service, _, _, _ = _build_service(tmp_path)
    service._search_web_results = lambda **kwargs: [
        type(
            "_FakeSearchResult",
            (),
            {
                "title": "CNA latest news",
                "url": "https://www.cna.com.tw/news/ahel/202604090001.aspx",
                "snippet": "latest update A",
            },
        )(),
        type(
            "_FakeSearchResult",
            (),
            {
                "title": "Reuters breaking update",
                "url": "https://www.reuters.com/world/asia-pacific/example-story/",
                "snippet": "latest update B",
            },
        )(),
    ]

    reply = service.handle_user_message(
        line_user_id="u-news",
        text="latest news about Taiwan earthquake",
    )

    assert "信心等級" in reply
    assert "來源：" in reply


def test_bot_service_uses_grounded_general_lookup_reply(tmp_path) -> None:
    service, _, _, llm = _build_service(tmp_path)
    service._search_web_results = lambda **kwargs: [
        type(
            "_FakeSearchResult",
            (),
            {
                "title": "LangGraph overview",
                "url": "https://example.com/langgraph-overview",
                "snippet": "LangGraph is a framework for building stateful, multi-step LLM agents.",
            },
        )(),
        type(
            "_FakeSearchResult",
            (),
            {
                "title": "LangGraph docs",
                "url": "https://docs.example.com/langgraph",
                "snippet": (
                    "It helps orchestrate tool use, memory, "
                    "and workflow control in agent systems."
                ),
            },
        )(),
    ]

    def _fake_generate_reply(**kwargs) -> LLMReply:
        llm.last_system_prompt = kwargs["system_prompt"]
        llm.last_conversation = kwargs["conversation"]
        return LLMReply(
            text="LangGraph 是用來建立具狀態、多步驟代理流程的框架。",
            model_name="fake-model",
            latency_ms=80,
            prompt_tokens=12,
            completion_tokens=18,
            total_tokens=30,
        )

    llm.generate_reply = _fake_generate_reply

    reply = service.handle_user_message(line_user_id="u-lookup", text="LangGraph 是什麼")

    assert "LangGraph" in reply
    assert "來源：" in reply
    assert "example.com/langgraph-overview" in reply


def test_bot_service_falls_back_to_general_knowledge_when_search_results_are_low_trust(
    tmp_path,
) -> None:
    service, _, _, llm = _build_service(tmp_path)
    service._search_web_results = lambda **kwargs: [
        type(
            "_FakeSearchResult",
            (),
            {
                "title": "LangGraph 討論串",
                "url": "https://www.zhihu.com/question/123456",
                "snippet": "community discussion",
            },
        )()
    ]

    def _fake_generate_reply(**kwargs) -> LLMReply:
        llm.last_system_prompt = kwargs["system_prompt"]
        llm.last_conversation = kwargs["conversation"]
        return LLMReply(
            text="LangGraph 是用來設計多步驟代理流程的框架。",
            model_name="fake-model",
            latency_ms=60,
            prompt_tokens=8,
            completion_tokens=14,
            total_tokens=22,
        )

    llm.generate_reply = _fake_generate_reply

    reply = service.handle_user_message(line_user_id="u-lookup-fallback", text="LangGraph 是什麼")

    assert "LangGraph" in reply
    assert "一般知識整理回答" in reply


def test_bot_service_prefers_local_knowledge_before_web_search(tmp_path) -> None:
    service, _, _, llm = _build_service(tmp_path)
    service.rag_enabled = True
    service.rag_service = type(
        "_FakeRAGService",
        (),
        {
            "search": lambda self, query, top_k: [
                type(
                    "_FakeChunk",
                    (),
                    {
                        "source_path": "knowledge/kb.md",
                        "chunk_index": 0,
                        "content": "LineBot 專案的知識庫說明 LM Studio 與 RAG 的整合方式。",
                        "score": 0.91,
                    },
                )()
            ]
        },
    )()

    def _forbidden_search(**kwargs):
        raise AssertionError("web search should not run when local knowledge is enough")

    service._search_web_results = _forbidden_search

    def _fake_generate_reply(**kwargs) -> LLMReply:
        llm.last_system_prompt = kwargs["system_prompt"]
        llm.last_conversation = kwargs["conversation"]
        return LLMReply(
            text="這題我先根據本地知識庫回答：目前專案是以 LM Studio 搭配本地 RAG 來提供知識檢索。",
            model_name="fake-model",
            latency_ms=60,
            prompt_tokens=10,
            completion_tokens=16,
            total_tokens=26,
        )

    llm.generate_reply = _fake_generate_reply

    reply = service.handle_user_message(line_user_id="u-local-first", text="這個專案怎麼做知識檢索")

    assert "本地知識庫" in reply
    assert "kb.md#0" in reply


def test_bot_service_uses_unified_web_fallback_for_market_summary(tmp_path) -> None:
    service, _, _, llm = _build_service(tmp_path)
    service.market_service = type(
        "_NoMarketData",
        (),
        {
            "query_taiwan_stock_by_query": lambda self, query: None,
            "query_taiwan_weighted_index": lambda self: None,
        },
    )()
    service._search_web_results = lambda **kwargs: [
        type(
            "_FakeSearchResult",
            (),
            {
                "title": "台股盤勢重點",
                "url": "https://example.com/market-summary",
                "snippet": "電子權值股偏強，航運整理，市場聚焦成交量與美股表現。",
            },
        )()
    ]

    def _fake_generate_reply(**kwargs) -> LLMReply:
        return LLMReply(
            text="今天盤勢重點是電子權值股偏強，市場焦點在成交量與外部消息面。",
            model_name="fake-model",
            latency_ms=70,
            prompt_tokens=10,
            completion_tokens=18,
            total_tokens=28,
        )

    llm.generate_reply = _fake_generate_reply

    reply = service.handle_user_message(line_user_id="u-market-summary", text="今天盤勢重點")

    assert "今天盤勢重點" in reply
    assert "example.com/market-summary" in reply


def test_bot_service_uses_official_provider_for_ticket_price(tmp_path) -> None:
    service, _, _, _ = _build_service(tmp_path)
    service.knowledge_answer_service._fetch_thsr_standard_fare = lambda **kwargs: 1080

    reply = service.handle_user_message(
        line_user_id="u-ticket-price",
        text="請查高鐵 台北到嘉義車票價格",
    )

    assert "1,080 元" in reply
    assert "thsrc.com.tw" in reply
