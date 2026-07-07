from dataclasses import replace
from datetime import datetime
import json
import logging
from pathlib import Path

import pytest

from lgycp_wx_miniprogram.config import Settings
from lgycp_wx_miniprogram.models import (
    Course,
    CourseParseError,
    SHANGHAI,
    extract_courses,
    lookup,
    parse_course,
    parse_published_at,
)


FIXTURE = Path(__file__).parent / "fixtures" / "courses_response.json"


def settings() -> Settings:
    return Settings(
        api_url="https://example.invalid/courses",
        api_method="GET",
        api_headers={},
        api_body=None,
        timeout_seconds=15,
        items_path="data.list",
        id_path="courseId",
        title_path="name",
        published_path="publishTime",
        campus_path="campus",
        term_path="term",
        schedule_path="schedule",
        price_path="price",
        remaining_path="remaining",
        detail_url_path="url",
        notify_email="to@example.com",
        smtp_user="from@example.com",
        smtp_pass="secret",
        smtp_server="smtp.qq.com",
        smtp_port=465,
    )


def test_lookup_reads_nested_dot_path():
    assert lookup({"data": {"list": [1]}}, "data.list") == [1]


def test_lookup_rejects_missing_path_without_exposing_values():
    with pytest.raises(CourseParseError, match="missing field path: data.list"):
        lookup({"token": "sensitive"}, "data.list")


def test_parse_naive_datetime_as_shanghai_time():
    parsed = parse_published_at("2026-07-06 10:00:00")

    assert parsed == datetime(2026, 7, 6, 10, 0, tzinfo=SHANGHAI)


def test_parse_zulu_datetime_converts_to_shanghai_time():
    parsed = parse_published_at("2026-07-06T02:00:00Z")

    assert parsed == datetime(2026, 7, 6, 10, 0, tzinfo=SHANGHAI)


@pytest.mark.parametrize("as_text", [False, True])
def test_parse_unix_seconds_and_milliseconds(as_text):
    expected = datetime(2026, 7, 6, 10, 0, tzinfo=SHANGHAI)
    seconds = int(expected.timestamp())
    for value in (seconds, seconds * 1000):
        candidate = str(value) if as_text else value
        assert parse_published_at(candidate) == expected


@pytest.mark.parametrize("value", [None, "", "not-a-date"])
def test_parse_rejects_missing_or_invalid_datetime(value):
    with pytest.raises(CourseParseError):
        parse_published_at(value)


def test_extract_courses_maps_required_and_optional_fields():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    courses = extract_courses(payload, settings())

    assert courses == [
        Course(
            course_id="course-001",
            title="少儿绘画",
            published_at=datetime(2026, 7, 6, 10, 0, tzinfo=SHANGHAI),
            campus="临港校区",
            term="暑期",
            schedule="周六 10:00",
            price="800",
            remaining="5",
            detail_url="https://example.invalid/course-001",
        )
    ]
    assert courses[0].identity == "course-001"


def test_parse_course_rejects_null_title():
    item = {
        "courseId": "course-null-title",
        "name": None,
        "publishTime": "2026-07-06 10:00:00",
    }

    with pytest.raises(CourseParseError, match="missing title"):
        parse_course(item, settings())


def test_extract_courses_skips_one_bad_item_and_logs_index(caplog):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["data"]["list"].insert(0, {"name": "缺少时间"})

    with caplog.at_level(logging.WARNING):
        courses = extract_courses(payload, settings())

    assert [course.course_id for course in courses] == ["course-001"]
    assert "index 0" in caplog.text
    assert "缺少时间" not in caplog.text


def test_extract_courses_rejects_empty_or_all_invalid_list():
    with pytest.raises(CourseParseError, match="empty or invalid"):
        extract_courses({"data": {"list": []}}, settings())
    with pytest.raises(CourseParseError, match="no valid courses"):
        extract_courses({"data": {"list": [{"name": "缺少时间"}]}}, settings())


def test_identity_fallback_is_stable_and_ignores_published_time():
    base = Course(
        course_id=None,
        title=" 少儿  绘画 ",
        published_at=datetime(2026, 7, 6, 10, 0, tzinfo=SHANGHAI),
        campus="临港校区",
        term="暑期",
        schedule="周六 10:00",
    )

    changed_time = replace(
        base, published_at=datetime(2026, 7, 7, 10, 0, tzinfo=SHANGHAI)
    )

    assert base.identity == changed_time.identity
    assert len(base.identity) == 64
