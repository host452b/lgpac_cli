from dataclasses import replace
from datetime import datetime
import json
import logging
from pathlib import Path

import pytest

from lgycp_wx_miniprogram.config import Settings
import lgycp_wx_miniprogram.models as models_module
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
        api_params={},
        api_body=None,
        timeout_seconds=15,
        items_path="pageInfo.list",
        id_path="courseId",
        title_path="courseName",
        published_path="createTime",
        campus_path="centerName",
        term_path=None,
        schedule_path=None,
        price_path="subjectPrice",
        fallback_price_path="price",
        course_type_path="courseTypeName",
        start_date_path="startDate",
        end_date_path="endDate",
        remaining_path=None,
        detail_url_path=None,
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
            course_id="10027282",
            title="小小飞行员（国赛集训）",
            published_at=datetime(2026, 6, 12, 15, 18, 40, tzinfo=SHANGHAI),
            campus="临港青少年活动中心",
            price_yuan="640.00",
            course_type="无人机创客营",
            course_start_date="2026-07-15",
            course_end_date="2026-08-15",
        )
    ]
    assert courses[0].identity == "10027282"
    assert not hasattr(courses[0], "registration_time")
    assert not hasattr(courses[0], "image_url")


@pytest.mark.parametrize(
    ("primary", "fallback", "expected"),
    [
        (64000, 65000, "640.00"),
        (None, 65000, "650.00"),
        (1, None, "0.01"),
        (64000.5, None, "640.01"),
        (None, None, ""),
    ],
)
def test_parse_course_converts_price_cents_exactly(primary, fallback, expected):
    item = {
        "courseId": "price-test",
        "courseName": "价格测试",
        "createTime": "2026-07-06 10:00:00",
    }
    if primary is not None:
        item["subjectPrice"] = primary
    if fallback is not None:
        item["price"] = fallback

    assert parse_course(item, settings()).price_yuan == expected


def test_parse_course_rejects_null_title():
    item = {
        "courseId": "course-null-title",
        "courseName": None,
        "createTime": "2026-07-06 10:00:00",
    }

    with pytest.raises(CourseParseError, match="missing title"):
        parse_course(item, settings())


def test_extract_courses_skips_one_bad_item_and_logs_index(caplog):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["pageInfo"]["list"].insert(0, {"courseName": "缺少时间"})

    with caplog.at_level(logging.WARNING):
        courses = extract_courses(payload, settings())

    assert [course.course_id for course in courses] == ["10027282"]
    assert "index 0" in caplog.text
    assert "缺少时间" not in caplog.text


def test_extract_courses_rejects_empty_or_all_invalid_list():
    with pytest.raises(CourseParseError, match="empty or invalid"):
        extract_courses({"pageInfo": {"list": []}}, settings())
    with pytest.raises(CourseParseError, match="no valid courses"):
        extract_courses(
            {"pageInfo": {"list": [{"courseName": "缺少时间"}]}}, settings()
        )


def test_extract_courses_rejects_api_error_response():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["error"] = 500
    payload["message"] = "internal details must not be logged"

    with pytest.raises(CourseParseError, match="course API reported an error"):
        extract_courses(payload, settings())


def test_extract_courses_rejects_incomplete_page():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["pageInfo"]["total"] = 2

    with pytest.raises(CourseParseError, match="course response is incomplete"):
        extract_courses(payload, settings())


def test_validate_and_normalize_are_separate_diagnostic_steps():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["pageInfo"]["list"].insert(0, {"courseName": "缺少发布时间"})
    payload["pageInfo"]["total"] = 2

    contract = models_module.validate_payload(payload, settings())
    extraction = models_module.normalize_courses(contract, settings())

    assert contract.source_total == 2
    assert contract.top_level_keys == tuple(sorted(payload))
    assert len(contract.items) == 2
    assert len(extraction.courses) == 1
    assert extraction.skipped_invalid_count == 1


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
