from datetime import datetime, timedelta

from lgycp_wx_miniprogram.models import Course, SHANGHAI
from lgycp_wx_miniprogram.monitor import in_recent_window, mark_notified, update_archive
from lgycp_wx_miniprogram.storage import empty_archive


NOW = datetime(2026, 7, 7, 11, 5, tzinfo=SHANGHAI)


def course(course_id: str, published_at: datetime) -> Course:
    return Course(course_id=course_id, title=f"课程 {course_id}", published_at=published_at)


def test_recent_window_is_inclusive_and_rejects_future():
    assert in_recent_window(course("boundary", NOW - timedelta(days=7)), NOW)
    assert not in_recent_window(
        course("too-old", NOW - timedelta(days=7, seconds=1)), NOW
    )
    assert not in_recent_window(course("future", NOW + timedelta(seconds=1)), NOW)


def test_first_run_builds_baseline_without_candidates():
    archive = empty_archive()
    current = course("baseline", NOW - timedelta(days=1))

    candidates, updated = update_archive([current], archive, NOW)

    assert candidates == []
    assert updated["initialized_at"] == NOW.isoformat()
    assert updated["courses"]["baseline"] == {
        "published_at": current.published_at.isoformat(),
        "first_seen_at": NOW.isoformat(),
        "last_seen_at": NOW.isoformat(),
        "baseline": True,
        "notified_at": None,
    }


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
    assert archive["courses"]["new"]["notified_at"] == (
        NOW + timedelta(hours=1)
    ).isoformat()


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
