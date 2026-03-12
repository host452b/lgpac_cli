"""
data persistence layer.
supports JSON file output and optional diff tracking.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

from lgpac.models import Show, ShopConfig

logger = logging.getLogger("lgpac.storage")


class JsonStorage:
    def __init__(self, output_dir: str = "data"):
        self.base_dir = Path(output_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _timestamp_dir(self) -> Path:
        """create a timestamped sub-directory for each crawl run."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        d = self.base_dir / ts
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_shows(self, shows: List[Show], run_dir: Optional[Path] = None) -> Path:
        """save all shows as a single JSON file."""
        if run_dir is None:
            run_dir = self._timestamp_dir()

        output = [s.to_dict() for s in shows]
        filepath = run_dir / "shows.json"
        self._write_json(filepath, output)
        logger.info(f"saved {len(shows)} shows to {filepath}")
        return filepath

    def save_shop_config(self, config: ShopConfig, run_dir: Optional[Path] = None) -> Path:
        if run_dir is None:
            run_dir = self._timestamp_dir()

        filepath = run_dir / "shop_config.json"
        data = {
            "shop_name": config.shop_name,
            "shop_color": config.shop_color,
            "shop_avatar": config.shop_avatar,
            "intro": config.intro,
            "icp_license": config.icp_license,
            "frontend_categories": [
                {"id": c.biz_id, "name": c.name, "codes": c.category_codes}
                for c in config.frontend_categories
            ],
            "bottom_navigations": config.bottom_navigations,
        }
        self._write_json(filepath, data)
        return filepath

    def save_raw(self, name: str, data: Any, run_dir: Optional[Path] = None) -> Path:
        """save arbitrary data as JSON."""
        if run_dir is None:
            run_dir = self._timestamp_dir()
        filepath = run_dir / f"{name}.json"
        self._write_json(filepath, data)
        return filepath

    def save_latest_symlink(self, run_dir: Path):
        """create/update a 'latest' symlink pointing to the most recent run."""
        latest = self.base_dir / "latest"
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(run_dir.name)
        logger.info(f"updated latest -> {run_dir.name}")

    def load_latest_shows(self) -> List[Dict]:
        """load shows from the latest crawl run."""
        latest = self.base_dir / "latest" / "shows.json"
        if not latest.exists():
            return []
        return self._read_json(latest)

    def diff_shows(self, old_shows: List[Dict], new_shows: List[Dict]) -> Dict[str, Any]:
        """compute changes between two crawl runs."""
        old_map = {s["show_id"]: s for s in old_shows}
        new_map = {s["show_id"]: s for s in new_shows}

        added = [new_map[sid] for sid in new_map if sid not in old_map]
        removed = [old_map[sid] for sid in old_map if sid not in new_map]

        changed = []
        for sid in set(old_map) & set(new_map):
            diffs = self._compare_show(old_map[sid], new_map[sid])
            if diffs:
                changed.append({"show_id": sid, "name": new_map[sid]["name"], "changes": diffs})

        return {
            "added": added,
            "removed": removed,
            "changed": changed,
            "summary": {
                "added_count": len(added),
                "removed_count": len(removed),
                "changed_count": len(changed),
            },
        }

    def _compare_show(self, old: Dict, new: Dict) -> List[Dict]:
        """compare two show dicts and return a list of field changes."""
        watch_fields = ["name", "show_date", "status", "sold_out", "session_count"]
        diffs = []
        for field_name in watch_fields:
            old_val = old.get(field_name)
            new_val = new.get(field_name)
            if old_val != new_val:
                diffs.append({
                    "field": field_name,
                    "old": old_val,
                    "new": new_val,
                })

        old_min = old.get("price", {}).get("min", 0)
        new_min = new.get("price", {}).get("min", 0)
        if old_min != new_min:
            diffs.append({"field": "min_price", "old": old_min, "new": new_min})

        return diffs

    @staticmethod
    def _write_json(filepath: Path, data: Any):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _read_json(filepath: Path) -> Any:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
