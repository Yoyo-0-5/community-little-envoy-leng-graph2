import hashlib
import json
import re
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.config import TAVILY_API_KEY

_TAVILY_URL = "https://api.tavily.com/search"
_MAX_RESULTS = 10
_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{3,4})[-/.年]"
    r"(?P<month>\d{1,2})[-/.月](?P<day>\d{1,2})日?"
)
_TAIPEI = ZoneInfo("Asia/Taipei")


class WelfareSearchError(Exception):
    """A safe-to-report failure from the configured web search provider."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _date_from_parts(year: str, month: str, day: str) -> date | None:
    parsed_year = int(year)
    if len(year) == 3:
        parsed_year += 1911
    try:
        return date(parsed_year, int(month), int(day))
    except ValueError:
        return None


def _event_date(text: str) -> str | None:
    candidates = []
    today = datetime.now(_TAIPEI).date()
    for match in _DATE_RE.finditer(text):
        parsed = _date_from_parts(
            match.group("year"), match.group("month"), match.group("day")
        )
        if parsed and parsed >= today:
            candidates.append(parsed)
    return min(candidates).isoformat() if candidates else None


def _published_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _source(url: str) -> str:
    return urlsplit(url).hostname or ""


def _search(query: str) -> dict:
    if not TAVILY_API_KEY:
        raise WelfareSearchError(
            "福利／活動搜尋尚未設定 TAVILY_API_KEY。", status_code=503
        )
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": _MAX_RESULTS,
        "include_domains": ["gov.tw", "org.tw"],
        "include_answer": False,
    }
    request = Request(
        _TAVILY_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:
            data = json.loads(response.read())
    except HTTPError as exc:
        raise WelfareSearchError(
            f"福利／活動搜尋服務回傳 HTTP {exc.code}。"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise WelfareSearchError("福利／活動搜尋服務目前無法連線。") from exc
    except json.JSONDecodeError as exc:
        raise WelfareSearchError("福利／活動搜尋服務回傳無法解析的資料。") from exc
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise WelfareSearchError("福利／活動搜尋服務回傳格式不正確。")
    return data


def search_welfare(
    *,
    event_type: str | None = None,
    target: str | None = None,
    time: str | None = None,
    region: str | None = None,
) -> dict:
    terms = [
        term.strip()
        for term in (region, target, event_type)
        if isinstance(term, str) and term.strip()
    ]
    event_type = event_type.strip() if event_type else None
    target = target.strip() if target else None
    region = region.strip() if region else None
    query = " ".join(terms + ["政府或社區福利補助活動資訊 活動日期 申請期限"])
    if time not in {None, "today", "week", "month", "all"}:
        raise ValueError("time 必須是 today、week、month 或 all。")
    data = _search(query)
    results = []
    for item in data["results"]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        content = str(item.get("content") or "").strip()
        published_at = _published_date(item.get("published_date"))
        event_date = _event_date(f"{title}\n{content}")
        source = _source(url)
        # Do not invent publication or activity dates when search results omit them.
        if not (title and url.startswith(("http://", "https://")) and source):
            continue
        if not published_at or not event_date:
            continue
        if not _matches_time(date.fromisoformat(event_date), time):
            continue
        if event_type and event_type not in f"{title} {source}":
            continue
        if target and target not in f"{title} {source}":
            continue
        results.append(
            {
                "id": hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
                "title": title,
                "source": source,
                "publishedAt": published_at,
                "eventDate": event_date,
                "url": url,
            }
        )
    unique = []
    seen = set()
    for item in results:
        key = (item["title"], item["eventDate"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    unique.sort(
        key=lambda item: (
            0 if region and region in f"{item['title']} {item['source']}" else 1,
            -date.fromisoformat(item["eventDate"]).toordinal(),
        )
    )
    return {"results": unique, "partial": False, "failedSources": []}


def _matches_time(event_date: date, time: str | None) -> bool:
    if time in {None, "all"}:
        return True
    today = datetime.now(_TAIPEI).date()
    if time == "today":
        return event_date == today
    if time == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
        return start <= event_date < end
    if time == "month":
        return (event_date.year, event_date.month) == (today.year, today.month)
    return True
