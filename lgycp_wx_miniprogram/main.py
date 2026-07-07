"""Auditable orchestration for one course-monitor run."""

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import tempfile

from lgpac.notify import EmailDeliveryError
from lgycp_wx_miniprogram.client import (
    ApiError,
    decode_response,
    fetch_response,
)
from lgycp_wx_miniprogram.config import ConfigError, Settings, load_settings
from lgycp_wx_miniprogram.diagnostics import RunDiagnostics
from lgycp_wx_miniprogram.models import (
    SHANGHAI,
    normalize_courses,
    validate_payload,
)
from lgycp_wx_miniprogram.monitor import (
    finalize_success,
    mark_notified,
    update_archive,
)
from lgycp_wx_miniprogram.notify import send_courses
from lgycp_wx_miniprogram.storage import load_archive, save_archive


ARCHIVE_PATH = Path(__file__).parent / "data" / "archive.json"
logger = logging.getLogger("lgycp_wx_miniprogram")

_SAFE_STAGE_MESSAGES = {
    "configuration": "configuration failed",
    "http_fetch": "course API request failed",
    "json_decode": "course API JSON decoding failed",
    "contract_validation": "course API contract validation failed",
    "course_normalization": "course normalization failed",
    "archive_load": "archive load failed",
    "candidate_selection": "candidate selection failed",
    "smtp_delivery": "email delivery failed",
    "archive_save": "archive save failed",
}


def _diagnostics_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    configured = os.environ.get("LGYCP_DIAGNOSTICS_PATH", "").strip()
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "lgycp-wx-diagnostics.json"


def _run_id(started_at: datetime) -> str:
    timestamp = started_at.strftime("%Y%m%dT%H%M%S%z")
    github_run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    return f"{timestamp}-{github_run_id}" if github_run_id else timestamp


def _secret_values(settings: Settings) -> list[str]:
    values = [
        settings.notify_email,
        settings.smtp_user,
        settings.smtp_pass,
        *settings.api_headers.values(),
    ]
    if settings.api_body is not None:
        values.append(json.dumps(settings.api_body, ensure_ascii=False, sort_keys=True))
    return values


def _finished_at(fixed_now: datetime | None) -> datetime:
    return fixed_now or datetime.now(SHANGHAI)


def _complete_failure(
    diagnostics: RunDiagnostics,
    exc: Exception,
    *,
    fixed_now: datetime | None,
) -> int:
    stage = diagnostics.current_stage
    safe_message = _SAFE_STAGE_MESSAGES.get(stage, "monitor run failed")
    if isinstance(exc, ApiError) and exc.trace is not None:
        diagnostics.record_http(exc.trace)
    diagnostics.complete_failure(
        exc,
        safe_message=safe_message,
        finished_at=_finished_at(fixed_now),
    )
    logger.error("%s", safe_message)
    return 1


def _execute(
    settings: Settings,
    diagnostics: RunDiagnostics,
    *,
    run_at: datetime,
    fixed_now: datetime | None,
) -> int:
    try:
        with diagnostics.stage("http_fetch"):
            fetched = fetch_response(settings)
        diagnostics.record_http(fetched.trace)

        with diagnostics.stage("json_decode"):
            decoded = decode_response(fetched)
        payload = decoded.payload

        with diagnostics.stage("contract_validation"):
            contract = validate_payload(payload, settings)
        diagnostics.record_contract(
            payload=payload,
            source_total=contract.source_total,
            list_length=len(contract.items),
            items_path=settings.items_path,
            title_path=settings.title_path,
            published_path=settings.published_path,
        )

        with diagnostics.stage("course_normalization"):
            extraction = normalize_courses(contract, settings)

        with diagnostics.stage("archive_load"):
            archive = load_archive(ARCHIVE_PATH)

        with diagnostics.stage("candidate_selection"):
            update = update_archive(extraction.courses, archive, run_at)

        notified_count = 0
        if update.candidates:
            with diagnostics.stage("smtp_delivery"):
                if not send_courses(update.candidates):
                    raise EmailDeliveryError("email delivery failed")
            mark_notified(update.candidates, archive, run_at)
            notified_count = len(update.candidates)

        finished_at = _finished_at(fixed_now)
        finalize_success(
            archive,
            run_id=diagnostics.data["run_id"],
            started_at=run_at,
            finished_at=finished_at,
            source_total=contract.source_total,
            skipped_invalid_count=extraction.skipped_invalid_count,
            update=update,
            courses=extraction.courses,
            notified_count=notified_count,
        )
        with diagnostics.stage("archive_save"):
            save_archive(ARCHIVE_PATH, archive)

        summary = archive["last_run"]
        counts = {
            key: value
            for key, value in summary.items()
            if key.endswith("_count") or key == "source_total"
        }
        counts["archive_course_count"] = len(archive["courses"])
        diagnostics.complete_success(finished_at=finished_at, counts=counts)
        logger.info(
            "checked %d courses; notified %d",
            len(extraction.courses),
            notified_count,
        )
        return 0
    except Exception as exc:
        return _complete_failure(diagnostics, exc, fixed_now=fixed_now)


def run(
    settings: Settings,
    now: datetime | None = None,
    diagnostics_path: Path | None = None,
) -> int:
    run_at = now or datetime.now(SHANGHAI)
    diagnostics = RunDiagnostics(
        path=_diagnostics_path(diagnostics_path),
        run_id=_run_id(run_at),
        started_at=run_at,
        secret_values=_secret_values(settings),
    )
    with diagnostics.stage("configuration"):
        pass
    return _execute(
        settings,
        diagnostics,
        run_at=run_at,
        fixed_now=now,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run_at = datetime.now(SHANGHAI)
    diagnostics = RunDiagnostics(
        path=_diagnostics_path(),
        run_id=_run_id(run_at),
        started_at=run_at,
    )
    try:
        with diagnostics.stage("configuration"):
            settings = load_settings()
    except ConfigError as exc:
        diagnostics.complete_failure(
            exc,
            safe_message="configuration failed",
            finished_at=datetime.now(SHANGHAI),
        )
        logger.error("configuration failed")
        return 2
    return _execute(
        settings,
        diagnostics,
        run_at=run_at,
        fixed_now=None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
