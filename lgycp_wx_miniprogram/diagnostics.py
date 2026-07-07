"""Structured, sanitized diagnostics for one course-monitor run."""

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
import time
import traceback
from typing import Any, Iterator, Iterable

import requests

from lgycp_wx_miniprogram.client import HttpTrace


STAGES = (
    "configuration",
    "http_fetch",
    "json_decode",
    "contract_validation",
    "course_normalization",
    "archive_load",
    "candidate_selection",
    "smtp_delivery",
    "archive_save",
    "git_commit_push",
)

_SECRET_ENV_NAMES = (
    "LGPAC_NOTIFY_EMAIL",
    "LGPAC_SMTP_USER",
    "LGPAC_SMTP_PASS",
    "LGYCP_WX_API_HEADERS_JSON",
    "LGYCP_WX_API_BODY_JSON",
)
_URL_QUERY = re.compile(r"(https?://[^?\s]+)\?[^\s]+")


class RunDiagnostics:
    def __init__(
        self,
        *,
        path: Path,
        run_id: str,
        started_at: datetime,
        secret_values: Iterable[str] = (),
    ) -> None:
        self.path = Path(path)
        self.started_at = started_at
        environment_secrets = [os.environ.get(name, "") for name in _SECRET_ENV_NAMES]
        self._secret_values = sorted(
            {
                str(value)
                for value in [*environment_secrets, *secret_values]
                if value and len(str(value)) >= 4
            },
            key=len,
            reverse=True,
        )
        self._current_stage: str | None = None
        self.data: dict[str, Any] = {
            "run_id": run_id,
            "status": "running",
            "started_at": started_at.isoformat(),
            "finished_at": None,
            "duration_ms": None,
            "failed_stage": None,
            "exception_type": None,
            "safe_message": None,
            "traceback": None,
            "http": None,
            "contract": None,
            "counts": {},
            "stages": [],
            "runtime": {
                "git_sha": os.environ.get("GITHUB_SHA", ""),
                "python_version": platform.python_version(),
                "requests_version": requests.__version__,
                "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
            },
        }

    @property
    def current_stage(self) -> str | None:
        return self._current_stage

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if name not in STAGES:
            raise ValueError("unknown diagnostics stage")
        started = time.monotonic()
        record = {"name": name, "status": "running", "duration_ms": None}
        self.data["stages"].append(record)
        self._current_stage = name
        try:
            yield
        except BaseException:
            record["status"] = "failed"
            record["duration_ms"] = max(0, round((time.monotonic() - started) * 1000))
            raise
        else:
            record["status"] = "success"
            record["duration_ms"] = max(0, round((time.monotonic() - started) * 1000))
            self._current_stage = None

    def record_http(self, trace: HttpTrace) -> None:
        self.data["http"] = asdict(trace)

    def record_contract(
        self,
        *,
        payload: Any,
        source_total: int,
        list_length: int,
        items_path: str,
        title_path: str,
        published_path: str,
    ) -> None:
        keys = sorted(str(key) for key in payload) if isinstance(payload, dict) else []
        self.data["contract"] = {
            "response_type": "object"
            if isinstance(payload, dict)
            else type(payload).__name__,
            "top_level_keys": keys,
            "source_total": source_total,
            "list_length": list_length,
            "items_path": items_path,
            "title_path": title_path,
            "published_path": published_path,
        }

    def complete_success(
        self, *, finished_at: datetime, counts: dict[str, int]
    ) -> None:
        self.data.update(
            {
                "status": "success",
                "finished_at": finished_at.isoformat(),
                "duration_ms": self._duration_ms(finished_at),
                "counts": dict(counts),
            }
        )
        self._write_outputs()

    def complete_failure(
        self,
        exc: BaseException,
        *,
        safe_message: str,
        finished_at: datetime,
    ) -> None:
        formatted = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        self.data.update(
            {
                "status": "failed",
                "finished_at": finished_at.isoformat(),
                "duration_ms": self._duration_ms(finished_at),
                "failed_stage": self._current_stage,
                "exception_type": type(exc).__name__,
                "safe_message": safe_message,
                "traceback": self._sanitize(formatted),
            }
        )
        self._write_outputs()

    def _duration_ms(self, finished_at: datetime) -> int:
        return max(0, round((finished_at - self.started_at).total_seconds() * 1000))

    def _sanitize(self, value: str) -> str:
        sanitized = _URL_QUERY.sub(r"\1?[REDACTED]", value)
        for secret in self._secret_values:
            sanitized = sanitized.replace(secret, "[REDACTED]")
        return sanitized

    def _sanitized_data(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._sanitize(value)
        if isinstance(value, list):
            return [self._sanitized_data(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._sanitized_data(item) for key, item in value.items()}
        return value

    def _write_outputs(self) -> None:
        try:
            self._write_json()
        except Exception:
            print("diagnostics write failed", file=sys.stderr)
        try:
            self._append_summary()
        except Exception:
            print("diagnostics summary write failed", file=sys.stderr)

    def _write_json(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(
                    self._sanitized_data(self.data),
                    stream,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def _append_summary(self) -> None:
        target = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
        if not target:
            return
        counts = self.data.get("counts") or {}
        lines = [
            "## 临港少年宫课程监控",
            "",
            f"- 状态：{self.data['status']}",
            f"- Run ID：{self.data['run_id']}",
            f"- 耗时：{self.data['duration_ms']} ms",
        ]
        if self.data.get("failed_stage"):
            lines.append(f"- 失败阶段：{self.data['failed_stage']}")
        for name in sorted(counts):
            lines.append(f"- {name}：{counts[name]}")
        with Path(target).open("a", encoding="utf-8") as stream:
            stream.write(self._sanitize("\n".join(lines) + "\n"))
