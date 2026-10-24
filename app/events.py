import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db import engine
from app.storage import object_url

_MAX_SOURCE_ROWS = 500
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100
_TAIPEI = ZoneInfo("Asia/Taipei")
_COORDINATES_RE = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$"
)


def _event_status(value: str | None) -> str:
    status = (value or "pending").strip().lower().replace(" ", "_")
    if status in {"pending", "reported", "new"}:
        return "reported"
    if status in {"in_progress", "processing", "in-progress"}:
        return "in-progress"
    if status in {"resolved", "completed", "done"}:
        return "resolved"
    return status


def _event(row: dict) -> dict:
    address = row.get("address") or ""
    match = _COORDINATES_RE.match(address)
    coordinates = (
        {"lat": float(match.group(1)), "lng": float(match.group(2))}
        if match
        else None
    )
    created_at = int(row["created_at"])
    images = row.get("images")
    photo = object_url(images[0]) if isinstance(images, list) and images else None
    description = row.get("description") or ""

    return {
        "id": str(row["cases_id"]),
        "type": row.get("category_label") or "",
        "title": description[:30],
        "placeText": address,
        "coordinates": coordinates,
        "timeText": datetime.fromtimestamp(
            created_at / 1000, tz=timezone.utc
        ).astimezone(_TAIPEI).strftime("%Y-%m-%d %H:%M"),
        "status": _event_status(row.get("status")),
        "description": description,
        "photo": photo,
        "audio": None,
        "audioName": "",
        "reportedAt": datetime.fromtimestamp(
            created_at / 1000, tz=timezone.utc
        ).isoformat(),
    }


def _matches_time(event: dict, time_filter: str) -> bool:
    if time_filter == "all":
        return True
    reported_at = datetime.fromisoformat(event["reportedAt"]).astimezone(_TAIPEI)
    now = datetime.now(_TAIPEI)
    if time_filter == "today":
        return reported_at.date() == now.date()
    if time_filter == "week":
        start_of_week = now.date() - timedelta(days=now.weekday())
        return start_of_week <= reported_at.date() <= now.date()
    if time_filter == "month":
        return (reported_at.year, reported_at.month) == (now.year, now.month)
    return True


def _matches_region(event: dict, region: str) -> bool:
    return not region or region in f"{event['placeText']} {event['title']} {event['type']}"


def list_events(
    *,
    time_filter: str = "month",
    event_type: str | None = None,
    status: str | None = None,
    region: str | None = None,
    filter_region: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> list[dict]:
    """Return recent non-rejected reports in the frontend Event shape."""
    if time_filter not in {"today", "week", "month", "all"}:
        raise ValueError("time 必須是 today、week、month 或 all。")
    limit = max(1, min(limit, _MAX_LIMIT))
    offset = max(0, offset)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.cases_id, c.description, c.images, c.status, "
                "c.created_at, c.address, categories.label AS category_label "
                "FROM cases AS c "
                "LEFT JOIN categories ON categories.categories_id = c.category_id "
                "WHERE lower(coalesce(c.status, 'pending')) NOT IN "
                "('rejected', 'declined') "
                "ORDER BY c.created_at DESC LIMIT :source_limit"
            ),
            {"source_limit": _MAX_SOURCE_ROWS},
        ).mappings().all()

    events = [_event(dict(row)) for row in rows]
    if event_type:
        events = [event for event in events if event["type"] == event_type]
    if status:
        events = [event for event in events if event["status"] == status]
    events = [
        event for event in events
        if _matches_time(event, time_filter)
        and _matches_region(event, filter_region or "")
    ]
    events.sort(
        key=lambda event: (
            0 if region and _matches_region(event, region) else 1,
            -datetime.fromisoformat(event["reportedAt"]).timestamp(),
        )
    )
    return events[offset : offset + min(limit, _MAX_LIMIT)]


def get_event(event_id: str) -> dict | None:
    if not event_id.isdigit():
        return None
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT c.cases_id, c.description, c.images, c.status, "
                "c.created_at, c.address, categories.label AS category_label "
                "FROM cases AS c "
                "LEFT JOIN categories ON categories.categories_id = c.category_id "
                "WHERE c.cases_id = :cases_id "
                "AND lower(coalesce(c.status, 'pending')) NOT IN "
                "('rejected', 'declined')"
            ),
            {"cases_id": int(event_id)},
        ).mappings().first()
    return _event(dict(row)) if row else None
