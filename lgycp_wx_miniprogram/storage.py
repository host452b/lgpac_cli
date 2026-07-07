"""Versioned JSON archive with atomic replacement."""

import copy
import json
import os
from pathlib import Path
import tempfile
from typing import Any


class StorageError(RuntimeError):
    """Raised when persisted state is unreadable or unsupported."""


COURSE_BUSINESS_DEFAULTS = {
    "course_name": "",
    "price_yuan": "",
    "course_type": "",
    "course_start_date": "",
    "course_end_date": "",
}


def empty_archive() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "initialized_at": None,
        "last_success_at": None,
        "last_run": None,
        "courses": {},
    }


def _validate_courses(data: dict[str, Any]) -> None:
    courses = data.get("courses")
    if not isinstance(courses, dict) or not all(
        isinstance(record, dict) for record in courses.values()
    ):
        raise StorageError("unsupported archive structure")


def migrate_v1(data: dict[str, Any]) -> dict[str, Any]:
    _validate_courses(data)
    migrated = copy.deepcopy(data)
    migrated["schema_version"] = 2
    migrated["last_success_at"] = None
    migrated["last_run"] = None
    for record in migrated["courses"].values():
        for key, default in COURSE_BUSINESS_DEFAULTS.items():
            record.setdefault(key, default)
    return migrated


def load_archive(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_archive()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError(f"cannot read archive: {path.name}") from exc
    if not isinstance(data, dict):
        raise StorageError("unsupported archive structure")
    if data.get("schema_version") == 1:
        return migrate_v1(data)
    if data.get("schema_version") != 2:
        raise StorageError("unsupported archive structure")
    _validate_courses(data)
    if "last_success_at" not in data or "last_run" not in data:
        raise StorageError("unsupported archive structure")
    return data


def save_archive(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
