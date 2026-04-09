from __future__ import annotations

from linebot_app.services.weather_service import WeatherService


def test_weather_service_extracts_known_city() -> None:
    service = WeatherService()

    assert service._extract_location("桃園今天天氣如何") == "桃園"


def test_weather_service_defaults_to_taipei_when_location_missing() -> None:
    service = WeatherService()

    assert service._extract_location("今天天氣如何") == "台北"


def test_weather_service_builds_geocode_candidates_for_taipei_and_taoyuan() -> None:
    service = WeatherService()

    taipei_candidates = service._build_geocode_candidates("台北")
    taoyuan_candidates = service._build_geocode_candidates("桃園")

    assert "Taipei" in taipei_candidates
    assert "Taipei, Taiwan" in taipei_candidates
    assert "Taoyuan" in taoyuan_candidates
    assert "Taoyuan, Taiwan" in taoyuan_candidates


def test_weather_service_builds_parent_city_candidates_for_zhongli() -> None:
    service = WeatherService()

    zhongli_candidates = service._build_geocode_candidates("中壢")

    assert "桃園" in zhongli_candidates
    assert "Taoyuan" in zhongli_candidates
    assert "Taoyuan, Taiwan" in zhongli_candidates
