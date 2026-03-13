"""
twitter/X post tracker.
fetches recent posts from tracked users via twitter syndication API (no auth needed).
archives posts and notifies on new ones.
"""
import re
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

logger = logging.getLogger("lgpac.xbirds")

SYNDICATION_URL = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"
TWEET_URL_TEMPLATE = "https://x.com/{username}/status/{tweet_id}"
ARCHIVE_FILE = "archs_xbirds/archive.json"
TRACKED_USERS_FILE = "archs_xbirds/users.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def load_tracked_users() -> List[str]:
    """load the list of tracked usernames from users.json."""
    path = Path(TRACKED_USERS_FILE)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("users", [])
    except Exception:
        return []


def save_tracked_users(users: List[str]):
    path = Path(TRACKED_USERS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"users": users}, f, ensure_ascii=False, indent=2)


def fetch_user_posts(username: str) -> List[Dict[str, Any]]:
    """fetch recent posts for a single user via syndication API."""
    import requests

    url = SYNDICATION_URL.format(username=username)
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"[@{username}] fetch failed: {e}")
        return []

    return _parse_syndication(resp.text, username)


def _parse_syndication(html: str, username: str) -> List[Dict[str, Any]]:
    """extract tweet data from syndication page's __NEXT_DATA__ JSON."""
    pattern = re.compile(r'"__NEXT_DATA__"\s*type="application/json">(.*?)</script>', re.DOTALL)
    m = pattern.search(html)
    if not m:
        logger.warning(f"[@{username}] no __NEXT_DATA__ found in response")
        return []

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        logger.warning(f"[@{username}] failed to parse JSON")
        return []

    entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])

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
            "is_retweet": "retweeted_status" in tweet,
            "favorite_count": tweet.get("favorite_count", 0),
            "retweet_count": tweet.get("conversation_count", 0),
        })

    logger.info(f"[@{username}] fetched {len(posts)} posts")
    return posts


def fetch_all_users() -> Dict[str, List[Dict]]:
    """fetch posts for all tracked users."""
    users = load_tracked_users()
    if not users:
        logger.info("no tracked users configured")
        return {}

    result = {}
    for username in users:
        posts = fetch_user_posts(username)
        result[username] = posts
    return result


def check_and_archive(all_posts: Dict[str, List[Dict]]) -> List[Dict]:
    """compare against archive, return only NEW posts, update archive."""
    archive = _load_archive()
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
        archive["tweet_ids"] = list(known_ids)
        archive["last_updated"] = now
        archive["total_count"] = len(known_ids)
        _save_archive(archive)
        logger.info(f"archived {len(new_posts)} new posts (total: {len(known_ids)})")
    else:
        logger.info("no new posts")

    return new_posts


def generate_page(all_posts: Dict[str, List[Dict]], output_path: str = "docs_xbirds/index.md"):
    """generate a markdown page listing recent posts per user."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    users = load_tracked_users()

    lines = []
    lines.append("# 🐦 X/Twitter Tracker")
    lines.append("")
    lines.append(f"> updated: {now} · tracking {len(users)} user(s)")
    lines.append("")

    for username in users:
        posts = all_posts.get(username, [])
        lines.append(f"## @{username}")
        lines.append("")

        if not posts:
            lines.append("*no posts fetched*")
            lines.append("")
            continue

        lines.append("| # | Date | Post | Likes |")
        lines.append("|---|------|------|-------|")
        for i, p in enumerate(posts[:20], 1):
            date = _parse_tweet_date(p.get("created_at", ""))
            text = p.get("text", "")[:100].replace("|", "∣").replace("\n", " ")
            url = p.get("url", "")
            likes = p.get("favorite_count", 0)
            rt = " 🔁" if p.get("is_retweet") else ""
            lines.append(f"| {i} | {date} | [{text}]({url}){rt} | {likes} |")
        lines.append("")

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"xbirds page generated: {path}")


def send_email(new_posts: List[Dict]) -> bool:
    """send email for new posts."""
    import smtplib
    from email.mime.text import MIMEText

    to_addr = os.environ.get("LGPAC_NOTIFY_EMAIL", "").strip()
    smtp_user = os.environ.get("LGPAC_SMTP_USER", "").strip()
    smtp_pass = os.environ.get("LGPAC_SMTP_PASS", "").strip()
    smtp_server = os.environ.get("LGPAC_SMTP_SERVER", "smtp.qq.com")
    smtp_port = int(os.environ.get("LGPAC_SMTP_PORT", "465"))

    if not to_addr or not smtp_user or not smtp_pass:
        return False
    if not new_posts:
        return False

    rows = []
    for p in new_posts:
        date = _parse_tweet_date(p.get("created_at", ""))
        text = p.get("text", "")[:150]
        url = p.get("url", "#")
        username = p.get("username", "")
        rows.append(
            f'<tr><td style="padding:6px 8px;color:#1da1f2;">@{username}</td>'
            f'<td style="padding:6px 8px;">'
            f'<a href="{url}" style="color:#1a73e8;text-decoration:none;">{text}</a></td>'
            f'<td style="padding:6px 8px;color:#888;">{date}</td></tr>'
        )

    body = (
        '<html><body style="font-family:-apple-system,Arial,sans-serif;max-width:700px;margin:0 auto;">'
        f'<h2 style="color:#1da1f2;">🐦 {len(new_posts)} new post(s)</h2>'
        '<table style="border-collapse:collapse;width:100%;font-size:14px;">'
        '<tr style="background:#f6f8fa;"><th style="padding:6px 8px;text-align:left;">User</th>'
        '<th style="padding:6px 8px;text-align:left;">Post</th>'
        '<th style="padding:6px 8px;text-align:left;">Date</th></tr>'
        + "".join(rows)
        + '</table></body></html>'
    )

    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = f"[xbirds] {len(new_posts)} new post(s)"
    msg["From"] = smtp_user
    msg["To"] = to_addr

    try:
        with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15) as s:
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [to_addr], msg.as_string())
        logger.info("xbirds email: sent successfully")
        return True
    except Exception as e:
        logger.warning(f"xbirds email: send failed - {type(e).__name__}")
        return False


def run_monitor(notify: bool = False, page: bool = False) -> List[Dict]:
    """full pipeline: fetch all users -> archive -> page -> notify."""
    all_posts = fetch_all_users()
    new_posts = check_and_archive(all_posts)

    if page:
        generate_page(all_posts)

    if new_posts and notify:
        send_email(new_posts)

    return new_posts


# ------------------------------------------------------------------ #
# helpers
# ------------------------------------------------------------------ #

def _parse_tweet_date(raw: str) -> str:
    """parse twitter date format to YYYY-MM-DD."""
    if not raw:
        return ""
    try:
        dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return raw[:10]


def _load_archive() -> Dict[str, Any]:
    path = Path(ARCHIVE_FILE)
    if not path.exists():
        return {"tweet_ids": [], "total_count": 0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"tweet_ids": [], "total_count": 0}


def _save_archive(archive: Dict[str, Any]):
    path = Path(ARCHIVE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)
