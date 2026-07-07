from datetime import datetime, timedelta
import importlib
import json

import pytest

from lgycp_wx_miniprogram.client import HttpTrace
from lgycp_wx_miniprogram.models import SHANGHAI


STARTED = datetime(2026, 7, 8, 11, 5, tzinfo=SHANGHAI)


def test_diagnostics_records_success_without_payload_values(tmp_path, monkeypatch):
    diagnostics_module = importlib.import_module("lgycp_wx_miniprogram.diagnostics")
    path = tmp_path / "diagnostics.json"
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    diagnostics = diagnostics_module.RunDiagnostics(
        path=path, run_id="run-123", started_at=STARTED
    )

    with diagnostics.stage("http_fetch"):
        pass
    diagnostics.record_http(
        HttpTrace(
            method="GET",
            scheme="https",
            host="lg-venue.xports.cn",
            path="/api/training/queryTrainings0103",
            status_code=200,
            attempts=1,
            elapsed_ms=42,
        )
    )
    diagnostics.record_contract(
        payload={"error": 0, "pageInfo": {"secret": "payload-canary"}},
        source_total=53,
        list_length=53,
        items_path="pageInfo.list",
        title_path="courseName",
        published_path="createTime",
    )
    diagnostics.complete_success(
        finished_at=STARTED + timedelta(seconds=2),
        counts={"parsed_count": 53, "notified_count": 1},
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "success"
    assert data["run_id"] == "run-123"
    assert data["duration_ms"] == 2000
    assert data["http"]["host"] == "lg-venue.xports.cn"
    assert data["contract"]["top_level_keys"] == ["error", "pageInfo"]
    assert data["contract"]["source_total"] == 53
    assert data["counts"] == {"parsed_count": 53, "notified_count": 1}
    assert data["stages"][0]["name"] == "http_fetch"
    assert data["stages"][0]["status"] == "success"
    rendered = path.read_text(encoding="utf-8") + summary.read_text(encoding="utf-8")
    assert "payload-canary" not in rendered


def _raise_sensitive_chain():
    try:
        raise ValueError(
            "transport-canary https://example.test/path?token=query-canary "
            "smtp-user-canary header-json-canary body-json-canary"
        )
    except ValueError as exc:
        raise RuntimeError("wrapper-canary") from exc


def test_diagnostics_failure_keeps_traceback_and_redacts_values(
    tmp_path, monkeypatch
):
    diagnostics_module = importlib.import_module("lgycp_wx_miniprogram.diagnostics")
    path = tmp_path / "diagnostics.json"
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("LGPAC_SMTP_USER", "smtp-user-canary")
    monkeypatch.setenv("LGPAC_SMTP_PASS", "transport-canary")
    monkeypatch.setenv("LGYCP_WX_API_HEADERS_JSON", "header-json-canary")
    monkeypatch.setenv("LGYCP_WX_API_BODY_JSON", "body-json-canary")
    diagnostics = diagnostics_module.RunDiagnostics(
        path=path,
        run_id="run-456",
        started_at=STARTED,
        secret_values=["wrapper-canary"],
    )

    try:
        with diagnostics.stage("smtp_delivery"):
            _raise_sensitive_chain()
    except RuntimeError as exc:
        diagnostics.complete_failure(
            exc,
            safe_message="email delivery failed",
            finished_at=STARTED + timedelta(seconds=3),
        )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "failed"
    assert data["failed_stage"] == "smtp_delivery"
    assert data["exception_type"] == "RuntimeError"
    assert data["safe_message"] == "email delivery failed"
    assert "_raise_sensitive_chain" in data["traceback"]
    assert "The above exception was the direct cause" in data["traceback"]
    rendered = path.read_text(encoding="utf-8") + summary.read_text(encoding="utf-8")
    for canary in [
        "transport-canary",
        "query-canary",
        "smtp-user-canary",
        "header-json-canary",
        "body-json-canary",
        "wrapper-canary",
    ]:
        assert canary not in rendered
    assert "https://example.test/path?[REDACTED]" in data["traceback"]


def test_diagnostics_rejects_unknown_stage(tmp_path):
    diagnostics_module = importlib.import_module("lgycp_wx_miniprogram.diagnostics")
    diagnostics = diagnostics_module.RunDiagnostics(
        path=tmp_path / "diagnostics.json",
        run_id="run-789",
        started_at=STARTED,
    )

    with pytest.raises(ValueError, match="unknown diagnostics stage"):
        with diagnostics.stage("not-a-stage"):
            pass


def test_diagnostics_write_failure_is_non_fatal(tmp_path, capsys):
    diagnostics_module = importlib.import_module("lgycp_wx_miniprogram.diagnostics")
    diagnostics = diagnostics_module.RunDiagnostics(
        path=tmp_path,
        run_id="run-write-failure",
        started_at=STARTED,
    )

    diagnostics.complete_success(finished_at=STARTED, counts={})

    assert "diagnostics write failed" in capsys.readouterr().err
