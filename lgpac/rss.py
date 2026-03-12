"""
RSS.md incremental feed generator.
each crawl run appends a new entry at the top of RSS.md.
"""
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from lgpac.models import Show

logger = logging.getLogger("lgpac.rss")

HEADER = """# RSS Feed

> auto-updated every 4 hours via GitHub Actions.
> latest entry is at the top.

"""


def update_rss(
    shows: List[Show],
    diff: Optional[Dict[str, Any]] = None,
    rss_path: str = "RSS.md",
    extra_section: str = "",
):
    """prepend a new entry to RSS.md."""
    path = Path(rss_path)
    existing = ""
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        marker = "---"
        idx = raw.find(marker)
        if idx >= 0:
            existing = raw[idx:]
        elif raw.startswith("# RSS"):
            existing = ""
        else:
            existing = raw

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = _build_entry(now, shows, diff, extra_section)

    content = HEADER + entry + "\n" + existing
    path.write_text(content, encoding="utf-8")
    logger.info(f"RSS.md updated: {len(shows)} shows @ {now}")


def _build_entry(timestamp: str, shows: List[Show], diff: Optional[Dict] = None, extra_section: str = "") -> str:
    lines = []
    lines.append(f"## {timestamp}")
    lines.append("")
    lines.append(f"**{len(shows)} shows**")
    lines.append("")

    # show table
    lines.append("| # | Category | Name | Date | Price | Status |")
    lines.append("|---|----------|------|------|-------|--------|")
    for i, s in enumerate(shows, 1):
        cat = s.category.display_name if s.category else ""
        price = s.min_price_info.display if s.min_price_info else ""
        status = "SOLD OUT" if s.sold_out else s.status
        name = s.name.replace("|", "/")
        date = s.show_date
        lines.append(f"| {i} | {cat} | {name} | {date} | {price} | {status} |")

    lines.append("")

    # diff section
    if diff:
        summary = diff.get("summary", {})
        added_count = summary.get("added_count", 0)
        removed_count = summary.get("removed_count", 0)
        changed_count = summary.get("changed_count", 0)

        if added_count or removed_count or changed_count:
            lines.append("### Changes")
            lines.append("")

            for show in diff.get("added", []):
                lines.append(f"- **+** {show.get('name', '?')}")
            for show in diff.get("removed", []):
                lines.append(f"- **-** {show.get('name', '?')}")
            for item in diff.get("changed", []):
                parts = []
                for ch in item.get("changes", []):
                    parts.append(f"{ch['field']}: {ch['old']} → {ch['new']}")
                lines.append(f"- **~** {item.get('name', '?')} ({', '.join(parts)})")
            lines.append("")
        else:
            lines.append("*no changes since last run*")
            lines.append("")
    else:
        lines.append("*first run, no diff available*")
        lines.append("")

    if extra_section:
        lines.append(extra_section)
        lines.append("")

    lines.append("---")
    lines.append("")

    return "\n".join(lines)
