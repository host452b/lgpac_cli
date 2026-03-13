"""
generic JSON archive for incremental tracking.
used by monitor, lgycp, and xbirds to persist state across runs.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, Set

logger = logging.getLogger("lgpac.archive")


class JsonArchive:
    """
    simple key-value JSON archive with incremental add/check.

    usage:
        archive = JsonArchive("archs_xbirds/archive.json")
        data = archive.load()
        if not archive.has("some_key"):
            archive.add("some_key", {"url": "...", "found_at": "..."})
        archive.save()
    """

    def __init__(self, path: str, key_field: str = "items"):
        self._path = Path(path)
        self._key_field = key_field
        self._data: Dict[str, Any] = {}

    def load(self) -> Dict[str, Any]:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}
        else:
            self._data = {}
        return self._data

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        self._data[key] = value

    def has(self, key: str) -> bool:
        items = self._data.get(self._key_field, {})
        if isinstance(items, dict):
            return key in items
        if isinstance(items, list):
            return key in items
        return False

    def add(self, key: str, value: Any):
        items = self._data.setdefault(self._key_field, {})
        if isinstance(items, dict):
            items[key] = value

    def keys(self) -> Set[str]:
        items = self._data.get(self._key_field, {})
        if isinstance(items, dict):
            return set(items.keys())
        if isinstance(items, list):
            return set(items)
        return set()

    def add_to_list(self, key: str, value: Any):
        """add a value to a list field (e.g. tweet_ids)."""
        lst = self._data.setdefault(key, [])
        if isinstance(lst, list) and value not in lst:
            lst.append(value)
