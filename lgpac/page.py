"""
generate a markdown page for GitHub Pages.
pure markdown tables, rendered natively by GitHub.
"""
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from lgpac.models import Show
from lgpac.monitor import TicketAlert

logger = logging.getLogger("lgpac.page")


def generate_page(
    shows: List[Show],
    alerts: List[TicketAlert],
    max_price: float = 120.0,
    diff: Optional[Dict[str, Any]] = None,
    output_path: str = "docs/index.md",
):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []

    lines.append(f"# 🎭 Show Monitor")
    lines.append("")
    lines.append(f"> updated: {now} · price threshold: ¥{max_price:.0f}")
    lines.append("")

    # --- affordable tickets ---
    lines.append("## 🎫 Affordable Tickets")
    lines.append("")

    if not alerts:
        lines.append(f"*no tickets under ¥{max_price:.0f} at this time*")
    else:
        in_stock = [a for a in alerts if a.status != "sold_out"]
        sold_out = [a for a in alerts if a.status == "sold_out"]

        if in_stock:
            lines.append("| | Category | Name | Date | Prices | Stock | Since |")
            lines.append("|---|----------|------|------|--------|-------|-------|")
            for a in in_stock:
                stock_plans = [p for p in a.plans if p["available"]]
                prices = " / ".join(f"¥{p:.0f}" for p in sorted(set(p_["price"] for p_ in a.plans)))
                stock = f"{len(stock_plans)}/{len(a.plans)}"
                icon = {"new": "🆕", "available": "✅", "back_in_stock": "🔄"}.get(a.status, "✅")
                lines.append(
                    f"| {icon} | {a.category} | {_esc(a.show_name)} "
                    f"| {a.show_date} | {prices} | {stock} | {a.first_seen[:10]} |"
                )
            lines.append("")

        if sold_out:
            lines.append("<details><summary>❌ sold out</summary>")
            lines.append("")
            lines.append("| Category | Name | Date | Prices |")
            lines.append("|----------|------|------|--------|")
            for a in sold_out:
                prices = " / ".join(f"¥{p:.0f}" for p in sorted(set(p_["price"] for p_ in a.plans)))
                lines.append(f"| {a.category} | {_esc(a.show_name)} | {a.show_date} | {prices} |")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    # --- all shows ---
    lines.append("## 📋 All Shows")
    lines.append("")
    lines.append("| # | Category | Name | Date | Price | Venue | Status |")
    lines.append("|---|----------|------|------|-------|-------|--------|")
    for i, s in enumerate(shows, 1):
        cat = s.category.display_name if s.category else ""
        price = s.min_price_info.display if s.min_price_info else ""
        status = "❌ SOLD OUT" if s.sold_out else "✅"
        venue = s.venue_name or ""
        lines.append(f"| {i} | {cat} | {_esc(s.name)} | {s.show_date} | {price} | {venue} | {status} |")
    lines.append("")

    # --- price breakdown per show ---
    lines.append("<details><summary>💰 Price Breakdown</summary>")
    lines.append("")
    for s in shows:
        if not s.sessions:
            continue
        lines.append(f"**{_esc(s.name)}**")
        lines.append("")
        lines.append("| Session | Tier | Price | Combo | Available |")
        lines.append("|---------|------|-------|-------|-----------|")
        for sess in s.sessions:
            for plan in sess.seat_plans:
                combo = "✓" if plan.is_combo else ""
                avail = "✅" if plan.truly_available else "❌"
                lines.append(f"| {sess.name} | {plan.name} | ¥{plan.original_price:.0f} | {combo} | {avail} |")
        lines.append("")
    lines.append("</details>")
    lines.append("")

    # --- changes ---
    lines.append("## 📝 Changes")
    lines.append("")

    if diff:
        added = diff.get("added", [])
        removed = diff.get("removed", [])
        changed = diff.get("changed", [])

        if not added and not removed and not changed:
            lines.append("*no changes since last run*")
        else:
            for s in added:
                lines.append(f"- ➕ **{_esc(s.get('name', '?'))}**")
            for s in removed:
                lines.append(f"- ➖ ~~{_esc(s.get('name', '?'))}~~")
            for c in changed:
                parts = ", ".join(f"`{ch['field']}`: {ch['old']} → {ch['new']}" for ch in c.get("changes", []))
                lines.append(f"- 🔄 {_esc(c.get('name', '?'))} ({parts})")
    else:
        lines.append("*first run*")

    lines.append("")

    # write
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"page generated: {path}")


def _esc(text: str) -> str:
    """escape pipe characters for markdown tables."""
    return text.replace("|", "∣")
