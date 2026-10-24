import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config import GOOGLE_MAPS_API_KEY

_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_MAX_CANDIDATES = 5


class PlacesProviderError(Exception):
    """A safe-to-report error returned while querying Google Maps."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _require_api_key() -> None:
    if not GOOGLE_MAPS_API_KEY:
        raise PlacesProviderError(
            "地點服務尚未設定 GOOGLE_MAPS_API_KEY。",
            status_code=503,
        )


def _json_request(request: Request) -> dict:
    try:
        with urlopen(request, timeout=8) as response:
            data = json.loads(response.read())
    except HTTPError as exc:
        raise PlacesProviderError(
            f"Google Maps 地點服務回傳 HTTP {exc.code}。"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise PlacesProviderError("Google Maps 地點服務目前無法連線。") from exc
    except json.JSONDecodeError as exc:
        raise PlacesProviderError("Google Maps 地點服務回傳無法解析的資料。") from exc
    if not isinstance(data, dict):
        raise PlacesProviderError("Google Maps 地點服務回傳格式不正確。")
    return data


def search_places(query: str) -> list[dict]:
    """Search Taiwan places and return the candidate schema used by the frontend."""
    normalized = query.strip()
    if not normalized:
        return []
    _require_api_key()
    request = Request(
        _PLACES_SEARCH_URL,
        data=json.dumps(
            {
                "textQuery": normalized,
                "regionCode": "TW",
                "languageCode": "zh-TW",
                "maxResultCount": _MAX_CANDIDATES,
            }
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.formattedAddress,places.location"
            ),
        },
        method="POST",
    )
    data = _json_request(request)
    places = data.get("places", [])
    if not isinstance(places, list):
        raise PlacesProviderError("Google Maps 地點服務回傳格式不正確。")

    candidates = []
    for place in places:
        if not isinstance(place, dict):
            continue
        location = place.get("location")
        place_id = place.get("id")
        if not isinstance(location, dict) or not place_id:
            continue
        lat, lng = location.get("latitude"), location.get("longitude")
        if (
            not isinstance(lat, (int, float))
            or not isinstance(lng, (int, float))
            or not math.isfinite(lat)
            or not math.isfinite(lng)
        ):
            continue
        display_name = place.get("displayName")
        name = display_name.get("text", "") if isinstance(display_name, dict) else ""
        candidates.append(
            {
                "id": place_id,
                "name": name,
                "address": place.get("formattedAddress") or "",
                "lat": lat,
                "lng": lng,
            }
        )
    return candidates


def reverse_geocode(lat: float, lng: float) -> str:
    """Resolve coordinates to a formatted address using Google Geocoding."""
    if not math.isfinite(lat) or not math.isfinite(lng):
        raise ValueError("座標必須是有限數值。")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("座標超出有效範圍。")
    _require_api_key()
    params = urlencode(
        {"latlng": f"{lat},{lng}", "language": "zh-TW", "key": GOOGLE_MAPS_API_KEY}
    )
    data = _json_request(Request(f"{_GEOCODING_URL}?{params}"))
    status = data.get("status")
    if status == "ZERO_RESULTS":
        return ""
    if status != "OK":
        raise PlacesProviderError("Google Maps 地址反查失敗。")
    results = data.get("results")
    if not isinstance(results, list):
        raise PlacesProviderError("Google Maps 地址反查回傳格式不正確。")
    first = results[0] if results else {}
    address = first.get("formatted_address") if isinstance(first, dict) else None
    return address if isinstance(address, str) else ""
