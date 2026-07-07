"""Course normalization independent of the live mini-program response shape."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
    price_yuan: str = ""
    course_type: str = ""
    course_start_date: str = ""
    course_end_date: str = ""

    @property
    def identity(self) -> str:
        if self.course_id:
            return self.course_id
        parts = [
            self.title,
            self.course_type,
            self.course_start_date,
            self.course_end_date,
        ]
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
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
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


def _optional_value(item: dict[str, Any], path: str | None) -> Any:
    if not path:
        return None
    try:
        return lookup(item, path)
    except CourseParseError:
        return None


def parse_price_yuan(primary: Any, fallback: Any) -> str:
    raw = primary if primary is not None and primary != "" else fallback
    if raw is None or raw == "":
        return ""
    try:
        cents = Decimal(str(raw))
    except InvalidOperation as exc:
        raise CourseParseError("invalid course price") from exc
    if not cents.is_finite() or cents < 0:
        raise CourseParseError("invalid course price")
    yuan = (cents / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return format(yuan, ".2f")


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
        remaining=_optional(item, settings.remaining_path),
        detail_url=_optional(item, settings.detail_url_path),
        price_yuan=parse_price_yuan(
            _optional_value(item, settings.price_path),
            _optional_value(item, settings.fallback_price_path),
        ),
        course_type=_optional(item, settings.course_type_path),
        course_start_date=_optional(item, settings.start_date_path),
        course_end_date=_optional(item, settings.end_date_path),
    )


def extract_courses(payload: Any, settings: Settings) -> list[Course]:
    if isinstance(payload, dict) and payload.get("error") not in {None, 0, "0"}:
        raise CourseParseError("course API reported an error")

    items = lookup(payload, settings.items_path)
    if not isinstance(items, list) or not items:
        raise CourseParseError("course list is empty or invalid")

    if isinstance(payload, dict) and isinstance(payload.get("pageInfo"), dict):
        total = payload["pageInfo"].get("total")
        if total is not None:
            try:
                total_count = int(total)
            except (TypeError, ValueError) as exc:
                raise CourseParseError("invalid course total") from exc
            if total_count > len(items):
                raise CourseParseError("course response is incomplete")

    courses: list[Course] = []
    for index, item in enumerate(items):
        try:
            courses.append(parse_course(item, settings))
        except CourseParseError as exc:
            logger.warning("skipping course item at index %d: %s", index, exc)
    if not courses:
        raise CourseParseError("no valid courses in response")
    return courses
