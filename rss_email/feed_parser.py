"""
rss feed parser — async parallel fetcher with OPML support.
adapted from kylejohnston/rss-email-digest.
"""
import re
import time
import asyncio
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timedelta, timezone

import aiohttp
import feedparser

logger = logging.getLogger(__name__)


def parse_opml(opml_path):
    """parse OPML file, return list of {title, url, html_url, category}."""
    path = Path(opml_path)
    if not path.exists():
        raise FileNotFoundError(f"OPML not found: {opml_path}")

    tree = ET.parse(path)
    root = tree.getroot()
    feeds = []

    for folder in root.findall(".//body/outline"):
        # top-level folder (category grouping)
        if folder.findall("outline[@xmlUrl]"):
            category = folder.get("text") or folder.get("title") or ""
            for outline in folder.findall("outline[@xmlUrl]"):
                feeds.append({
                    "title": outline.get("text") or outline.get("title") or "",
                    "url": outline.get("xmlUrl"),
                    "html_url": outline.get("htmlUrl", ""),
                    "category": category,
                })
        # flat outline (no folder nesting)
        elif folder.get("xmlUrl"):
            feeds.append({
                "title": folder.get("text") or folder.get("title") or "",
                "url": folder.get("xmlUrl"),
                "html_url": folder.get("htmlUrl", ""),
                "category": "",
            })

    return feeds


def _is_within_window(date_value, cutoff):
    """check if a parsed date is newer than cutoff."""
    if date_value is None:
        return False

    if isinstance(date_value, time.struct_time):
        date_value = datetime(*date_value[:6], tzinfo=timezone.utc)

    if date_value.tzinfo is None:
        date_value = date_value.replace(tzinfo=timezone.utc)

    return date_value >= cutoff


def _strip_html(text):
    """remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text).strip()


MAX_RETRIES = 2


async def _fetch_once(session, url, timeout):
    """single fetch attempt, returns response text."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; rss-digest/1.0)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    async with session.get(
        url, timeout=aiohttp.ClientTimeout(total=timeout), headers=headers,
        allow_redirects=True, ssl=False,
    ) as resp:
        return await resp.text()


async def fetch_feed(name, url, cutoff, timeout=20, html_url=""):
    """fetch a single RSS feed with retry, filter posts by cutoff datetime."""
    last_error = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with aiohttp.ClientSession() as session:
                content = await _fetch_once(session, url, timeout)

            feed = feedparser.parse(content)

            if feed.bozo and not feed.entries:
                last_error = f"invalid feed: {feed.bozo_exception}"
                break

            site_url = ""
            if hasattr(feed, "feed"):
                site_url = feed.feed.get("link", "")

            posts = []
            for entry in feed.entries:
                pub_date = (
                    getattr(entry, "published_parsed", None)
                    or getattr(entry, "updated_parsed", None)
                )
                if not _is_within_window(pub_date, cutoff):
                    continue

                excerpt = ""
                if hasattr(entry, "summary"):
                    excerpt = entry.summary
                elif hasattr(entry, "content"):
                    excerpt = entry.content[0].value
                excerpt = _strip_html(excerpt)
                if len(excerpt) > 300:
                    excerpt = excerpt[:300] + "..."

                posts.append({
                    "title": getattr(entry, "title", "(no title)"),
                    "link": getattr(entry, "link", ""),
                    "excerpt": excerpt,
                })

            status = "ok" if posts else "no_updates"
            return {
                "name": name, "status": status, "posts": posts,
                "site_url": site_url or html_url,
            }

        except asyncio.TimeoutError:
            last_error = f"timeout after {timeout}s"
            logger.warning(f"{name}: {last_error} (attempt {attempt}/{MAX_RETRIES})")
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.warning(f"{name}: {last_error} (attempt {attempt}/{MAX_RETRIES})")

        if attempt < MAX_RETRIES:
            await asyncio.sleep(2 * attempt)

    return {
        "name": name, "status": "error", "posts": [],
        "error": last_error, "site_url": html_url,
    }


async def fetch_all_feeds(feeds, hours=24, batch_size=10, timeout=15):
    """
    fetch all feeds in parallel batches.
    returns list of result dicts with keys: name, status, posts, error, site_url.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    results = []

    logger.info(f"fetching {len(feeds)} feeds (cutoff={hours}h, batch={batch_size})")

    for i in range(0, len(feeds), batch_size):
        batch = feeds[i:i + batch_size]
        tasks = [
            fetch_feed(f["title"], f["url"], cutoff, timeout, f.get("html_url", ""))
            for f in batch
        ]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for j, result in enumerate(batch_results):
            if isinstance(result, Exception):
                feed = batch[j]
                logger.error(f"{feed['title']}: unexpected: {result}")
                results.append({
                    "name": feed["title"], "status": "error", "posts": [],
                    "error": str(result), "site_url": feed.get("html_url", ""),
                })
            else:
                results.append(result)

    ok = sum(1 for r in results if r["status"] == "ok")
    err = sum(1 for r in results if r["status"] == "error")
    logger.info(f"done: {ok} with posts, {err} errors, {len(results) - ok - err} no updates")
    return results
