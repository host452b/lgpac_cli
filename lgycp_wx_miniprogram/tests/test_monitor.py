from datetime import datetime, timedelta

from lgycp_wx_miniprogram.models import Course, SHANGHAI
import lgycp_wx_miniprogram.monitor as monitor_module
from lgycp_wx_miniprogram.monitor import in_recent_window, mark_notified, update_archive
from lgycp_wx_miniprogram.storage import empty_archive


NOW = datetime(2026, 7, 7, 11, 5, tzinfo=SHANGHAI)


def course(course_id: str, published_at: datetime) -> Course:
    return Course(
        course_id=course_id,
        title=f"课程 {course_id}",
        published_at=published_at,
        price_yuan="640.00",
        course_type="创客",
        course_start_date="2026-07-15",
        course_end_date="2026-08-15",
    )


def test_recent_window_is_inclusive_and_rejects_future():
    assert in_recent_window(course("boundary", NOW - timedelta(days=7)), NOW)
    assert not in_recent_window(
        course("too-old", NOW - timedelta(days=7, seconds=1)), NOW
    )
    assert not in_recent_window(course("future", NOW + timedelta(seconds=1)), NOW)


def test_first_run_builds_baseline_without_candidates():
    archive = empty_archive()
    current = course("baseline", NOW - timedelta(days=1))

    result = update_archive([current], archive, NOW)
    candidates, updated = result

    assert candidates == []
    assert result.newly_seen_count == 1
    assert result.missing_count == 0
    assert updated["initialized_at"] == NOW.isoformat()
    assert updated["courses"]["baseline"] == {
        "course_name": "课程 baseline",
        "price_yuan": "640.00",
        "course_type": "创客",
        "published_at": current.published_at.isoformat(),
        "course_start_date": "2026-07-15",
        "course_end_date": "2026-08-15",
        "first_seen_at": NOW.isoformat(),
        "last_seen_at": NOW.isoformat(),
        "baseline": True,
        "notified_at": None,
    }
    assert not {
        "coursePicUrl",
        "image_url",
        "registration_time",
        "campus",
    } & updated["courses"]["baseline"].keys()


def test_second_run_returns_only_new_recent_course():
    archive = empty_archive()
    baseline = course("baseline", NOW - timedelta(days=1))
    update_archive([baseline], archive, NOW - timedelta(days=1))
    new_course = course("new", NOW - timedelta(hours=2))

    candidates, updated = update_archive([baseline, new_course], archive, NOW)

    assert candidates == [new_course]
    assert updated["courses"]["baseline"]["baseline"] is True
    assert updated["courses"]["new"]["baseline"] is False
    assert updated["courses"]["new"]["notified_at"] is None


def test_new_course_older_than_seven_days_is_recorded_but_not_notified():
    archive = empty_archive()
    update_archive([], archive, NOW - timedelta(days=1))
    old = course("old", NOW - timedelta(days=8))

    candidates, updated = update_archive([old], archive, NOW)

    assert candidates == []
    assert updated["courses"]["old"]["baseline"] is False


def test_unnotified_course_is_retried_until_marked():
    archive = empty_archive()
    update_archive([], archive, NOW - timedelta(days=1))
    new_course = course("new", NOW - timedelta(hours=2))

    first_candidates, archive = update_archive([new_course], archive, NOW)
    retry_candidates, archive = update_archive(
        [new_course], archive, NOW + timedelta(hours=1)
    )

    assert first_candidates == retry_candidates == [new_course]

    mark_notified(retry_candidates, archive, NOW + timedelta(hours=1))
    final_candidates, archive = update_archive(
        [new_course], archive, NOW + timedelta(days=1)
    )
    assert final_candidates == []
    assert (
        archive["courses"]["new"]["notified_at"]
        == (NOW + timedelta(hours=1)).isoformat()
    )


def test_duplicate_identity_produces_one_candidate():
    archive = empty_archive()
    update_archive([], archive, NOW - timedelta(days=1))
    duplicate = course("same", NOW - timedelta(hours=1))

    candidates, _ = update_archive([duplicate, duplicate], archive, NOW)

    assert candidates == [duplicate]


def test_first_seen_time_never_makes_old_course_eligible():
    archive = empty_archive()
    update_archive([], archive, NOW - timedelta(days=1))
    old = course("old", NOW - timedelta(days=30))

    candidates, updated = update_archive([old], archive, NOW)

    assert updated["courses"]["old"]["first_seen_at"] == NOW.isoformat()
    assert candidates == []


def test_missing_course_is_preserved_without_updating_last_seen():
    archive = empty_archive()
    first_seen = NOW - timedelta(days=1)
    present = course("present", first_seen)
    missing = course("missing", first_seen)
    update_archive([present, missing], archive, first_seen)
    previous_last_seen = archive["courses"]["missing"]["last_seen_at"]

    result = update_archive([present], archive, NOW)

    assert result.missing_count == 1
    assert "missing" in archive["courses"]
    assert archive["courses"]["missing"]["last_seen_at"] == previous_last_seen


def test_existing_course_refreshes_business_fields_only():
    archive = empty_archive()
    original = course("same", NOW - timedelta(days=2))
    update_archive([original], archive, NOW - timedelta(days=1))
    record = archive["courses"]["same"]
    record["notified_at"] = (NOW - timedelta(hours=3)).isoformat()
    first_seen_at = record["first_seen_at"]
    notified_at = record["notified_at"]
    changed = Course(
        course_id="same",
        title="更新后的课程名",
        published_at=NOW - timedelta(hours=1),
        price_yuan="800.00",
        course_type="艺术",
        course_start_date="2026-08-01",
        course_end_date="2026-08-31",
    )

    update_archive([changed], archive, NOW)

    assert archive["courses"]["same"] == {
        "course_name": "更新后的课程名",
        "price_yuan": "800.00",
        "course_type": "艺术",
        "published_at": changed.published_at.isoformat(),
        "course_start_date": "2026-08-01",
        "course_end_date": "2026-08-31",
        "first_seen_at": first_seen_at,
        "last_seen_at": NOW.isoformat(),
        "baseline": True,
        "notified_at": notified_at,
    }


def test_finalize_success_writes_run_summary():
    archive = empty_archive()
    archive["initialized_at"] = NOW.isoformat()
    started_at = datetime(2026, 7, 8, 11, 5, tzinfo=SHANGHAI)
    finished_at = started_at + timedelta(seconds=12)
    courses = [
        course("oldest", datetime(2025, 6, 6, 11, 5, 13, tzinfo=SHANGHAI)),
        course("newest", datetime(2026, 7, 8, 9, 30, tzinfo=SHANGHAI)),
    ]
    result = update_archive(courses, archive, started_at)

    monitor_module.finalize_success(
        archive,
        run_id="run-123",
        started_at=started_at,
        finished_at=finished_at,
        source_total=3,
        skipped_invalid_count=1,
        update=result,
        courses=courses,
        notified_count=1,
    )

    assert archive["last_success_at"] == finished_at.isoformat()
    assert archive["last_run"] == {
        "run_id": "run-123",
        "started_at": "2026-07-08T11:05:00+08:00",
        "finished_at": finished_at.isoformat(),
        "source_total": 3,
        "parsed_count": 2,
        "skipped_invalid_count": 1,
        "newly_seen_count": 2,
        "eligible_count": 1,
        "notified_count": 1,
        "missing_count": 0,
        "oldest_published_at": "2025-06-06T11:05:13+08:00",
        "newest_published_at": "2026-07-08T09:30:00+08:00",
    }
