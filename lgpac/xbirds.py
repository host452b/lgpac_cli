"""
X/Twitter post tracker.
uses twitter's public syndication embed API (same endpoint browsers use
to render embedded timelines — no auth, no API key, no scraping).
reads tracked users from archs_xbirds/tracked.yml.
"""
import re
import json
import logging
import time
import random
import yaml
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from lgpac.archive import JsonArchive
from lgpac.notify import send_email, build_html_email

logger = logging.getLogger("lgpac.xbirds")

# twitter's public embed endpoint — the same URL browsers hit when
# rendering an embedded timeline widget. no authentication required.
# this is NOT a private/undocumented API; it serves the public embed JS.
SYNDICATION_URL = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"
TWEET_URL_TEMPLATE = "https://x.com/{username}/status/{tweet_id}"
TRACKED_FILE = "archs_xbirds/tracked.yml"
ARCHIVE_FILE = "archs_xbirds/archive.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

REQUEST_DELAY_MIN = 0.8
REQUEST_DELAY_MAX = 1.5
RECENT_HOURS = 24
MAX_RETRIES = 2


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
# fetch posts (polite, with retry and jitter)
# ------------------------------------------------------------------ #

# fetch result status codes
FETCH_OK = "ok"             # got posts
FETCH_EMPTY = "empty"       # account exists but 0 posts (or protected)
FETCH_NOT_FOUND = "404"     # account does not exist
FETCH_RATE_LIMITED = "429"  # rate limited after retries
FETCH_ERROR = "error"       # network/server/parse error


def fetch_user_posts(username: str) -> tuple:
    """
    fetch recent posts via the public syndication embed endpoint.
    returns (status, posts_list).
    status is one of: FETCH_OK, FETCH_EMPTY, FETCH_NOT_FOUND, FETCH_RATE_LIMITED, FETCH_ERROR.
    """
    import requests

    url = SYNDICATION_URL.format(username=username)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    last_status = FETCH_ERROR
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=15)

            if resp.status_code == 404:
                return FETCH_NOT_FOUND, []
            if resp.status_code == 429:
                last_status = FETCH_RATE_LIMITED
                wait = 5 * attempt
                logger.warning(f"[@{username}] rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                last_status = FETCH_ERROR
                logger.debug(f"[@{username}] server error {resp.status_code}, retrying")
                time.sleep(2 * attempt)
                continue

            resp.raise_for_status()
            posts = _parse_syndication(resp.text, username)
            if posts:
                return FETCH_OK, posts
            return FETCH_EMPTY, []

        except requests.Timeout:
            last_status = FETCH_ERROR
            logger.debug(f"[@{username}] timeout (attempt {attempt})")
            time.sleep(2)
        except requests.ConnectionError:
            last_status = FETCH_ERROR
            logger.debug(f"[@{username}] connection error (attempt {attempt})")
            time.sleep(3)
        except Exception as e:
            last_status = FETCH_ERROR
            logger.debug(f"[@{username}] error: {e}")
            break

    return last_status, []


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


# ------------------------------------------------------------------ #
# connectivity preflight check
# ------------------------------------------------------------------ #

DRYRUN_USERNAME = "karpathy"


def preflight_check() -> bool:
    """
    verify connectivity to twitter syndication before full crawl.
    tests with a known-active account. logs detailed diagnostics on failure.
    """
    import requests

    url = SYNDICATION_URL.format(username=DRYRUN_USERNAME)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    logger.info(f"preflight: testing connectivity via @{DRYRUN_USERNAME}")

    try:
        resp = requests.get(url, headers=headers, timeout=15)
    except requests.Timeout:
        logger.error("preflight FAILED: connection timed out after 15s. "
                      "possible network issue or DNS block.")
        return False
    except requests.ConnectionError as e:
        logger.error(f"preflight FAILED: connection error. "
                      f"syndication.twitter.com may be unreachable. detail: {e}")
        return False
    except Exception as e:
        logger.error(f"preflight FAILED: unexpected error: {type(e).__name__}: {e}")
        return False

    if resp.status_code == 429:
        logger.warning("preflight: rate limited (429). shared IP may be throttled. "
                        "will proceed with individual requests (they may still work).")
        return False

    if resp.status_code == 403:
        logger.error("preflight FAILED: forbidden (403). "
                      "syndication API may have added auth requirements or geo-block.")
        return False

    if resp.status_code >= 500:
        logger.error(f"preflight FAILED: server error ({resp.status_code}). "
                      "twitter syndication service may be down.")
        return False

    if resp.status_code != 200:
        logger.error(f"preflight FAILED: unexpected status {resp.status_code}. "
                      f"headers: {dict(resp.headers)}")
        return False

    # verify response contains expected data structure
    if "__NEXT_DATA__" not in resp.text:
        logger.error("preflight FAILED: response is 200 but missing __NEXT_DATA__. "
                      "twitter may have changed the embed page structure. "
                      f"content-type: {resp.headers.get('content-type', 'unknown')}, "
                      f"body length: {len(resp.text)}")
        return False

    posts = _parse_syndication(resp.text, DRYRUN_USERNAME)
    if not posts:
        logger.warning("preflight WARNING: page parsed but 0 posts extracted. "
                        "JSON structure may have changed.")
        return True

    logger.info(f"preflight OK: {len(posts)} posts from @{DRYRUN_USERNAME}, "
                 f"latest: {posts[0].get('created_at', 'unknown')[:16]}")
    return True


# ------------------------------------------------------------------ #
# fetch all users (sequential, polite)
# ------------------------------------------------------------------ #

def fetch_all_users(recent_hours: int = RECENT_HOURS) -> tuple:
    """
    fetch posts for all tracked users sequentially with random jitter.
    filters to posts within recent_hours (default 24h).
    returns (results_dict, warnings_list).
    """
    tracked = load_tracked()
    if not tracked:
        return {}, []

    if not preflight_check():
        logger.warning("preflight failed — continuing anyway (may have partial results)")
        time.sleep(5)

    results = {}
    warnings = []
    total = len(tracked)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=recent_hours)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M UTC")

    # stage distribution for logging
    from collections import Counter
    stage_dist = Counter(e.get("wave_stage", -1) for e in tracked)
    stage_summary = " ".join(f"s{s}={c}" for s, c in sorted(stage_dist.items()))

    logger.info(f"fetching {total} users, cutoff={recent_hours}h ({cutoff_str})")
    logger.info(f"stage distribution: {stage_summary}")

    success_count = 0
    empty_count = 0
    error_count = 0
    consecutive_429 = 0
    start_time = time.time()

    for i, entry in enumerate(tracked):
        username = entry.get("username", "")
        if not username:
            continue

        # if we hit consecutive 429s, do a long cooldown before continuing
        if consecutive_429 >= 3:
            cooldown = 30 + consecutive_429 * 10
            logger.warning(f"  ⏸  {consecutive_429} consecutive 429s — cooling down {cooldown}s")
            time.sleep(cooldown)
            consecutive_429 = 0

        status, posts = fetch_user_posts(username)
        stage = entry.get("wave_stage", "?")

        if status == FETCH_OK:
            consecutive_429 = 0
            recent = _filter_recent(posts, cutoff)
            if recent:
                results[username] = recent
                success_count += 1
                logger.info(f"  [{i+1}/{total}] ✅ @{username} (s{stage}): {len(recent)} recent posts")
            else:
                empty_count += 1
                logger.info(f"  [{i+1}/{total}] ⏭  @{username} (s{stage}): {len(posts)} posts, 0 in window")
        elif status == FETCH_EMPTY:
            consecutive_429 = 0
            empty_count += 1
            logger.info(f"  [{i+1}/{total}] 📭 @{username} (s{stage}): account exists, 0 posts")
        elif status == FETCH_NOT_FOUND:
            consecutive_429 = 0
            error_count += 1
            logger.info(f"  [{i+1}/{total}] 🚫 @{username} (s{stage}): account not found (404)")
            warnings.append({"username": username, "name": entry.get("name", username),
                             "category": entry.get("category", ""), "issue": "account_not_found"})
        elif status == FETCH_RATE_LIMITED:
            consecutive_429 += 1
            error_count += 1
            logger.info(f"  [{i+1}/{total}] 🚦 @{username} (s{stage}): rate limited (429)")
            warnings.append({"username": username, "name": entry.get("name", username),
                             "category": entry.get("category", ""), "issue": "rate_limited"})
        else:
            consecutive_429 += 1
            error_count += 1
            logger.info(f"  [{i+1}/{total}] ❌ @{username} (s{stage}): fetch error")
            warnings.append({"username": username, "name": entry.get("name", username),
                             "category": entry.get("category", ""), "issue": "fetch_error"})

        # summary progress every 10 users
        if (i + 1) % 10 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (total - i - 1) / rate if rate > 0 else 0
            pct = (i + 1) * 100 // total
            logger.info(
                f"  --- {pct}% ({i+1}/{total}) "
                f"✅{success_count} ⏭{empty_count} ❌{error_count} "
                f"| {elapsed:.0f}s elapsed, ~{eta:.0f}s remaining ---"
            )

        # polite delay with random jitter — longer after errors
        if i < total - 1:
            base_delay = REQUEST_DELAY_MIN if consecutive_429 == 0 else 3.0
            delay = random.uniform(base_delay, base_delay + 1.0)
            time.sleep(delay)

    elapsed_total = time.time() - start_time
    logger.info(
        f"fetch complete: {total} users in {elapsed_total:.0f}s "
        f"({success_count} active, {empty_count} no-recent, {error_count} errors, {len(warnings)} warnings)"
    )
    return results, warnings


def _filter_recent(posts: List[Dict], cutoff: datetime) -> List[Dict]:
    """keep only posts newer than cutoff. drops posts with unparseable dates."""
    recent = []
    for p in posts:
        raw = p.get("created_at", "")
        if not raw:
            continue
        try:
            dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
            if dt >= cutoff:
                recent.append(p)
        except ValueError:
            pass
    return recent


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
# page generation (grouped by wave_stage)
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

    entry_map = {e["username"]: e for e in tracked}

    lines = [
        "# 🐦 X/Twitter Tracker",
        "",
        f"> updated: {now} · {active}/{total} users active",
        "> grouped by **wave_stage** — read top-down to see how information propagates",
        "",
    ]

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
# email (grouped by wave_stage)
# ------------------------------------------------------------------ #

def send_email_alert(new_posts: List[Dict]) -> bool:
    if not new_posts:
        return False

    tracked = load_tracked()
    entry_map = {e["username"]: e for e in tracked}

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

def run_monitor(notify: bool = False, page: bool = False, recent_hours: int = RECENT_HOURS) -> tuple:
    logger.info(f"=== xbirds run_monitor start (hours={recent_hours}, notify={notify}, page={page}) ===")

    all_posts, warnings = fetch_all_users(recent_hours=recent_hours)

    # summarize what we got per stage
    tracked = load_tracked()
    entry_map = {e["username"]: e for e in tracked}
    stage_active = {}
    for username in all_posts:
        s = entry_map.get(username, {}).get("wave_stage", -1)
        stage_active.setdefault(s, []).append(username)
    for s in sorted(stage_active):
        users = stage_active[s]
        logger.info(f"  stage {s}: {len(users)} active users")

    new_posts = check_and_archive(all_posts)
    logger.info(f"  new posts to archive: {len(new_posts)}")

    if page:
        generate_page(all_posts, warnings)

    if new_posts and notify:
        send_email_alert(new_posts)
        logger.info(f"  email sent with {len(new_posts)} posts")
    elif notify:
        logger.info("  no new posts, email skipped")

    logger.info(f"=== xbirds run_monitor complete ===")
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
