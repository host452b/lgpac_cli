"""Course normalization independent of the live mini-program response shape."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import logging
from typing import Any
from zoneinfo import ZoneInfo

from lgycp_wx_miniprogram.config import Settings


SHANGHAI = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger("lgycp_wx_miniprogram.models")


class CourseParseError(ValueError):
    """Raised when a payload cannot produce any trustworthy course data."""


@dataclass(frozen=True)
class Course:
    course_id: str | None
    title: str
    published_at: datetime
    campus: str = ""
    term: str = ""
    schedule: str = ""
    price: str = ""
    remaining: str = ""
    detail_url: str = ""

    @property
    def identity(self) -> str:
        if self.course_id:
            return self.course_id
        parts = [self.title, self.campus, self.term, self.schedule]
        canonical = "\x1f".join(" ".join(part.split()).casefold() for part in parts)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lookup(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise CourseParseError(f"missing field path: {path}")
        current = current[part]
    return current


def parse_published_at(value: Any) -> datetime:
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.isdigit()
    ):
        number = float(value)
        if number >= 1_000_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=SHANGHAI)
        except (OverflowError, OSError, ValueError) as exc:
            raise CourseParseError("invalid published_at") from exc

    if not isinstance(value, str) or not value.strip():
        raise CourseParseError("missing published_at")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise CourseParseError("invalid published_at") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def _optional(item: dict[str, Any], path: str | None) -> str:
    if not path:
        return ""
    try:
        value = lookup(item, path)
    except CourseParseError:
        return ""
    return "" if value is None else str(value)


def parse_course(item: Any, settings: Settings) -> Course:
    if not isinstance(item, dict):
        raise CourseParseError("course item must be an object")
    title_value = lookup(item, settings.title_path)
    if title_value is None:
        raise CourseParseError("missing title")
    title = str(title_value).strip()
    if not title:
        raise CourseParseError("missing title")
    return Course(
        course_id=_optional(item, settings.id_path) or None,
        title=title,
        published_at=parse_published_at(lookup(item, settings.published_path)),
        campus=_optional(item, settings.campus_path),
        term=_optional(item, settings.term_path),
        schedule=_optional(item, settings.schedule_path),
        price=_optional(item, settings.price_path),
        remaining=_optional(item, settings.remaining_path),
        detail_url=_optional(item, settings.detail_url_path),
    )


def extract_courses(payload: Any, settings: Settings) -> list[Course]:
    items = lookup(payload, settings.items_path)
    if not isinstance(items, list) or not items:
        raise CourseParseError("course list is empty or invalid")

    courses: list[Course] = []
    for index, item in enumerate(items):
        try:
            courses.append(parse_course(item, settings))
        except CourseParseError as exc:
            logger.warning("skipping course item at index %d: %s", index, exc)
    if not courses:
        raise CourseParseError("no valid courses in response")
    return courses
