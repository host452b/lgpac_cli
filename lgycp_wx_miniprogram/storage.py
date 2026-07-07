"""Versioned JSON archive with atomic replacement."""

import json
import os
from pathlib import Path
import tempfile
from typing import Any


class StorageError(RuntimeError):
    """Raised when persisted state is unreadable or unsupported."""


def empty_archive() -> dict[str, Any]:
    return {"schema_version": 1, "initialized_at": None, "courses": {}}


def load_archive(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_archive()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError(f"cannot read archive: {path.name}") from exc
    if data.get("schema_version") != 1 or not isinstance(data.get("courses"), dict):
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
