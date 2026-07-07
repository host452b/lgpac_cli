from datetime import datetime, timedelta
import logging

from lgycp_wx_miniprogram.client import ApiError
from lgycp_wx_miniprogram.models import Course, CourseParseError, SHANGHAI
from lgycp_wx_miniprogram.storage import empty_archive
from lgycp_wx_miniprogram.tests.test_models import settings
import lgycp_wx_miniprogram.main as main_module


NOW = datetime(2026, 7, 7, 11, 5, tzinfo=SHANGHAI)
COURSE = Course(
    course_id="course-001",
    title="少儿绘画",
    published_at=NOW - timedelta(hours=1),
)


def patch_successful_source(monkeypatch, courses=None, archive=None):
    monkeypatch.setattr(main_module, "fetch_payload", lambda _: {"payload": True})
    monkeypatch.setattr(
        main_module, "extract_courses", lambda payload, configured: courses or [COURSE]
    )
    monkeypatch.setattr(
        main_module, "load_archive", lambda path: archive or empty_archive()
    )


def test_run_does_not_save_when_fetch_fails(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "fetch_payload",
        lambda _: (_ for _ in ()).throw(ApiError("HTTP 401")),
    )
    saves = []
    monkeypatch.setattr(main_module, "save_archive", lambda *args: saves.append(args))

    assert main_module.run(settings(), now=NOW) == 1
    assert saves == []


def test_run_does_not_save_when_payload_is_unusable(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_payload", lambda _: {})
    monkeypatch.setattr(
        main_module,
        "extract_courses",
        lambda *_: (_ for _ in ()).throw(CourseParseError("no valid courses")),
    )
    saves = []
    monkeypatch.setattr(main_module, "save_archive", lambda *args: saves.append(args))

    assert main_module.run(settings(), now=NOW) == 1
    assert saves == []


def test_first_run_saves_baseline_without_sending(monkeypatch):
    patch_successful_source(monkeypatch)
    sent = []
    saved = []
    monkeypatch.setattr(main_module, "send_courses", lambda *args: sent.append(args))
    monkeypatch.setattr(
        main_module, "save_archive", lambda path, data: saved.append(data)
    )

    assert main_module.run(settings(), now=NOW) == 0
    assert sent == []
    assert saved[0]["courses"]["course-001"]["baseline"] is True
    assert saved[0]["courses"]["course-001"]["notified_at"] is None


def test_new_course_is_marked_only_after_successful_email(monkeypatch):
    archive = empty_archive()
    archive["initialized_at"] = (NOW - timedelta(days=1)).isoformat()
    patch_successful_source(monkeypatch, archive=archive)
    sent = []
    saved = []
    monkeypatch.setattr(
        main_module,
        "send_courses",
        lambda courses, configured: sent.append(courses) or True,
    )
    monkeypatch.setattr(
        main_module, "save_archive", lambda path, data: saved.append(data)
    )

    assert main_module.run(settings(), now=NOW) == 0
    assert sent == [[COURSE]]
    assert saved[0]["courses"]["course-001"]["notified_at"] == NOW.isoformat()


def test_email_failure_returns_error_without_saving_notification(monkeypatch):
    archive = empty_archive()
    archive["initialized_at"] = (NOW - timedelta(days=1)).isoformat()
    patch_successful_source(monkeypatch, archive=archive)
    saves = []
    monkeypatch.setattr(main_module, "send_courses", lambda *args: False)
    monkeypatch.setattr(main_module, "save_archive", lambda *args: saves.append(args))

    assert main_module.run(settings(), now=NOW) == 1
    assert archive["courses"]["course-001"]["notified_at"] is None
    assert saves == []


def test_existing_course_updates_last_seen_and_saves_without_email(monkeypatch):
    archive = empty_archive()
    archive["initialized_at"] = (NOW - timedelta(days=2)).isoformat()
    archive["courses"]["course-001"] = {
        "published_at": COURSE.published_at.isoformat(),
        "first_seen_at": (NOW - timedelta(days=2)).isoformat(),
        "last_seen_at": (NOW - timedelta(days=1)).isoformat(),
        "baseline": True,
        "notified_at": None,
    }
    patch_successful_source(monkeypatch, archive=archive)
    saved = []
    monkeypatch.setattr(
        main_module,
        "send_courses",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not send")),
    )
    monkeypatch.setattr(
        main_module, "save_archive", lambda path, data: saved.append(data)
    )

    assert main_module.run(settings(), now=NOW) == 0
    assert saved[0]["courses"]["course-001"]["last_seen_at"] == NOW.isoformat()


def test_storage_failure_returns_error(monkeypatch, caplog):
    patch_successful_source(monkeypatch)
    monkeypatch.setattr(
        main_module,
        "save_archive",
        lambda *args: (_ for _ in ()).throw(OSError("disk failure")),
    )

    with caplog.at_level(logging.ERROR):
        assert main_module.run(settings(), now=NOW) == 1

    assert "archive save failed" in caplog.text
