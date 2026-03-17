#!/usr/bin/env python3
"""
rss email digest — daily summary of RSS feed updates.
adapted from kylejohnston/rss-email-digest.

reuses LGPAC_SMTP_* env vars so the same GitHub Secrets work.

usage:
    python rss_email/main.py                   # default: last 24h
    python rss_email/main.py --hours 48        # custom lookback
    python rss_email/main.py --dry-run         # fetch + print, no email
    python rss_email/main.py --opml path.opml  # custom OPML file
"""
import os
import sys
import asyncio
import argparse
import logging
from pathlib import Path

# allow running from project root: `python rss_email/main.py`
sys.path.insert(0, str(Path(__file__).parent))

from feed_parser import parse_opml, fetch_all_feeds
from email_gen import build_message, send_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DEFAULT_OPML = Path(__file__).parent / "feeds.opml"


def _get_smtp_config():
    """read SMTP config from env vars (same as lgpac/notify.py)."""
    to_addr = os.environ.get("LGPAC_NOTIFY_EMAIL", "").strip()
    smtp_user = os.environ.get("LGPAC_SMTP_USER", "").strip()
    smtp_pass = os.environ.get("LGPAC_SMTP_PASS", "").strip()
    smtp_server = os.environ.get("LGPAC_SMTP_SERVER", "smtp.qq.com")
    smtp_port = int(os.environ.get("LGPAC_SMTP_PORT", "465"))

    missing = []
    if not to_addr:
        missing.append("LGPAC_NOTIFY_EMAIL")
    if not smtp_user:
        missing.append("LGPAC_SMTP_USER")
    if not smtp_pass:
        missing.append("LGPAC_SMTP_PASS")

    return {
        "to": to_addr,
        "user": smtp_user,
        "pass": smtp_pass,
        "server": smtp_server,
        "port": smtp_port,
        "missing": missing,
    }


async def run(opml_path, hours, dry_run):
    """main workflow: parse -> fetch -> email."""
    if not Path(opml_path).exists():
        logger.error(f"OPML not found: {opml_path}")
        logger.info("create rss_email/feeds.opml with your RSS feeds")
        sys.exit(1)

    feeds = parse_opml(opml_path)
    logger.info(f"parsed {len(feeds)} feeds from {opml_path}")

    if not feeds:
        logger.info("no feeds in OPML, nothing to do")
        return

    results = await fetch_all_feeds(feeds, hours=hours, batch_size=10, timeout=15)

    with_posts = [r for r in results if r["posts"]]
    total_posts = sum(len(r["posts"]) for r in results)
    errors = [r for r in results if r["status"] == "error"]

    logger.info(f"result: {len(with_posts)} feeds active, {total_posts} posts, {len(errors)} errors")

    if dry_run:
        logger.info("--- DRY RUN (no email) ---")
        for r in sorted(results, key=lambda x: x["name"].lower()):
            if r["posts"]:
                print(f"\n{r['name']}:")
                for p in r["posts"]:
                    print(f"  - {p['title']}")
                    print(f"    {p['link']}")
        if not with_posts:
            print("\nno updates found in the lookback window.")
        print(f"\nsummary: {len(with_posts)}/{len(results)} feeds, {total_posts} posts, {len(errors)} errors")
        return

    if not with_posts and not errors:
        logger.info("no updates and no errors, skipping email")
        return

    smtp = _get_smtp_config()
    if smtp["missing"]:
        logger.error(f"missing env vars: {', '.join(smtp['missing'])}")
        sys.exit(1)

    msg = build_message(results, smtp["user"], smtp["to"])
    send_email(msg, smtp["server"], smtp["port"], smtp["user"], smtp["pass"])
    logger.info("done")


def main():
    parser = argparse.ArgumentParser(description="rss email digest")
    parser.add_argument("--opml", default=str(DEFAULT_OPML), help="OPML feed file")
    parser.add_argument("--hours", type=int, default=24, help="lookback window (default: 24)")
    parser.add_argument("--dry-run", action="store_true", help="fetch only, no email")
    args = parser.parse_args()

    asyncio.run(run(args.opml, args.hours, args.dry_run))


if __name__ == "__main__":
    main()
