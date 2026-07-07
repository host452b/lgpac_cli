"""Pure course-window and notification state transitions."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterator

from lgycp_wx_miniprogram.models import Course, SHANGHAI


@dataclass(frozen=True)
class ArchiveUpdate:
    candidates: list[Course]
    archive: dict[str, Any]
    newly_seen_count: int
    missing_count: int

    def __iter__(self) -> Iterator[Any]:
        """Keep tuple unpacking compatible while callers adopt named fields."""
        yield self.candidates
        yield self.archive


def _shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI)


def in_recent_window(course: Course, now: datetime) -> bool:
    current = _shanghai(now)
    published_at = _shanghai(course.published_at)
    return current - timedelta(days=7) <= published_at <= current


def _business_fields(course: Course) -> dict[str, str]:
    return {
        "course_name": course.title,
        "price_yuan": course.price_yuan,
        "course_type": course.course_type,
        "published_at": _shanghai(course.published_at).isoformat(),
        "course_start_date": course.course_start_date,
        "course_end_date": course.course_end_date,
    }


def _course_record(
    course: Course, timestamp: str, baseline: bool
) -> dict[str, Any]:
    return {
        **_business_fields(course),
        "first_seen_at": timestamp,
        "last_seen_at": timestamp,
        "baseline": baseline,
        "notified_at": None,
    }


def update_archive(
    courses: list[Course], archive: dict[str, Any], now: datetime
) -> ArchiveUpdate:
    timestamp = _shanghai(now).isoformat()
    first_run = archive.get("initialized_at") is None
    if first_run:
        archive["initialized_at"] = timestamp

    records = archive.setdefault("courses", {})
    existing_identities = set(records)
    candidates: list[Course] = []
    processed: set[str] = set()
    newly_seen_count = 0
    for current_course in courses:
        identity = current_course.identity
        if identity in processed:
            continue
        processed.add(identity)

        record = records.get(identity)
        if record is None:
            record = _course_record(current_course, timestamp, first_run)
            records[identity] = record
            newly_seen_count += 1
        else:
            record.update(_business_fields(current_course))
            record["last_seen_at"] = timestamp

        if (
            not first_run
            and not record["baseline"]
            and record["notified_at"] is None
            and in_recent_window(current_course, now)
        ):
            candidates.append(current_course)

    return ArchiveUpdate(
        candidates=candidates,
        archive=archive,
        newly_seen_count=newly_seen_count,
        missing_count=len(existing_identities - processed),
    )


def mark_notified(
    candidates: list[Course], archive: dict[str, Any], now: datetime
) -> None:
    timestamp = _shanghai(now).isoformat()
    for current_course in candidates:
        archive["courses"][current_course.identity]["notified_at"] = timestamp


def finalize_success(
    archive: dict[str, Any],
    *,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    source_total: int,
    skipped_invalid_count: int,
    update: ArchiveUpdate,
    courses: list[Course],
    notified_count: int,
) -> None:
    published = [_shanghai(course.published_at) for course in courses]
    finished = _shanghai(finished_at).isoformat()
    archive["last_success_at"] = finished
    archive["last_run"] = {
        "run_id": run_id,
        "started_at": _shanghai(started_at).isoformat(),
        "finished_at": finished,
        "source_total": source_total,
        "parsed_count": len(courses),
        "skipped_invalid_count": skipped_invalid_count,
        "newly_seen_count": update.newly_seen_count,
        "eligible_count": len(update.candidates),
        "notified_count": notified_count,
        "missing_count": update.missing_count,
        "oldest_published_at": min(published).isoformat() if published else None,
        "newest_published_at": max(published).isoformat() if published else None,
    }
