from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    display_name: str
    price: float
    change: float | None
    change_percent: float | None
    market_time: datetime | None
    source: str


class MarketService:
    def __init__(self) -> None:
        self._yahoo_quote = "https://query1.finance.yahoo.com/v7/finance/quote"
        self._twse_mis = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
        self._twse_company_list = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
        self._tpex_daily_quotes = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
        self._stock_directory: list[tuple[str, str, str]] | None = None
        self._common_aliases = {
            "台積電": ("2330", "台積電", "tse"),
            "tsmc": ("2330", "台積電", "tse"),
            "鴻海": ("2317", "鴻海", "tse"),
            "聯發科": ("2454", "聯發科", "tse"),
            "神達": ("3706", "神達", "tse"),
        }

    def query_taiwan_weighted_index(self) -> MarketSnapshot | None:
        twse = self._query_twse(ex_ch="tse_t00.tw", fallback_name="發行量加權股價指數")
        if twse is not None:
            return twse
        return self._query_quote(symbol="^TWII", fallback_name="發行量加權股價指數")

    def query_taiwan_stock(
        self,
        stock_code: str,
        *,
        fallback_name: str | None = None,
        market: str = "tse",
    ) -> MarketSnapshot | None:
        code = stock_code.strip()
        if not code:
            return None

        channel = "otc" if market.lower() == "otc" else "tse"
        snapshot = self._query_twse(
            ex_ch=f"{channel}_{code}.tw",
            fallback_name=fallback_name or f"台股 {code}",
        )
        if snapshot is not None:
            return snapshot

        symbol = code if code.upper().endswith(".TW") else f"{code}.TW"
        return self._query_quote(symbol=symbol, fallback_name=fallback_name or f"台股 {code}")

    def query_taiwan_stock_by_query(self, query: str) -> MarketSnapshot | None:
        query_text = (query or "").strip()
        if not query_text:
            return None

        code = self._extract_stock_code(query_text)
        if code:
            listed = self.query_taiwan_stock(code, fallback_name=f"台股 {code}", market="tse")
            if listed is not None:
                return listed
            return self.query_taiwan_stock(code, fallback_name=f"台股 {code}", market="otc")

        resolved = self._resolve_stock_from_name(query_text)
        if resolved is None:
            return None

        stock_code, stock_name, stock_market = resolved
        return self.query_taiwan_stock(
            stock_code,
            fallback_name=stock_name,
            market=stock_market,
        )

    def _extract_stock_code(self, text: str) -> str | None:
        match = re.search(r"(?<!\d)(\d{4,6})(?!\d)", text)
        return match.group(1) if match else None

    def _normalize_name(self, text: str) -> str:
        normalized = (text or "").strip().lower()
        for old, new in (("臺", "台"), ("股份有限公司", ""), ("控股", "")):
            normalized = normalized.replace(old, new)
        for token in (
            "今天",
            "目前",
            "現在",
            "股價",
            "股票",
            "台股",
            "大盤",
            "加權指數",
            "多少",
            "查詢",
            "報價",
            "price",
            "stock",
            "quote",
        ):
            normalized = normalized.replace(token, " ")
        return "".join(normalized.split())

    def _resolve_stock_from_name(self, query: str) -> tuple[str, str, str] | None:
        key = self._normalize_name(query)
        if not key:
            return None

        for alias, resolved in self._common_aliases.items():
            if alias in key or key in alias.lower():
                return resolved

        directory = self._load_stock_directory()
        if not directory:
            return None

        normalized_entries = [
            (code, name, market, self._normalize_name(name))
            for code, name, market in directory
        ]

        for code, name, market, normalized_name in normalized_entries:
            if key == normalized_name:
                return code, name, market

        candidates = [
            (code, name, market)
            for code, name, market, normalized_name in normalized_entries
            if key in normalized_name or normalized_name in key
        ]
        if not candidates:
            return None

        candidates.sort(key=lambda item: len(item[1]))
        return candidates[0]

    def _load_stock_directory(self) -> list[tuple[str, str, str]]:
        if self._stock_directory:
            return self._stock_directory

        rows: list[tuple[str, str, str]] = []

        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                response = client.get(
                    self._twse_company_list,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    code = str(item.get("公司代號") or "").strip()
                    name = str(item.get("公司簡稱") or item.get("公司名稱") or "").strip()
                    if code.isdigit() and name:
                        rows.append((code, name, "tse"))
        except Exception:
            pass

        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                response = client.get(
                    self._tpex_daily_quotes,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    code = str(item.get("SecuritiesCompanyCode") or "").strip()
                    name = str(item.get("CompanyName") or "").strip()
                    if code.isdigit() and name:
                        rows.append((code, name, "otc"))
        except Exception:
            pass

        deduped: dict[tuple[str, str], tuple[str, str, str]] = {}
        for code, name, market in rows:
            deduped.setdefault((code, market), (code, name, market))

        directory = list(deduped.values())
        if directory:
            self._stock_directory = directory
        return directory

    def _query_twse(self, *, ex_ch: str, fallback_name: str) -> MarketSnapshot | None:
        try:
            with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                response = client.get(
                    self._twse_mis,
                    params={"ex_ch": ex_ch},
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://mis.twse.com.tw/stock/fibest.jsp",
                    },
                )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None

        items = payload.get("msgArray") or []
        if not items:
            return None

        item = items[0]
        price = _extract_twse_price(item)
        if price is None:
            return None

        prev_price = None
        previous_close = str(item.get("y") or "").strip()
        if previous_close and previous_close != "-":
            try:
                prev_price = float(previous_close.replace(",", ""))
            except ValueError:
                prev_price = None

        change = None
        change_percent = None
        if prev_price not in {None, 0}:
            change = price - prev_price
            change_percent = (change / prev_price) * 100

        market_time = None
        timestamp = item.get("tlong")
        if isinstance(timestamp, str) and timestamp.isdigit():
            market_time = datetime.fromtimestamp(int(timestamp) / 1000, tz=UTC)

        return MarketSnapshot(
            symbol=str(item.get("c") or ex_ch).strip(),
            display_name=str(item.get("n") or fallback_name).strip() or fallback_name,
            price=price,
            change=change,
            change_percent=change_percent,
            market_time=market_time,
            source="TWSE MIS API",
        )

    def _query_quote(self, *, symbol: str, fallback_name: str) -> MarketSnapshot | None:
        try:
            with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                response = client.get(self._yahoo_quote, params={"symbols": symbol})
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None

        items = (payload.get("quoteResponse") or {}).get("result") or []
        if not items:
            return None

        item = items[0]
        price = item.get("regularMarketPrice")
        if not isinstance(price, (int, float)):
            return None

        market_time = None
        epoch = item.get("regularMarketTime")
        if isinstance(epoch, (int, float)):
            market_time = datetime.fromtimestamp(float(epoch), tz=UTC)

        return MarketSnapshot(
            symbol=str(item.get("symbol") or symbol),
            display_name=str(item.get("shortName") or fallback_name),
            price=float(price),
            change=(
                float(item["regularMarketChange"])
                if isinstance(item.get("regularMarketChange"), (int, float))
                else None
            ),
            change_percent=(
                float(item["regularMarketChangePercent"])
                if isinstance(item.get("regularMarketChangePercent"), (int, float))
                else None
            ),
            market_time=market_time,
            source="Yahoo Finance quote API",
        )


def _extract_twse_price(item: dict) -> float | None:
    latest = str(item.get("z") or "").strip()
    if latest and latest != "-":
        try:
            return float(latest.replace(",", ""))
        except ValueError:
            pass

    best_bid = _extract_first_level_price(item.get("b"))
    best_ask = _extract_first_level_price(item.get("a"))
    if best_bid is not None and best_ask is not None:
        return round((best_bid + best_ask) / 2, 2)
    if best_ask is not None:
        return best_ask
    if best_bid is not None:
        return best_bid

    for key in ("o", "pz", "h", "l"):
        raw = str(item.get(key) or "").strip()
        if not raw or raw == "-":
            continue
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            continue

    return None


def _extract_first_level_price(raw_levels: object) -> float | None:
    if not isinstance(raw_levels, str):
        return None
    for chunk in raw_levels.split("_"):
        candidate = chunk.strip()
        if not candidate or candidate == "-":
            continue
        try:
            return float(candidate.replace(",", ""))
        except ValueError:
            continue
    return None
