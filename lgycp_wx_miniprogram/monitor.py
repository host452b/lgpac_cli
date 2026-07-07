"""Pure course-window and notification state transitions."""

from datetime import datetime, timedelta
from typing import Any

from lgycp_wx_miniprogram.models import Course, SHANGHAI


def _shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI)


def in_recent_window(course: Course, now: datetime) -> bool:
    current = _shanghai(now)
    published_at = _shanghai(course.published_at)
    return current - timedelta(days=7) <= published_at <= current


def update_archive(
    courses: list[Course], archive: dict[str, Any], now: datetime
) -> tuple[list[Course], dict[str, Any]]:
    timestamp = _shanghai(now).isoformat()
    first_run = archive.get("initialized_at") is None
    if first_run:
        archive["initialized_at"] = timestamp

    records = archive.setdefault("courses", {})
    candidates: list[Course] = []
    processed: set[str] = set()
    for current_course in courses:
        identity = current_course.identity
        if identity in processed:
            continue
        processed.add(identity)

        record = records.get(identity)
        if record is None:
            record = {
                "published_at": current_course.published_at.isoformat(),
                "first_seen_at": timestamp,
                "last_seen_at": timestamp,
                "baseline": first_run,
                "notified_at": None,
            }
            records[identity] = record
        else:
            record["last_seen_at"] = timestamp
            record["published_at"] = current_course.published_at.isoformat()

        if (
            not first_run
            and not record["baseline"]
            and record["notified_at"] is None
            and in_recent_window(current_course, now)
        ):
            candidates.append(current_course)

    return candidates, archive


def mark_notified(
    candidates: list[Course], archive: dict[str, Any], now: datetime
) -> None:
    timestamp = _shanghai(now).isoformat()
    for current_course in candidates:
        archive["courses"][current_course.identity]["notified_at"] = timestamp
