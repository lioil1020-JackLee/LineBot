from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from ..models.research import EvidenceBundle, EvidenceItem, ResearchPlan
from ..tools.fetch_url import fetch_url
from .web_search_service import WebSearchService

logger = logging.getLogger(__name__)

_HIGH_TRUST_FACTUAL_DOMAINS = (
    "twse.com.tw",
    "mops.twse.com.tw",
    "thsrc.com.tw",
    "railway.gov.tw",
    "cwa.gov.tw",
    "gov.tw",
    "moneydj.com",
    "open.er-api.com",
    "rate.bot.com.tw",
    "en.thsrc.com.tw",
)

_CWA_W50_DATA_URL = "https://www.cwa.gov.tw/Data/js/fcst/W50_Data.js"
_THSRC_GENERAL_FARE_URL = (
    "https://en.thsrc.com.tw/ArticleContent/"
    "4c3efc1d-e6df-4bfd-97b4-52e89f79ee5c"
)
_AQICN_TAIWAN_MAP_URL = "https://aqicn.org/map/taiwan/"


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _looks_market_question(text: str) -> bool:
    compact = (text or "").lower().replace(" ", "")
    return any(
        token in compact
        for token in ("股價", "股票", "收盤", "盤中", "twse", "quote", "price")
    )


def _looks_travel_fare_question(text: str) -> bool:
    compact = (text or "").lower().replace(" ", "")
    return ("高鐵" in compact or "thsrc" in compact) and any(
        token in compact for token in ("票價", "車票", "fare")
    )


def _looks_weather_question(text: str) -> bool:
    compact = (text or "").lower().replace(" ", "")
    return any(
        token in compact
        for token in (
            "天氣",
            "氣溫",
            "降雨",
            "forecast",
            "weather",
            "aqi",
            "空氣品質",
            "紫外線",
        )
    )


def _looks_platform_status_question(text: str) -> bool:
    compact = (text or "").lower().replace(" ", "")
    return any(
        token in compact
        for token in ("當機", "壞", "壞掉", "不能用", "故障", "維修", "status", "outage", "災情")
    )


def _looks_line_platform_question(text: str) -> bool:
    compact = (text or "").lower().replace(" ", "")
    return "line" in compact or "賴" in compact


_LINE_API_STATUS_URL = "https://api.line-status.info/"


def _fetch_line_api_status() -> tuple[str, str] | None:
    """Fetch LINE API status from LINE developer status page (best-effort)."""
    try:
        from bs4 import BeautifulSoup  # imported lazily

        html = httpx.get(
            _LINE_API_STATUS_URL,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ).text
        soup = BeautifulSoup(html, "html.parser")
        text = " ".join(soup.get_text(" ", strip=True).split())
    except Exception:
        return None

    if not text:
        return None

    m = re.search(r"(No incidents reported(?: today)?\.)", text, flags=re.IGNORECASE)
    if m:
        snippet = f"LINE API 狀態：{m.group(1).strip()}"
        return snippet, _LINE_API_STATUS_URL

    for token in (
        "Major Outage",
        "Partial Outage",
        "Degraded Performance",
        "Maintenance",
        "Incident",
    ):
        if token.lower() in text.lower():
            snippet = f"LINE API 狀態頁出現狀態訊號：{token}（請以狀態頁為準）"
            return snippet, _LINE_API_STATUS_URL

    return None


def _looks_driving_eta_question(text: str) -> bool:
    compact = (text or "").replace(" ", "")
    return (
        ("到" in compact or "→" in compact)
        and any(token in compact for token in ("開車", "多久", "要多久", "車程", "路程"))
    )


def _extract_two_places(text: str) -> tuple[str, str] | None:
    t = (text or "").replace("臺", "台").replace("→", "到")
    t = " ".join(t.split()).strip()
    m = re.search(r"([\u4e00-\u9fff]{2,8})到([\u4e00-\u9fff]{2,16})", t)
    if not m:
        return None
    a, b = m.group(1).strip(), m.group(2).strip()
    for cut in (
        "現在",
        "開車",
        "多久",
        "要多久",
        "車程",
        "路程",
        "多遠",
        "車票",
        "票價",
        "價格",
        "高鐵",
        "台鐵",
    ):
        if cut in b:
            b = b.split(cut, 1)[0].strip()
    if not a or not b or a == b:
        return None
    return a, b


def _geocode_tw(place: str) -> tuple[float, float] | None:
    q = (place or "").strip()
    if not q:
        return None
    url = "https://nominatim.openstreetmap.org/search"
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            resp = client.get(
                url,
                params={
                    "q": q,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "tw",
                },
                headers={"User-Agent": "linebot-research/0.1"},
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    item = data[0] if isinstance(data[0], dict) else {}
    try:
        lat = float(item.get("lat"))
        lon = float(item.get("lon"))
    except Exception:
        return None
    return lat, lon


def _fetch_osrm_driving_eta(
    a: tuple[float, float],
    b: tuple[float, float],
) -> tuple[str, str] | None:
    # OSRM expects lon,lat.
    (alat, alon), (blat, blon) = a, b
    url = f"http://router.project-osrm.org/route/v1/driving/{alon},{alat};{blon},{blat}"
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            resp = client.get(
                url,
                params={"overview": "false"},
                headers={"User-Agent": "linebot-research/0.1"},
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    routes = data.get("routes") if isinstance(data, dict) else None
    if not isinstance(routes, list) or not routes:
        return None
    r0 = routes[0] if isinstance(routes[0], dict) else {}
    duration = r0.get("duration")
    distance = r0.get("distance")
    if not isinstance(duration, (int, float)) or not isinstance(distance, (int, float)):
        return None
    mins = int(round(float(duration) / 60.0))
    km = float(distance) / 1000.0
    snippet = f"估計開車時間約 {mins} 分鐘（距離約 {km:.1f} 公里；OSRM 路線估算）"
    return snippet, url


def _is_platform_status_domain(host: str) -> bool:
    host = (host or "").lower()
    return host.endswith(
        (
            "status.line.me",
            "api.line-status.info",
            "developers.line.biz",
            "downdetector.com",
            "downdetector.tw",
            "istheservicedown.com",
        )
    )


def _has_platform_status_signal(text: str) -> bool:
    t = (text or "").lower()
    return any(
        token in t for token in ("status", "outage", "incident", "downdetector", "is it down")
    )


def _has_local_business_signal(text: str) -> bool:
    t = (text or "").replace(" ", "")
    return any(token in t for token in ("地址", "電話", "營業", "營業時間", "open", "hours"))


def _looks_gov_policy_question(text: str) -> bool:
    compact = (text or "").replace(" ", "")
    return any(
        token in compact
        for token in (
            "報稅",
            "補助",
            "停班停課",
            "罰則",
            "法規",
            "公告",
            "規定",
            "期限",
            "截止",
        )
    )


def _is_gov_policy_domain(host: str) -> bool:
    host = (host or "").lower()
    return host.endswith(
        (
            "gov.tw",
            "moj.gov.tw",
            "law.moj.gov.tw",
            "mof.gov.tw",
            "ntb.gov.tw",
            "cwa.gov.tw",
            "dgpa.gov.tw",
        )
    )


def _has_gov_policy_signal(text: str) -> bool:
    t = (text or "").replace(" ", "")
    # Date / deadline / penalty signals.
    return bool(re.search(r"\d{1,4}[/-]\d{1,2}[/-]\d{1,2}", t)) or any(
        token in t for token in ("期限", "截止", "罰", "處", "條例", "第", "條")
    )


def _looks_shopping_discount_question(text: str) -> bool:
    compact = (text or "").lower().replace(" ", "")
    return any(token in compact for token in ("最低價", "特價", "折扣", "比價", "deals", "price"))


def _has_price_signal(text: str) -> bool:
    t = (text or "").replace(" ", "")
    return bool(re.search(r"\d{2,6}", t)) and any(
        token in t for token in ("元", "nt", "twd", "$", "usd", "價格")
    )


def _looks_entertainment_question(text: str) -> bool:
    compact = (text or "").replace(" ", "")
    return any(
        token in compact
        for token in ("上映", "電影", "場次", "演唱會", "展覽", "市集", "音樂祭")
    )


def _is_entertainment_domain(host: str) -> bool:
    host = (host or "").lower()
    return host.endswith(
        (
            "atmovies.com.tw",
            "yahoo.com",
            "imdb.com",
            "rottentomatoes.com",
            "opentix.life",
            "kktix.com",
            "tixcraft.com",
            "ticketplus.com.tw",
        )
    )


def _has_entertainment_signal(text: str) -> bool:
    t = (text or "").replace(" ", "")
    return any(token in t for token in ("上映", "場次", "售票", "開賣", "映演", "影城", "戲院"))


def _has_weather_signal(text: str) -> bool:
    t = (text or "").replace(" ", "")
    return any(
        token in t
        for token in (
            "°",
            "℃",
            "AQI",
            "aqi",
            "降雨",
            "降水",
            "降雨機率",
            "濕度",
            "風速",
            "體感",
            "預報",
            "雷達",
            "累積雨量",
        )
    )


def _is_weather_domain(host: str) -> bool:
    host = (host or "").lower()
    return host.endswith(
        (
            "cwa.gov.tw",
            "weather.com",
            "accuweather.com",
            "windy.com",
            "metoffice.gov.uk",
        )
    ) or host.endswith("xn--rsso55b.tw")

def _is_air_quality_domain(host: str) -> bool:
    host = (host or "").lower()
    return host.endswith(("aqicn.org", "iqair.com", "moenv.gov.tw", "epa.gov.tw"))


def _cwa_county_code_from_query(text: str) -> str:
    """Best-effort mapping for common Taiwan locations to CWA county codes."""
    q = (text or "").replace("臺", "台")
    if any(token in q for token in ("新北", "淡水", "板橋", "三重", "新莊", "汐止", "林口")):
        return "65"
    if "台北" in q:
        return "63"
    if any(token in q for token in ("桃園", "中壢")):
        return "68"
    if "新竹" in q:
        return "10004"
    if any(token in q for token in ("台中", "臺中")):
        return "66"
    if any(token in q for token in ("台南", "臺南")):
        return "67"
    if any(token in q for token in ("高雄", "左營")):
        return "64"
    return ""


def _thsrc_station_en(name: str) -> str:
    s = (name or "").replace("臺", "台").strip()
    mapping = {
        "南港": "Nangang",
        "台北": "Taipei",
        "板橋": "Banqiao",
        "桃園": "Taoyuan",
        "新竹": "Hsinchu",
        "苗栗": "Miaoli",
        "台中": "Taichung",
        "彰化": "Changhua",
        "雲林": "Yunlin",
        "嘉義": "Chiayi",
        "台南": "Tainan",
        "左營": "Zuoying",
    }
    return mapping.get(s, "")


def _extract_thsrc_station_pair(text: str) -> tuple[str, str] | None:
    q = (text or "").replace("臺", "台")
    stations = [
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
    found: list[tuple[int, str]] = []
    for s in stations:
        idx = q.find(s)
        if idx >= 0:
            found.append((idx, s))
    found.sort(key=lambda x: x[0])
    dedup: list[str] = []
    for _, s in found:
        if s not in dedup:
            dedup.append(s)
    if len(dedup) >= 2:
        return dedup[0], dedup[1]
    return None


def _fetch_thsrc_general_fare(origin: str, dest: str) -> tuple[str, str] | None:
    """Fetch THSRC general fare from official English fare table (best-effort)."""
    o = _thsrc_station_en(origin)
    d = _thsrc_station_en(dest)
    if not o or not d or o == d:
        return None

    try:
        from bs4 import BeautifulSoup  # imported lazily to keep module import light

        html = httpx.get(
            _THSRC_GENERAL_FARE_URL,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ).text
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        if not tables:
            return None
        t = tables[0]
        rows: list[list[str]] = []
        for tr in t.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        if len(rows) < 2:
            return None
        header = [h.strip() for h in rows[0][1:]]
        fare_rows = rows[1:]
    except Exception:
        return None

    def _to_int(x: str) -> int | None:
        raw = (x or "").replace(",", "").replace("*", "").strip()
        return int(raw) if raw.isdigit() else None

    row_map: dict[str, dict[str, int]] = {}
    for r in fare_rows:
        if not r:
            continue
        src = r[0].strip()
        fares: dict[str, int] = {}
        for i, dest_name in enumerate(header):
            if i + 1 >= len(r):
                continue
            value = _to_int(r[i + 1])
            if value is not None:
                fares[dest_name] = value
        row_map[src] = fares

    price = row_map.get(o, {}).get(d)
    if price is None:
        price = row_map.get(d, {}).get(o)
    if price is None:
        return None

    snippet = f"THSRC 一般票價（{origin}→{dest}）約 {price} 元（以官方票價表為準）。"
    return snippet, _THSRC_GENERAL_FARE_URL


def _fetch_cwa_w50_county_brief(county_code: str) -> tuple[str, str] | None:
    code = (county_code or "").strip()
    if not code:
        return None
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            resp = client.get(_CWA_W50_DATA_URL, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        text = resp.text
    except Exception:
        return None

    # Extract the county block in the JS payload.
    # Format: '65':{ 'Title':'...', 'Content':[ '...', '...' ], 'DataTime':'MM/DD HH:MM' }
    start = text.find(f"'{code}':")
    if start < 0:
        return None
    window = text[start : start + 1200]
    title_m = re.search(r"'Title':'([^']+)'", window)
    content_m = re.search(r"'Content':\[\s*'([^']+)'", window)
    time_m = re.search(r"'DataTime':'([^']+)'", window)
    title = title_m.group(1) if title_m else ""
    first = content_m.group(1) if content_m else ""
    when = time_m.group(1) if time_m else ""
    snippet = " ".join(part for part in (title, first, f"（更新 {when}）" if when else "") if part)
    if not snippet:
        return None
    return snippet, _CWA_W50_DATA_URL


def _aqi_location_hint(text: str) -> str:
    q = (text or "").replace("臺", "台")
    if "淡水" in q:
        return "Tamsui"
    if "新北" in q:
        return "New Taipei"
    if "台北" in q:
        return "Taipei"
    if "桃園" in q:
        return "TaoYuan"
    if "台中" in q:
        return "Taichung"
    if "台南" in q:
        return "Tainan"
    if "高雄" in q:
        return "Kaohsiung"
    return ""


def _fetch_aqicn_taiwan_aqi(question: str) -> tuple[str, str] | None:
    """Fetch an AQI snapshot from AQICN Taiwan map (no key, best-effort)."""
    try:
        html = httpx.get(
            _AQICN_TAIWAN_MAP_URL,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ).text
    except Exception:
        return None

    hint = _aqi_location_hint(question)
    # Try a location-matching station first; fallback to Taipei as a practical default.
    candidates = []
    if hint:
        candidates.append(hint)
    candidates.append("Taipei")

    for key in candidates:
        m = re.search(
            rf'"aqi":"(?P<aqi>\d{{1,3}})".{{0,180}}?"name":"(?P<name>[^"]*{re.escape(key)}[^"]*)".{{0,120}}?"t":"(?P<t>[^"]+)"',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not m:
            # Try reverse order (name then aqi).
            m = re.search(
                rf'"name":"(?P<name>[^"]*{re.escape(key)}[^"]*)".{{0,180}}?"t":"(?P<t>[^"]+)".{{0,180}}?"aqi":"(?P<aqi>\d{{1,3}})"',
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )
        if m:
            aqi = m.group("aqi")
            name = m.group("name").replace("\\/", "/")
            t = m.group("t")
            snippet = f"AQI（{name}）約 {aqi}（時間 {t}；資料：AQICN）"
            return snippet, _AQICN_TAIWAN_MAP_URL

    return None


def _has_number_signal(text: str) -> bool:
    return bool(re.search(r"\d", text or ""))


def _has_market_signal(text: str) -> bool:
    t = (text or "").replace(" ", "")
    return _has_number_signal(t) or any(
        token in t for token in ("未提供成交價", "非交易時間", "無成交")
    )


def _is_high_trust_domain(host: str) -> bool:
    return any(host == d or host.endswith(f".{d}") for d in _HIGH_TRUST_FACTUAL_DOMAINS)


def _extract_tw_stock_code(text: str) -> str:
    """Extract a Taiwan stock code like 3706 from text."""
    if not text:
        return ""
    # Common formats: 神達(3706), 3706, 3706.TW etc.
    m = re.search(r"\((\d{4})\)", text)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{4})\b", text)
    if m:
        return m.group(1)
    return ""


def _fetch_twse_code_query(keyword: str) -> str:
    """Resolve a TWSE stock code from company keyword via TWSE codeQuery API."""
    q = (keyword or "").strip()
    if not q:
        return ""
    url = "https://www.twse.com.tw/zh/api/codeQuery"
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            resp = client.get(url, params={"query": q}, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return ""

    suggestions = data.get("suggestions") if isinstance(data, dict) else None
    if not isinstance(suggestions, list):
        return ""
    for item in suggestions:
        text = str(item)
        code = text.split("\t", 1)[0].strip()
        if re.fullmatch(r"\d{4}", code):
            return code
    return ""


def _extract_tw_stock_keyword(text: str) -> str:
    """Extract a likely company keyword from a stock quote question."""
    t = " ".join((text or "").split()).strip()
    if not t:
        return ""
    # Prefer text before 股價/股票 if present.
    for sep in ("股價", "股票"):
        if sep in t:
            t = t.split(sep, 1)[0]
            break
    stop = {"今天", "目前", "現在", "最新", "即時", "台股", "臺股", "價格", "怎樣", "如何"}
    for m in re.finditer(r"[\u4e00-\u9fff]{2,6}", t):
        w = m.group(0)
        if w not in stop:
            return w
    return ""


def _fetch_fx_rate_usd_twd() -> tuple[str, str] | None:
    """Fetch USD->TWD rate from a public JSON endpoint."""
    url = "https://open.er-api.com/v6/latest/USD"
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    rates = data.get("rates") if isinstance(data, dict) else None
    if not isinstance(rates, dict):
        return None
    twd = rates.get("TWD")
    if not isinstance(twd, (int, float)):
        return None
    snippet = f"USD→TWD 即時匯率約 {float(twd):.4f}（資料來源：open.er-api.com）"
    return snippet, url


def _fetch_twse_realtime_quote(stock_code: str) -> tuple[str, str] | None:
    """Fetch TWSE MIS realtime quote. Returns (snippet, url) if available."""
    code = (stock_code or "").strip()
    if not re.fullmatch(r"\d{4}", code):
        return None

    url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
    params = {"ex_ch": f"tse_{code}.tw"}
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            resp = client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    msg_array = data.get("msgArray") if isinstance(data, dict) else None
    if not isinstance(msg_array, list) or not msg_array:
        return None
    item = msg_array[0] if isinstance(msg_array[0], dict) else {}
    name = str(item.get("n") or item.get("nf") or "").strip()
    code_out = str(item.get("c") or code).strip()
    price = str(item.get("z") or "").strip()
    time_str = str(item.get("t") or item.get("tv") or "").strip()
    if not price or price == "-":
        snippet = (
            f"{name or code_out}（{code_out}）TWSE MIS 目前未提供成交價"
            "（可能為非交易時間或無成交）。"
        )
        source_url = f"{url}?ex_ch=tse_{code}.tw"
        return snippet, source_url

    snippet = f"{name or code_out}（{code_out}）即時成交價約 {price}（時間 {time_str}）"
    source_url = f"{url}?ex_ch=tse_{code}.tw"
    return snippet, source_url



@dataclass(frozen=True)
class WebResearchConfig:
    enabled: bool = True
    max_results_per_query: int = 4
    max_fetch_pages: int = 2


class WebResearchService:
    def __init__(
        self,
        *,
        web_search_service: WebSearchService,
        config: WebResearchConfig | None = None,
    ) -> None:
        self.web_search_service = web_search_service
        self.config = config or WebResearchConfig()

    def research(self, *, question: str, plan: ResearchPlan) -> EvidenceBundle:
        if not self.config.enabled or not plan.needs_external_info:
            return EvidenceBundle(items=[], sufficient=False, notes="web_disabled_or_not_needed")

        q = " ".join((question or "").split()).strip()
        queries = [item.strip() for item in (plan.search_queries or []) if item.strip()]
        if not queries and q:
            queries = [q]

        max_per_query = max(1, min(self.config.max_results_per_query, 8))
        fetched = 0
        items: list[EvidenceItem] = []
        seen_urls: set[str] = set()
        seen_hosts: dict[str, int] = {}
        fetched_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        # Provider shortcut: LINE API status (workaround for status.line.me DNS failures).
        if _looks_platform_status_question(q) and _looks_line_platform_question(q):
            status = _fetch_line_api_status()
            if status is not None:
                snippet, src = status
                items.append(
                    EvidenceItem(
                        kind="web",
                        title="LINE API Status（官方狀態頁）",
                        source=src,
                        snippet=snippet,
                        score=3.0,
                        fetched_at=fetched_at,
                    )
                )

        # Provider shortcut: CWA county brief forecast for Taiwan weather queries.
        if _looks_weather_question(q):
            code = _cwa_county_code_from_query(q)
            brief = _fetch_cwa_w50_county_brief(code) if code else None
            if brief is not None:
                snippet, src = brief
                items.append(
                    EvidenceItem(
                        kind="web",
                        title="中央氣象署 縣市概況預報（摘要）",
                        source=src,
                        snippet=snippet,
                        score=3.0,
                        fetched_at=fetched_at,
                    )
                )

        # Provider shortcut: AQI snapshot (AQICN Taiwan map).
        if "aqi" in q.lower().replace(" ", "") or "空氣品質" in q or "紫外線" in q:
            aqi = _fetch_aqicn_taiwan_aqi(q)
            if aqi is not None:
                snippet, src = aqi
                items.append(
                    EvidenceItem(
                        kind="web",
                        title="AQI 即時快照（AQICN）",
                        source=src,
                        snippet=snippet,
                        score=3.0,
                        fetched_at=fetched_at,
                    )
                )

        # Provider shortcut: THSRC general fare table for fare questions.
        if _looks_travel_fare_question(q) or plan.label == "travel_ticketing":
            pair = _extract_thsrc_station_pair(q) or _extract_two_places(q)
            if pair is not None:
                pa, pb = pair
                fare = _fetch_thsrc_general_fare(pa, pb)
                if fare is not None:
                    snippet, src = fare
                    items.append(
                        EvidenceItem(
                            kind="web",
                            title="THSRC 官方票價表（一般票價）",
                            source=src,
                            snippet=snippet,
                            score=3.0,
                            fetched_at=fetched_at,
                        )
                    )

        # Provider shortcut: driving ETA via Nominatim + OSRM.
        if _looks_driving_eta_question(q):
            pair = _extract_two_places(q)
            if pair is not None:
                pa, pb = pair
                ga = _geocode_tw(pa)
                gb = _geocode_tw(pb)
                if ga is not None and gb is not None:
                    eta = _fetch_osrm_driving_eta(ga, gb)
                    if eta is not None:
                        snippet, src = eta
                        items.append(
                            EvidenceItem(
                                kind="web",
                                title=f"開車時間估算（{pa}→{pb}）",
                                source=src,
                                snippet=snippet,
                                score=3.0,
                                fetched_at=fetched_at,
                            )
                        )

        # Provider shortcut: USD/TWD exchange rate.
        if (
            "匯率" in q
            and ("美元" in q or "usd" in q.lower())
            and ("台幣" in q or "twd" in q.lower())
        ):
            fx = _fetch_fx_rate_usd_twd()
            if fx is not None:
                snippet, src = fx
                items.append(
                    EvidenceItem(
                        kind="web",
                        title="USD→TWD 匯率（API）",
                        source=src,
                        snippet=snippet,
                        score=3.0,
                        fetched_at=fetched_at,
                    )
                )

        for query in queries:
            results, diag = self.web_search_service.search_with_diagnostics(
                query,
                max_results=max_per_query,
            )
            for result in results[:max_per_query]:
                url = (result.url or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                host = _host(url)
                if host:
                    seen_hosts[host] = seen_hosts.get(host, 0) + 1
                    # Cap per host to avoid one domain dominating.
                    if seen_hosts[host] > 2:
                        continue
                    if plan.freshness in {"today", "realtime"} and host.endswith("wikipedia.org"):
                        continue

                snippet = (result.snippet or "").strip()
                title = (result.title or "").strip() or url

                if fetched < self.config.max_fetch_pages:
                    page_text = fetch_url(url)
                    invalid_prefixes = (
                        "HTTP ",
                        "讀取逾時",
                        "無效的 URL",
                        "不支援的 Content-Type",
                    )
                    if page_text and not page_text.startswith(invalid_prefixes):
                        # Keep a compact page excerpt.
                        snippet = (page_text[:1200] + "…") if len(page_text) > 1200 else page_text
                        fetched += 1

                # Freshness/answerability hints.
                meta_text = f"{title} {snippet} {url}".lower()
                score = 0.0
                if plan.freshness in {"today", "realtime"}:
                    if any(
                        token in meta_text
                        for token in ("today", "今日", "今天", "更新", "latest", "breaking")
                    ):
                        score += 1.0
                    if str(datetime.now().year) in meta_text:
                        score += 0.5
                if len(snippet) >= 80:
                    score += 0.3

                items.append(
                    EvidenceItem(
                        kind="web",
                        title=title,
                        source=url,
                        snippet=snippet,
                        score=score,
                        fetched_at=fetched_at,
                    )
                )

        # If this looks like a TW stock quote question, try to upgrade evidence to TWSE MIS API.
        if _looks_market_question(q):
            # Prefer code directly from the user's question / TWSE codeQuery.
            code = _extract_tw_stock_code(q)
            if not code:
                kw = _extract_tw_stock_keyword(q)
                code = _fetch_twse_code_query(kw or q)
            # Only as a last resort, try to parse a code from search result titles/snippets.
            if not code:
                for item in items[:10]:
                    code = _extract_tw_stock_code(
                        f"{item.title} {item.snippet} {item.source}"
                    )
                    if code:
                        break
            if code:
                quote = _fetch_twse_realtime_quote(code)
                if quote is not None:
                    snippet, src = quote
                    items.insert(
                        0,
                        EvidenceItem(
                            kind="web",
                            title="TWSE MIS 即時報價",
                            source=src,
                            snippet=snippet,
                            score=3.0,
                            fetched_at=fetched_at,
                        ),
                    )

        # Rank evidence: higher score first, then longer snippet.
        items.sort(key=lambda i: (i.score or 0.0, len(i.snippet or "")), reverse=True)
        items = items[: max(2, max_per_query * 2)]

        # Domain-specific cleanup to avoid irrelevant evidence passing sufficiency.
        weather = _looks_weather_question(q)
        if weather:
            filtered: list[EvidenceItem] = []
            for item in items:
                h = _host(item.source)
                blob = (item.title or "") + (item.snippet or "")
                if (
                    _is_weather_domain(h)
                    or _is_air_quality_domain(h)
                    or _has_weather_signal(item.snippet)
                    or "天氣" in blob
                    or "AQI" in blob
                    or "空氣品質" in blob
                    or "紫外線" in blob
                ):
                    filtered.append(item)
            # For weather/AQI/UV, irrelevant pages must not pass.
            items = filtered

        platform_status = _looks_platform_status_question(q)
        if platform_status:
            filtered = []
            for item in items:
                h = _host(item.source)
                blob = f"{item.title} {item.snippet} {item.source}"
                if _is_platform_status_domain(h) or _has_platform_status_signal(blob):
                    filtered.append(item)
            if filtered:
                items = filtered

        # Sufficiency: must match question type (avoid "two wrong sources").
        distinct_hosts = {
            _host(item.source)
            for item in items
            if item.source
        }
        market = _looks_market_question(q)
        fare = _looks_travel_fare_question(q)
        weather = _looks_weather_question(q)
        platform_status = _looks_platform_status_question(q)
        traffic_eta = plan.label == "traffic_transit"
        health_avail = plan.label == "health_service_availability"
        inventory = plan.label == "inventory_local_availability"
        store_status = plan.label == "store_service_status"
        gov_policy = plan.label == "gov_policy_notice" or _looks_gov_policy_question(q)
        shopping = plan.label == "shopping_discount_comparison"
        if not shopping:
            shopping = _looks_shopping_discount_question(q)
        entertainment = plan.label == "entertainment_events" or _looks_entertainment_question(q)
        has_high_trust = any(_is_high_trust_domain(h) for h in distinct_hosts if h)
        has_answer_signal = any(_has_number_signal(item.snippet) for item in items)
        has_market_answer_signal = any(_has_market_signal(item.snippet) for item in items)
        has_weather_answer_signal = any(_has_weather_signal(item.snippet) for item in items)
        has_weather_domain = any(_is_weather_domain(h) for h in distinct_hosts if h)
        has_air_quality_domain = any(_is_air_quality_domain(h) for h in distinct_hosts if h)
        has_platform_domain = any(_is_platform_status_domain(h) for h in distinct_hosts if h)
        has_osrm_eta = any(
            "project-osrm.org/route" in (item.source or "") for item in items
        )
        has_business_signal = any(
            _has_local_business_signal(f"{it.title} {it.snippet} {it.source}") for it in items
        )
        has_gov_domain = any(_is_gov_policy_domain(h) for h in distinct_hosts if h)
        has_gov_signal = any(
            _has_gov_policy_signal(f"{it.title} {it.snippet} {it.source}") for it in items
        )
        has_price_answer = any(_has_price_signal(f"{it.title} {it.snippet}") for it in items)
        has_ent_domain = any(_is_entertainment_domain(h) for h in distinct_hosts if h)
        has_ent_signal = any(
            _has_entertainment_signal(f"{it.title} {it.snippet} {it.source}") for it in items
        )

        if market:
            sufficient = bool(items) and has_high_trust and has_market_answer_signal
        elif fare:
            sufficient = bool(items) and has_high_trust and has_answer_signal
        elif traffic_eta:
            sufficient = bool(items) and has_osrm_eta
        elif health_avail or inventory or store_status:
            # For local availability questions, generic articles are not answerable.
            sufficient = bool(items) and has_high_trust and has_business_signal
        elif gov_policy:
            sufficient = bool(items) and has_gov_domain and has_gov_signal
        elif shopping:
            sufficient = bool(items) and has_high_trust and has_price_answer
        elif entertainment:
            sufficient = bool(items) and has_ent_domain and has_ent_signal
        elif weather:
            sufficient = (
                bool(items)
                and (has_high_trust or has_weather_domain or has_air_quality_domain)
                and has_weather_answer_signal
            )
        elif platform_status:
            sufficient = bool(items) and has_platform_domain
        elif plan.freshness in {"today", "realtime"}:
            sufficient = len(distinct_hosts) >= 2 and has_answer_signal
        else:
            sufficient = len(distinct_hosts) >= 2 and len(items) >= 2
        if sufficient:
            notes = "web_ok"
        else:
            notes = "web_insufficient"
            if diag.get("last_error"):
                notes += f";last_error={diag['last_error']}"
            notes += f";hosts={sorted(h for h in distinct_hosts if h)[:6]}"

        logger.info(
            "web_research decision market=%s fare=%s freshness=%s sufficient=%s hosts=%s",
            market,
            fare,
            plan.freshness,
            sufficient,
            sorted(h for h in distinct_hosts if h)[:6],
        )
        return EvidenceBundle(items=items, sufficient=sufficient, notes=notes)

