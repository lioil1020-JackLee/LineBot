from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx


@dataclass(frozen=True)
class WeatherSnapshot:
    location: str
    summary: str
    temp_min: float | None
    temp_max: float | None
    rain_max: int | None
    wind_max: float | None


_WEATHER_CODE_ZH = {
    0: "晴朗",
    1: "大致晴",
    2: "局部多雲",
    3: "陰天",
    45: "有霧",
    48: "霧凇",
    51: "毛毛雨",
    53: "短暫毛毛雨",
    55: "持續毛毛雨",
    61: "小雨",
    63: "雨",
    65: "大雨",
    66: "凍雨",
    67: "強凍雨",
    71: "小雪",
    73: "雪",
    75: "大雪",
    77: "冰粒",
    80: "陣雨",
    81: "較強陣雨",
    82: "強陣雨",
    95: "雷雨",
    96: "雷雨夾小冰雹",
    99: "雷雨夾大冰雹",
}

_LOCATION_NORMALIZATION = {
    "臺北": "台北",
    "臺中": "台中",
    "臺南": "台南",
    "臺東": "台東",
    "臺灣": "台灣",
}

_GEOCODE_QUERY_ALIASES = {
    "台北": "Taipei",
    "新北": "New Taipei",
    "基隆": "Keelung",
    "桃園": "Taoyuan",
    "新竹": "Hsinchu",
    "苗栗": "Miaoli",
    "台中": "Taichung",
    "彰化": "Changhua",
    "南投": "Nantou",
    "雲林": "Yunlin",
    "嘉義": "Chiayi",
    "台南": "Tainan",
    "高雄": "Kaohsiung",
    "屏東": "Pingtung",
    "宜蘭": "Yilan",
    "花蓮": "Hualien",
    "台東": "Taitung",
    "澎湖": "Penghu",
    "金門": "Kinmen",
    "連江": "Lienchiang",
    "淡水": "Tamsui",
    "中壢": "Zhongli",
}

_LOCATION_PARENT_CITY = {
    "淡水": "新北",
    "中壢": "桃園",
}

_LOCATION_ALIASES = tuple(_GEOCODE_QUERY_ALIASES.keys())


class WeatherService:
    def __init__(self) -> None:
        self._geo_base = "https://geocoding-api.open-meteo.com/v1/search"
        self._forecast_base = "https://api.open-meteo.com/v1/forecast"

    def query_today(self, location_query: str) -> WeatherSnapshot | None:
        location = self._extract_location(location_query)
        geo = self._geocode(location)
        if geo is None:
            return None

        forecast = self._forecast(geo["latitude"], geo["longitude"])
        if forecast is None:
            return None

        return self._build_snapshot(location=location, forecast=forecast)

    def _extract_location(self, text: str) -> str:
        normalized_text = self._normalize_location_text(text)
        for alias in _LOCATION_ALIASES:
            if alias in normalized_text:
                return alias

        match = re.search(
            r"([\u4e00-\u9fff]{2,5})(?:今天天氣|天氣|氣溫|降雨|下雨|天候)",
            normalized_text,
        )
        if match:
            candidate = _LOCATION_NORMALIZATION.get(match.group(1), match.group(1))
            if candidate in _GEOCODE_QUERY_ALIASES:
                return candidate

        return "台北"

    def _normalize_location_text(self, text: str) -> str:
        normalized = text.strip()
        for old, new in _LOCATION_NORMALIZATION.items():
            normalized = normalized.replace(old, new)
        return normalized

    def _geocode(self, location: str) -> dict | None:
        for candidate in self._build_geocode_candidates(location):
            params = {"name": candidate, "count": 1, "format": "json", "language": "zh"}
            try:
                with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                    response = client.get(self._geo_base, params=params)
                response.raise_for_status()
                data = response.json()
            except Exception:
                continue

            results = data.get("results") or []
            if results:
                return results[0]

        return None

    def _build_geocode_candidates(self, location: str) -> list[str]:
        normalized = _LOCATION_NORMALIZATION.get(location, location).strip()
        candidates: list[str] = []
        if normalized:
            candidates.append(normalized)

        english_alias = _GEOCODE_QUERY_ALIASES.get(normalized)
        if english_alias:
            candidates.append(english_alias)
            candidates.append(f"{english_alias}, Taiwan")

        parent_city = _LOCATION_PARENT_CITY.get(normalized)
        if parent_city:
            candidates.append(parent_city)
            parent_en = _GEOCODE_QUERY_ALIASES.get(parent_city)
            if parent_en:
                candidates.append(parent_en)
                candidates.append(f"{parent_en}, Taiwan")

        return list(dict.fromkeys(candidates))

    def _forecast(self, lat: float, lon: float) -> dict | None:
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,precipitation_probability,weather_code,wind_speed_10m",
            "timezone": "Asia/Taipei",
            "forecast_days": 1,
        }
        try:
            with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                response = client.get(self._forecast_base, params=params)
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

    def _build_snapshot(self, *, location: str, forecast: dict) -> WeatherSnapshot | None:
        hourly = forecast.get("hourly") or {}
        times = hourly.get("time") or []
        temps = hourly.get("temperature_2m") or []
        pops = hourly.get("precipitation_probability") or []
        codes = hourly.get("weather_code") or []
        winds = hourly.get("wind_speed_10m") or []
        if not times or not temps:
            return None

        now = self._now_taipei()
        now_key = now.strftime("%Y-%m-%dT%H:00")

        index = 0
        for idx, value in enumerate(times):
            if value >= now_key:
                index = idx
                break

        temp_values = [float(value) for value in temps if isinstance(value, (int, float))]
        pop_values = [int(value) for value in pops if isinstance(value, (int, float))]
        wind_values = [float(value) for value in winds if isinstance(value, (int, float))]

        temp_min = min(temp_values) if temp_values else None
        temp_max = max(temp_values) if temp_values else None
        rain_max = max(pop_values) if pop_values else None
        wind_max = max(wind_values) if wind_values else None
        weather_code = codes[index] if index < len(codes) else None
        weather_desc = _WEATHER_CODE_ZH.get(weather_code, "天氣變化")

        parts = [f"{location}今天天氣{weather_desc}"]
        if temp_min is not None and temp_max is not None:
            parts.append(f"氣溫約 {round(temp_min)}-{round(temp_max)}°C")
        if rain_max is not None:
            parts.append(f"降雨機率最高約 {rain_max}%")
        if wind_max is not None:
            parts.append(f"風速最高約 {round(wind_max, 1)} m/s")

        return WeatherSnapshot(
            location=location,
            summary="，".join(parts),
            temp_min=temp_min,
            temp_max=temp_max,
            rain_max=rain_max,
            wind_max=wind_max,
        )

    def _now_taipei(self) -> datetime:
        try:
            return datetime.now(ZoneInfo("Asia/Taipei"))
        except (ZoneInfoNotFoundError, ModuleNotFoundError):
            return datetime.now(timezone(timedelta(hours=8)))
