from datetime import datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from lgpac.notify import EmailDeliveryError
from lgycp_wx_miniprogram.client import FetchResult, FetchedResponse, HttpTrace
from lgycp_wx_miniprogram.config import ConfigError
from lgycp_wx_miniprogram.models import Course, SHANGHAI
from lgycp_wx_miniprogram.storage import empty_archive
from lgycp_wx_miniprogram.tests.test_models import settings
import lgycp_wx_miniprogram.main as main_module


NOW = datetime(2026, 7, 7, 11, 5, tzinfo=SHANGHAI)
TRACE = HttpTrace(
    method="GET",
    scheme="https",
    host="example.invalid",
    path="/courses",
    status_code=200,
    attempts=1,
    elapsed_ms=12,
)
COURSE = Course(
    course_id="course-001",
    title="少儿绘画",
    published_at=NOW - timedelta(hours=1),
    price_yuan="640.00",
    course_type="美术",
    course_start_date="2026-07-15",
    course_end_date="2026-08-15",
)


def patch_successful_pipeline(monkeypatch, *, courses=None, archive=None):
    normalized = courses if courses is not None else [COURSE]
    source_total = len(normalized)
    monkeypatch.setattr(
        main_module,
        "fetch_response",
        lambda _: FetchedResponse(response=object(), trace=TRACE),
        raising=False,
    )
    monkeypatch.setattr(
        main_module,
        "decode_response",
        lambda _: FetchResult(payload={"pageInfo": {}}, trace=TRACE),
        raising=False,
    )
    monkeypatch.setattr(
        main_module,
        "validate_payload",
        lambda *_: SimpleNamespace(
            items=[{} for _ in normalized],
            source_total=source_total,
            top_level_keys=("pageInfo",),
        ),
        raising=False,
    )
    monkeypatch.setattr(
        main_module,
        "normalize_courses",
        lambda *_: SimpleNamespace(
            courses=normalized,
            skipped_invalid_count=0,
        ),
        raising=False,
    )
    selected_archive = archive if archive is not None else empty_archive()
    monkeypatch.setattr(main_module, "load_archive", lambda _: selected_archive)
    return selected_archive


def diagnostic(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_first_run_saves_v2_baseline_and_success_summary(monkeypatch, tmp_path):
    patch_successful_pipeline(monkeypatch)
    saved = []
    monkeypatch.setattr(
        main_module, "save_archive", lambda path, data: saved.append(data)
    )
    monkeypatch.setattr(
        main_module,
        "send_courses",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not send")),
    )
    diagnostics_path = tmp_path / "diagnostics.json"

    assert main_module.run(settings(), now=NOW, diagnostics_path=diagnostics_path) == 0

    record = saved[0]["courses"]["course-001"]
    assert record["baseline"] is True
    assert record["notified_at"] is None
    assert record["course_name"] == "少儿绘画"
    assert saved[0]["last_success_at"] == NOW.isoformat()
    assert saved[0]["last_run"]["source_total"] == 1
    assert saved[0]["last_run"]["notified_count"] == 0
    assert diagnostic(diagnostics_path)["status"] == "success"


def test_new_recent_course_is_marked_only_after_one_email(monkeypatch, tmp_path):
    archive = empty_archive()
    archive["initialized_at"] = (NOW - timedelta(days=1)).isoformat()
    patch_successful_pipeline(monkeypatch, archive=archive)
    sent = []
    saved = []
    monkeypatch.setattr(
        main_module, "send_courses", lambda courses: sent.append(courses) or True
    )
    monkeypatch.setattr(
        main_module, "save_archive", lambda path, data: saved.append(data)
    )

    assert (
        main_module.run(
            settings(), now=NOW, diagnostics_path=tmp_path / "diagnostics.json"
        )
        == 0
    )

    assert sent == [[COURSE]]
    assert saved[0]["courses"]["course-001"]["notified_at"] == NOW.isoformat()
    assert saved[0]["last_run"]["eligible_count"] == 1
    assert saved[0]["last_run"]["notified_count"] == 1


def test_old_new_course_is_archived_without_email(monkeypatch, tmp_path):
    archive = empty_archive()
    archive["initialized_at"] = (NOW - timedelta(days=1)).isoformat()
    old = Course(
        course_id="old-course",
        title="旧课",
        published_at=NOW - timedelta(days=8),
    )
    patch_successful_pipeline(monkeypatch, courses=[old], archive=archive)
    saved = []
    monkeypatch.setattr(
        main_module,
        "send_courses",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not send")),
    )
    monkeypatch.setattr(
        main_module, "save_archive", lambda path, data: saved.append(data)
    )

    assert (
        main_module.run(
            settings(), now=NOW, diagnostics_path=tmp_path / "diagnostics.json"
        )
        == 0
    )

    assert "old-course" in saved[0]["courses"]
    assert saved[0]["last_run"]["eligible_count"] == 0


def test_email_failure_does_not_save_or_mark_notified(monkeypatch, tmp_path):
    archive = empty_archive()
    archive["initialized_at"] = (NOW - timedelta(days=1)).isoformat()
    patch_successful_pipeline(monkeypatch, archive=archive)
    saves = []
    monkeypatch.setattr(
        main_module,
        "send_courses",
        lambda *_: (_ for _ in ()).throw(EmailDeliveryError("email delivery failed")),
    )
    monkeypatch.setattr(main_module, "save_archive", lambda *args: saves.append(args))
    diagnostics_path = tmp_path / "diagnostics.json"

    assert main_module.run(settings(), now=NOW, diagnostics_path=diagnostics_path) == 1

    assert archive["courses"]["course-001"]["notified_at"] is None
    assert saves == []
    assert diagnostic(diagnostics_path)["failed_stage"] == "smtp_delivery"


@pytest.mark.parametrize(
    ("target", "failed_stage"),
    [
        ("fetch_response", "http_fetch"),
        ("decode_response", "json_decode"),
        ("validate_payload", "contract_validation"),
        ("normalize_courses", "course_normalization"),
        ("load_archive", "archive_load"),
        ("update_archive", "candidate_selection"),
        ("save_archive", "archive_save"),
    ],
)
def test_each_failed_stage_writes_trace_and_returns_error(
    monkeypatch, tmp_path, target, failed_stage
):
    patch_successful_pipeline(monkeypatch)
    saves = []
    original_save = main_module.save_archive
    monkeypatch.setattr(main_module, "save_archive", lambda *args: saves.append(args))

    def fail(*args, **kwargs):
        raise RuntimeError("secret")

    monkeypatch.setattr(main_module, target, fail, raising=False)
    if target != "save_archive":
        monkeypatch.setattr(
            main_module, "save_archive", lambda *args: saves.append(args)
        )
    diagnostics_path = tmp_path / f"{failed_stage}.json"

    assert main_module.run(settings(), now=NOW, diagnostics_path=diagnostics_path) == 1

    data = diagnostic(diagnostics_path)
    assert data["failed_stage"] == failed_stage
    assert "test_main.py" in data["traceback"]
    assert " in fail" in data["traceback"]
    assert "secret" not in diagnostics_path.read_text(encoding="utf-8")
    if target != "save_archive":
        assert saves == []
    else:
        assert original_save is not None


def test_main_configuration_failure_returns_two_with_trace(monkeypatch, tmp_path):
    diagnostics_path = tmp_path / "configuration.json"
    monkeypatch.setenv("LGYCP_DIAGNOSTICS_PATH", str(diagnostics_path))
    monkeypatch.setattr(
        main_module,
        "load_settings",
        lambda: (_ for _ in ()).throw(ConfigError("missing LGPAC_SMTP_PASS")),
    )

    assert main_module.main() == 2

    data = diagnostic(diagnostics_path)
    assert data["failed_stage"] == "configuration"
    assert data["safe_message"] == "configuration failed"
