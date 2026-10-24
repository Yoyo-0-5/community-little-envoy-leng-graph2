import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_WEATHER_CODES = {
    0: "晴朗",
    1: "大致晴朗",
    2: "局部多雲",
    3: "陰天",
    45: "有霧",
    48: "霧凇",
    51: "毛毛雨",
    53: "毛毛雨",
    55: "毛毛雨",
    56: "凍毛毛雨",
    57: "凍毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "凍雨",
    67: "凍雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "霰",
    80: "陣雨",
    81: "陣雨",
    82: "強陣雨",
    85: "陣雪",
    86: "強陣雪",
    95: "雷雨",
    96: "雷雨伴隨冰雹",
    99: "強雷雨伴隨冰雹",
}


class WeatherProviderError(Exception):
    """A safe-to-report failure from the weather provider."""


def get_weather(lat: float, lng: float) -> dict:
    params = urlencode(
        {
            "latitude": lat,
            "longitude": lng,
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "is_day,precipitation,weather_code,wind_speed_10m"
            ),
            "timezone": "auto",
        }
    )
    try:
        with urlopen(f"{_OPEN_METEO_URL}?{params}", timeout=10) as response:
            data = json.loads(response.read())
    except HTTPError as exc:
        raise WeatherProviderError(
            f"天氣服務回傳 HTTP {exc.code}。"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise WeatherProviderError("天氣服務目前無法連線。") from exc
    except json.JSONDecodeError as exc:
        raise WeatherProviderError("天氣服務回傳無法解析的資料。") from exc

    current = data.get("current") if isinstance(data, dict) else None
    if not isinstance(current, dict):
        raise WeatherProviderError("天氣服務回傳格式不正確。")
    code = current.get("weather_code")
    if not isinstance(code, int):
        code = None
    return {
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": data.get("timezone"),
        "time": current.get("time"),
        "temperatureC": current.get("temperature_2m"),
        "apparentTemperatureC": current.get("apparent_temperature"),
        "humidityPercent": current.get("relative_humidity_2m"),
        "precipitationMm": current.get("precipitation"),
        "windSpeedKmh": current.get("wind_speed_10m"),
        "isDay": current.get("is_day") == 1,
        "weatherCode": code,
        "condition": _WEATHER_CODES.get(code, "天氣資訊"),
    }
