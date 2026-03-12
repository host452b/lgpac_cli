"""
lightweight scheduled crawling support.
can be used standalone or integrated with cron/systemd/apscheduler.
"""
import time
import signal
import logging
from typing import Optional, Callable

from lgpac.config import SiteConfig
from lgpac.spider import LgpacSpider
from lgpac.storage import JsonStorage

logger = logging.getLogger("lgpac.scheduler")


class CrawlScheduler:
    """
    run the crawler at a fixed interval.

    usage:
        scheduler = CrawlScheduler(interval_minutes=60)
        scheduler.start()  # blocks, runs every 60 minutes

    for production, prefer cron or systemd timer instead:
        */60 * * * * cd /path/to/project && python main.py crawl
    """

    def __init__(
        self,
        interval_minutes: int = 60,
        config: Optional[SiteConfig] = None,
        on_complete: Optional[Callable] = None,
    ):
        self.interval = interval_minutes * 60
        self.config = config or SiteConfig()
        self.on_complete = on_complete
        self._running = False

    def start(self):
        """start the scheduler loop. blocks until interrupted."""
        self._running = True
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        logger.info(f"scheduler started, interval={self.interval // 60} minutes")

        while self._running:
            self._run_once()
            if not self._running:
                break
            logger.info(f"next crawl in {self.interval // 60} minutes")
            self._interruptible_sleep(self.interval)

        logger.info("scheduler stopped")

    def _run_once(self):
        """execute a single crawl run."""
        try:
            spider = LgpacSpider(config=self.config)
            shows, _diff = spider.crawl_all(fetch_details=True)

            if self.on_complete:
                self.on_complete(shows)

            logger.info(f"scheduled crawl complete: {len(shows)} shows")
        except Exception as e:
            logger.error(f"crawl failed: {e}", exc_info=True)

    def _interruptible_sleep(self, total_seconds: int):
        """sleep in small chunks so we can respond to signals."""
        chunk = 5
        elapsed = 0
        while elapsed < total_seconds and self._running:
            time.sleep(min(chunk, total_seconds - elapsed))
            elapsed += chunk

    def _handle_signal(self, signum, frame):
        logger.info(f"received signal {signum}, shutting down...")
        self._running = False
