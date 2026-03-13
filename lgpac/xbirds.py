"""
X/Twitter post tracker.
fetches recent posts via twitter syndication API (no auth needed).
reads tracked users from archs_xbirds/tracked.yml.
invalid accounts are skipped and collected as warnings.
"""
import re
import json
import logging
import time
import yaml
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from lgpac.archive import JsonArchive
from lgpac.notify import send_email, build_html_email

logger = logging.getLogger("lgpac.xbirds")

SYNDICATION_URL = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"
TWEET_URL_TEMPLATE = "https://x.com/{username}/status/{tweet_id}"
TRACKED_FILE = "archs_xbirds/tracked.yml"
ARCHIVE_FILE = "archs_xbirds/archive.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

REQUEST_DELAY = 1.0


# ------------------------------------------------------------------ #
# tracked user management (YAML)
# ------------------------------------------------------------------ #

def load_tracked() -> List[Dict[str, Any]]:
    path = Path(TRACKED_FILE)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, list):
        return []
    return data


def get_usernames() -> List[str]:
    return [u["username"] for u in load_tracked() if u.get("username")]


def add_user(username: str, name: str = "", category: str = "custom", tags: Optional[List[str]] = None):
    tracked = load_tracked()
    if username in {u["username"] for u in tracked}:
        return False
    tracked.append({"username": username, "name": name or username, "category": category, "tags": tags or []})
    _save_tracked(tracked)
    return True


def remove_user(username: str) -> bool:
    tracked = load_tracked()
    before = len(tracked)
    tracked = [u for u in tracked if u["username"] != username]
    if len(tracked) == before:
        return False
    _save_tracked(tracked)
    return True


def _save_tracked(tracked: List[Dict]):
    path = Path(TRACKED_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(tracked, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ------------------------------------------------------------------ #
# fetch posts
# ------------------------------------------------------------------ #

def fetch_user_posts(username: str) -> List[Dict[str, Any]]:
    import requests

    url = SYNDICATION_URL.format(username=username)
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
    except Exception as e:
        logger.debug(f"[@{username}] request error: {e}")
        return []

    return _parse_syndication(resp.text, username)


def _parse_syndication(html: str, username: str) -> List[Dict[str, Any]]:
    pattern = re.compile(r'"__NEXT_DATA__"\s*type="application/json">(.*?)</script>', re.DOTALL)
    m = pattern.search(html)
    if not m:
        return []

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    page_props = data.get("props", {}).get("pageProps", {})
    if not page_props.get("contextProvider", {}).get("hasResults", True):
        return []

    entries = page_props.get("timeline", {}).get("entries", [])
    posts = []
    for entry in entries:
        if entry.get("type") != "tweet":
            continue
        tweet = entry.get("content", {}).get("tweet", {})
        if not tweet:
            continue
        user = tweet.get("user", {})
        tweet_id = tweet.get("id_str", "")
        screen_name = user.get("screen_name", username)
        posts.append({
            "id": tweet_id,
            "username": screen_name,
            "display_name": user.get("name", ""),
            "text": tweet.get("text", ""),
            "created_at": tweet.get("created_at", ""),
            "url": TWEET_URL_TEMPLATE.format(username=screen_name, tweet_id=tweet_id),
            "favorite_count": tweet.get("favorite_count", 0),
        })
    return posts


def fetch_all_users() -> tuple:
    """returns (results_dict, warnings_list)."""
    tracked = load_tracked()
    if not tracked:
        return {}, []

    results = {}
    warnings = []
    total = len(tracked)

    for i, entry in enumerate(tracked):
        username = entry.get("username", "")
        if not username:
            continue
        posts = fetch_user_posts(username)
        if posts:
            results[username] = posts
            logger.debug(f"[{i+1}/{total}] @{username}: {len(posts)} posts")
        else:
            warnings.append({
                "username": username,
                "name": entry.get("name", username),
                "category": entry.get("category", ""),
                "issue": "no_data_or_not_found",
            })
        if i < total - 1:
            time.sleep(REQUEST_DELAY)

    logger.info(f"fetched {len(results)}/{total} users, {len(warnings)} warnings")
    return results, warnings


# ------------------------------------------------------------------ #
# archive (uses shared JsonArchive)
# ------------------------------------------------------------------ #

def check_and_archive(all_posts: Dict[str, List[Dict]]) -> List[Dict]:
    archive = JsonArchive(ARCHIVE_FILE, key_field="tweet_ids")
    archive.load()
    known_ids = set(archive.get("tweet_ids", []))
    now = datetime.now(timezone.utc).isoformat()

    new_posts = []
    for username, posts in all_posts.items():
        for post in posts:
            tid = post["id"]
            if tid and tid not in known_ids:
                new_posts.append(post)
                known_ids.add(tid)

    if new_posts:
        archive.set("tweet_ids", list(known_ids))
        archive.set("last_updated", now)
        archive.set("total_count", len(known_ids))
        archive.save()
        logger.info(f"archived {len(new_posts)} new posts (total: {len(known_ids)})")
    else:
        logger.info("no new posts")

    return new_posts


# ------------------------------------------------------------------ #
# page generation
# ------------------------------------------------------------------ #

STAGE_META = {
    0: ("🔬 Stage 0: Ignition", "raw signal — papers, prototypes, technical breakthroughs"),
    1: ("📡 Stage 1: Momentum", "domain amplification — experts interpreting within their circle"),
    2: ("🚀 Stage 2: Explosion", "cross-industry spread — business analysis, tool reviews, mainstream influencers"),
    3: ("📺 Stage 3: Decay", "mass awareness — media, generalists, popularization"),
    4: ("💀 Stage 4: Fading", "outdated — hustle content, common knowledge"),
}


def generate_page(all_posts: Dict[str, List[Dict]], warnings: List[Dict], output_path: str = "docs_xbirds/index.md"):
    tracked = load_tracked()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    active = len(all_posts)
    total = len(tracked)

    # build username -> entry lookup
    entry_map = {e["username"]: e for e in tracked}

    lines = [
        "# 🐦 X/Twitter Tracker",
        "",
        f"> updated: {now} · {active}/{total} users active",
        "> grouped by **wave_stage** — read top-down to see how information propagates",
        "",
    ]

    # group active posts by wave_stage
    stage_groups = {s: [] for s in range(5)}
    for username, posts in all_posts.items():
        if not posts:
            continue
        entry = entry_map.get(username, {})
        stage = entry.get("wave_stage", 2)
        stage_groups[stage].append((username, entry, posts))

    for stage in range(5):
        title, desc = STAGE_META[stage]
        users_in_stage = stage_groups.get(stage, [])

        lines.append(f"## {title}")
        lines.append(f"> {desc}")
        lines.append("")

        if not users_in_stage:
            lines.append("*no active posts in this stage*")
            lines.append("")
            continue

        lines.append(f"| User | Category | Latest Post | Date | Likes |")
        lines.append(f"|------|----------|------------|------|-------|")

        for username, entry, posts in users_in_stage:
            p = posts[0]
            cat = entry.get("category", "")
            date = parse_tweet_date(p.get("created_at", ""))
            text = p.get("text", "")[:80].replace("|", "∣").replace("\n", " ")
            url = p.get("url", "")
            likes = p.get("favorite_count", 0)
            lines.append(f"| @{username} | {cat} | [{text}]({url}) | {date} | {likes} |")

        lines.append("")

    # warnings
    if warnings:
        lines.append(f"<details><summary>⚠️ warnings ({len(warnings)} accounts)</summary>")
        lines.append("")
        lines.append("| Username | Name | Category | Issue |")
        lines.append("|----------|------|----------|-------|")
        for w in warnings:
            lines.append(f"| @{w['username']} | {w['name']} | {w['category']} | {w['issue']} |")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"xbirds page generated: {path}")


# ------------------------------------------------------------------ #
# email (uses shared notify)
# ------------------------------------------------------------------ #

def send_email_alert(new_posts: List[Dict]) -> bool:
    if not new_posts:
        return False

    tracked = load_tracked()
    entry_map = {e["username"]: e for e in tracked}

    # group by wave_stage
    stage_posts = {s: [] for s in range(5)}
    for p in new_posts:
        username = p.get("username", "")
        entry = entry_map.get(username, {})
        stage = entry.get("wave_stage", 2)
        stage_posts[stage].append(p)

    stage_colors = {0: "#6e40c9", 1: "#1f6feb", 2: "#d29922", 3: "#da3633", 4: "#8b949e"}

    parts = ['<html><body style="font-family:-apple-system,Arial,sans-serif;max-width:700px;margin:0 auto;">']
    parts.append(f'<h2 style="color:#1da1f2;">🐦 {len(new_posts)} new post(s)</h2>')

    for stage in range(5):
        posts = stage_posts.get(stage, [])
        if not posts:
            continue
        title, desc = STAGE_META[stage]
        color = stage_colors[stage]
        parts.append(f'<h3 style="color:{color};margin-top:20px;">{title}</h3>')
        parts.append(f'<p style="color:#666;font-size:13px;">{desc}</p>')
        parts.append('<table style="border-collapse:collapse;width:100%;font-size:14px;">')
        parts.append(
            '<tr style="background:#f6f8fa;">'
            '<th style="padding:6px 8px;text-align:left;">User</th>'
            '<th style="padding:6px 8px;text-align:left;">Post</th>'
            '<th style="padding:6px 8px;text-align:left;">Date</th></tr>'
        )
        for p in posts:
            date = parse_tweet_date(p.get("created_at", ""))
            text = p.get("text", "")[:150]
            url = p.get("url", "#")
            username = p.get("username", "")
            parts.append(
                f'<tr><td style="padding:6px 8px;color:#1da1f2;">@{username}</td>'
                f'<td style="padding:6px 8px;"><a href="{url}" style="color:#1a73e8;text-decoration:none;">{text}</a></td>'
                f'<td style="padding:6px 8px;color:#888;">{date}</td></tr>'
            )
        parts.append('</table>')

    parts.append('</body></html>')
    html = "\n".join(parts)
    return send_email(f"[xbirds] {len(new_posts)} new post(s)", html)


# ------------------------------------------------------------------ #
# run
# ------------------------------------------------------------------ #

def run_monitor(notify: bool = False, page: bool = False) -> tuple:
    all_posts, warnings = fetch_all_users()
    new_posts = check_and_archive(all_posts)

    if page:
        generate_page(all_posts, warnings)

    if new_posts and notify:
        send_email_alert(new_posts)

    return new_posts, warnings


# ------------------------------------------------------------------ #
# helpers
# ------------------------------------------------------------------ #

def parse_tweet_date(raw: str) -> str:
    if not raw:
        return ""
    try:
        dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return raw[:10]
