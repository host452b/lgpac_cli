"""Command-line orchestration for one monitoring run."""

from datetime import datetime
import logging
from pathlib import Path

from lgycp_wx_miniprogram.client import ApiError, fetch_payload
from lgycp_wx_miniprogram.config import ConfigError, Settings, load_settings
from lgycp_wx_miniprogram.models import CourseParseError, SHANGHAI, extract_courses
from lgycp_wx_miniprogram.monitor import mark_notified, update_archive
from lgycp_wx_miniprogram.notify import send_courses
from lgycp_wx_miniprogram.storage import StorageError, load_archive, save_archive


ARCHIVE_PATH = Path(__file__).parent / "data" / "archive.json"
logger = logging.getLogger("lgycp_wx_miniprogram")


def run(settings: Settings, now: datetime | None = None) -> int:
    run_at = now or datetime.now(SHANGHAI)
    try:
        payload = fetch_payload(settings)
        courses = extract_courses(payload, settings)
        archive = load_archive(ARCHIVE_PATH)
    except (ApiError, CourseParseError, StorageError) as exc:
        logger.error("monitor failed: %s", exc)
        return 1

    candidates, updated = update_archive(courses, archive, run_at)
    if candidates:
        if not send_courses(candidates, settings):
            logger.error("email delivery failed")
            return 1
        mark_notified(candidates, updated, run_at)

    try:
        save_archive(ARCHIVE_PATH, updated)
    except (OSError, StorageError) as exc:
        logger.error("archive save failed: %s", exc)
        return 1

    logger.info("checked %d courses; notified %d", len(courses), len(candidates))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        settings = load_settings()
    except ConfigError as exc:
        logger.error("configuration failed: %s", exc)
        return 2
    return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
