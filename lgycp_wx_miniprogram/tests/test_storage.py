import json

import pytest

from lgycp_wx_miniprogram.storage import (
    StorageError,
    empty_archive,
    load_archive,
    save_archive,
)


def test_load_missing_archive_returns_new_schema(tmp_path):
    assert load_archive(tmp_path / "missing.json") == {
        "schema_version": 1,
        "initialized_at": None,
        "courses": {},
    }


def test_save_and_load_archive_round_trip(tmp_path):
    path = tmp_path / "nested" / "archive.json"
    data = empty_archive()
    data["courses"]["course-001"] = {"baseline": True}

    save_archive(path, data)

    assert load_archive(path) == data
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert list(path.parent.glob(".archive.json.*")) == []


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        json.dumps({"schema_version": 2, "courses": {}}),
        json.dumps({"schema_version": 1, "courses": []}),
    ],
)
def test_load_archive_rejects_corruption_or_unknown_schema(tmp_path, content):
    path = tmp_path / "archive.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(StorageError):
        load_archive(path)


def test_failed_replace_does_not_destroy_existing_archive(tmp_path, monkeypatch):
    path = tmp_path / "archive.json"
    original = empty_archive()
    path.write_text(json.dumps(original), encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("disk failure")

    monkeypatch.setattr("lgycp_wx_miniprogram.storage.os.replace", fail_replace)

    changed = empty_archive()
    changed["initialized_at"] = "2026-07-07T11:05:00+08:00"
    with pytest.raises(OSError, match="disk failure"):
        save_archive(path, changed)

    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert list(tmp_path.glob(".archive.json.*")) == []
