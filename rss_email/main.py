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
import time
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from feed_parser import parse_opml, fetch_all_feeds
from email_gen import build_message, send_email, STAGE_META

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


def _log_stage_summary(feeds, results):
    """log per-stage breakdown for observability."""
    stage_feeds = defaultdict(int)
    for f in feeds:
        stage_feeds[f.get("wave_stage", -1)] += 1

    stage_ok = defaultdict(int)
    stage_err = defaultdict(int)
    stage_posts = defaultdict(int)
    for r in results:
        s = r.get("wave_stage", -1)
        if r["status"] == "ok":
            stage_ok[s] += 1
            stage_posts[s] += len(r["posts"])
        elif r["status"] == "error":
            stage_err[s] += 1

    logger.info("--- stage breakdown ---")
    for stage in sorted(stage_feeds.keys()):
        if stage < 0:
            continue
        meta = STAGE_META.get(stage, {"icon": "?", "label": f"Stage {stage}"})
        total = stage_feeds[stage]
        ok = stage_ok[stage]
        posts = stage_posts[stage]
        err = stage_err[stage]
        logger.info(f"  {meta['icon']} s{stage} {meta['label']:10s}: "
                     f"{ok}/{total} feeds, {posts} posts"
                     f"{f', {err} errors' if err else ''}")


def _write_github_summary(feeds, results, hours, elapsed):
    """write markdown to $GITHUB_STEP_SUMMARY if running in Actions."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    total_posts = sum(len(r["posts"]) for r in results)
    with_posts = [r for r in results if r["posts"]]
    failed = [r for r in results if r["status"] == "error"]

    lines = [
        f"### RSS Digest Summary",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Feeds | {len(feeds)} |",
        f"| Window | {hours}h |",
        f"| Active | {len(with_posts)} |",
        f"| Posts | {total_posts} |",
        f"| Errors | {len(failed)} |",
        f"| Elapsed | {elapsed:.1f}s |",
        "",
        "#### By Stage",
        "| Stage | Feeds | Posts | Errors |",
        "|-------|-------|-------|--------|",
    ]

    stage_groups = defaultdict(list)
    for r in results:
        stage_groups[r.get("wave_stage", -1)].append(r)

    for stage in sorted(stage_groups.keys()):
        if stage < 0:
            continue
        items = stage_groups[stage]
        meta = STAGE_META.get(stage, {"icon": "?", "label": f"Stage {stage}"})
        ok = sum(1 for r in items if r["posts"])
        posts = sum(len(r["posts"]) for r in items)
        err = sum(1 for r in items if r["status"] == "error")
        lines.append(f"| {meta['icon']} {meta['label']} | {ok}/{len(items)} | {posts} | {err} |")

    if failed:
        lines.append("")
        lines.append("#### Errors")
        for r in failed:
            lines.append(f"- **{r['name']}**: {r.get('error', 'unknown')}")

    try:
        with open(summary_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


async def run(opml_path, hours, dry_run):
    """main workflow: parse -> fetch -> email."""
    if not Path(opml_path).exists():
        logger.error(f"OPML not found: {opml_path}")
        logger.info("create rss_email/feeds.opml with your RSS feeds")
        sys.exit(1)

    # parse
    feeds = parse_opml(opml_path)
    logger.info(f"parsed {len(feeds)} feeds from {opml_path}")
    if not feeds:
        logger.info("no feeds in OPML, nothing to do")
        return

    # log feed distribution before fetching
    stage_dist = defaultdict(int)
    for f in feeds:
        stage_dist[f.get("wave_stage", -1)] += 1
    dist_str = "  ".join(f"s{s}={c}" for s, c in sorted(stage_dist.items()) if s >= 0)
    logger.info(f"distribution: {dist_str}")

    # fetch
    start = time.time()
    results = await fetch_all_feeds(feeds, hours=hours, batch_size=10, timeout=20)
    elapsed = time.time() - start

    # per-stage summary
    with_posts = [r for r in results if r["posts"]]
    total_posts = sum(len(r["posts"]) for r in results)
    errors = [r for r in results if r["status"] == "error"]

    logger.info(f"result: {len(with_posts)} feeds active, {total_posts} posts, "
                f"{len(errors)} errors ({elapsed:.1f}s)")
    _log_stage_summary(feeds, results)
    _write_github_summary(feeds, results, hours, elapsed)

    if dry_run:
        logger.info("--- DRY RUN (no email) ---")
        stage_groups = defaultdict(list)
        for r in results:
            stage_groups[r.get("wave_stage", -1)].append(r)

        for stage in sorted(stage_groups.keys()):
            items = stage_groups[stage]
            active = [r for r in items if r["posts"]]
            if not active:
                continue
            meta = STAGE_META.get(stage, {"icon": "?", "label": f"Stage {stage}"})
            print(f"\n{meta['icon']} Stage {stage}: {meta['label']}")
            print(f"{'─' * 50}")
            for r in active:
                cat = r.get("feed_category", "")
                tag = f"[{cat}] " if cat else ""
                print(f"  {tag}{r['name']}:")
                for p in r["posts"]:
                    print(f"    - {p['title']}")
                    print(f"      {p['link']}")
            print()

        print(f"summary: {len(with_posts)}/{len(results)} feeds, "
              f"{total_posts} posts, {len(errors)} errors")
        return

    if not with_posts and not errors:
        logger.info("no updates and no errors, skipping email")
        return

    smtp = _get_smtp_config()
    if smtp["missing"]:
        logger.error(f"missing env vars: {', '.join(smtp['missing'])}")
        sys.exit(1)

    msg = build_message(results, smtp["user"], smtp["to"], hours=hours)
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
