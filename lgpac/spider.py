"""
main crawler orchestration.
coordinates API calls, assembles complete Show objects, and stores results.
"""
import logging
from datetime import datetime
from typing import List, Optional

from lgpac.config import SiteConfig
from lgpac.client import ApiClient, ApiError
from lgpac.api import LgpacApi
from lgpac.models import Show, Session, SeatPlan, ServiceNote, ShopConfig, Category
from lgpac.storage import JsonStorage

logger = logging.getLogger("lgpac.spider")


class LgpacSpider:
    def __init__(
        self,
        config: Optional[SiteConfig] = None,
        storage: Optional[JsonStorage] = None,
    ):
        self.config = config or SiteConfig()
        self.client = ApiClient(self.config)
        self.api = LgpacApi(self.client, self.config)
        self.storage = storage or JsonStorage(self.config.output_dir)

    # ------------------------------------------------------------------ #
    # full crawl pipeline
    # ------------------------------------------------------------------ #

    def crawl_all(self, fetch_details: bool = True) -> List[Show]:
        """
        complete crawl pipeline:
          1. fetch shop config
          2. fetch all categories
          3. fetch show list (all pages)
          4. for each show, fetch detail + sessions + seat plans + notes
          5. save results and compute diff with previous run
        """
        logger.info(f"starting full crawl of {self.config.base_url}")
        run_dir = self.storage._timestamp_dir()
        timestamp = datetime.now().isoformat()

        # step 1: shop config
        shop_config = self.crawl_shop_config()
        if shop_config:
            self.storage.save_shop_config(shop_config, run_dir)
            logger.info(f"shop: {shop_config.shop_name}")

        # step 2: categories
        categories = self.crawl_categories()
        if categories:
            self.storage.save_raw(
                "categories",
                [{"code": c.code, "name": c.display_name, "key": c.name} for c in categories],
                run_dir,
            )

        # step 3: show list
        shows = self.crawl_show_list()
        logger.info(f"found {len(shows)} shows")

        # step 4: detail enrichment
        if fetch_details:
            for i, show in enumerate(shows):
                logger.info(f"[{i+1}/{len(shows)}] enriching: {show.name}")
                self._enrich_show(show)
                show.crawled_at = timestamp

        # step 5: save and diff
        old_shows = self.storage.load_latest_shows()
        self.storage.save_shows(shows, run_dir)

        diff = None
        if old_shows:
            new_show_dicts = [s.to_dict() for s in shows]
            diff = self.storage.diff_shows(old_shows, new_show_dicts)
            self.storage.save_raw("diff", diff, run_dir)
            self._log_diff(diff)

        self.storage.save_latest_symlink(run_dir)

        logger.info(
            f"crawl complete: {len(shows)} shows, "
            f"{self.client.stats['total_requests']} API requests"
        )
        return shows, diff

    # ------------------------------------------------------------------ #
    # individual crawl steps
    # ------------------------------------------------------------------ #

    def crawl_shop_config(self) -> Optional[ShopConfig]:
        try:
            resp = self.api.get_shop_configs()
            return ShopConfig.from_api(resp.get("data", {}))
        except ApiError as e:
            logger.error(f"failed to fetch shop config: {e}")
            return None

    def crawl_categories(self) -> List[Category]:
        try:
            resp = self.api.get_backend_categories()
            return [Category.from_api(c) for c in resp.get("data", [])]
        except ApiError as e:
            logger.error(f"failed to fetch categories: {e}")
            return []

    def crawl_show_list(self, show_type: str = "") -> List[Show]:
        """fetch all shows from the search API."""
        raw_shows = self.api.search_all_shows(show_type=show_type)
        return [Show.from_search(s) for s in raw_shows]

    def crawl_show_detail(self, show_id: str) -> Optional[Show]:
        """crawl a single show with full detail."""
        try:
            static_resp = self.api.get_show_static(show_id)
            static_data = static_resp.get("data", {})

            show = Show(
                show_id=show_id,
                name=static_data.get("showName", ""),
            )
            show.enrich_from_static(static_data)
            self._enrich_show(show)
            show.crawled_at = datetime.now().isoformat()
            return show
        except ApiError as e:
            logger.error(f"failed to crawl show {show_id}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # enrichment helpers
    # ------------------------------------------------------------------ #

    def _enrich_show(self, show: Show):
        """fetch all supplementary data for a show."""
        self._enrich_static(show)
        self._enrich_dynamic(show)
        self._enrich_sessions(show)
        self._enrich_service_notes(show)

    def _enrich_static(self, show: Show):
        try:
            resp = self.api.get_show_static(show.show_id)
            show.enrich_from_static(resp.get("data", {}))
        except ApiError as e:
            logger.warning(f"static data failed for {show.show_id}: {e}")

    def _enrich_dynamic(self, show: Show):
        try:
            resp = self.api.get_show_dynamic(show.show_id)
            show.enrich_from_dynamic(resp.get("data", {}))
        except ApiError as e:
            logger.warning(f"dynamic data failed for {show.show_id}: {e}")

    def _enrich_sessions(self, show: Show):
        try:
            resp = self.api.get_sessions_static(show.show_id)
            data = resp.get("data", {})
            session_list = data.get("sessionVOs", [])

            for s_data in session_list:
                session = Session.from_api(s_data)
                self._enrich_seat_plans(show.show_id, session)
                show.sessions.append(session)

            show.session_count = len(show.sessions)
        except ApiError as e:
            logger.warning(f"sessions failed for {show.show_id}: {e}")

    def _enrich_seat_plans(self, show_id: str, session: Session):
        try:
            resp = self.api.get_seat_plans_static(show_id, session.session_id)
            data = resp.get("data", {})
            plans = data.get("seatPlans", [])
            session.seat_plans = [SeatPlan.from_api(p) for p in plans]
        except ApiError as e:
            logger.warning(
                f"seat plans failed for {show_id}/{session.session_id}: {e}"
            )

    def _enrich_service_notes(self, show: Show):
        try:
            resp = self.api.get_service_notes(show.show_id)
            notes = resp.get("data", [])
            show.service_notes = [ServiceNote.from_api(n) for n in notes]
        except ApiError as e:
            logger.warning(f"service notes failed for {show.show_id}: {e}")

    # ------------------------------------------------------------------ #
    # diff reporting
    # ------------------------------------------------------------------ #

    @staticmethod
    def _log_diff(diff):
        summary = diff.get("summary", {})
        added = summary.get("added_count", 0)
        removed = summary.get("removed_count", 0)
        changed = summary.get("changed_count", 0)

        if added == 0 and removed == 0 and changed == 0:
            logger.info("no changes since last crawl")
            return

        logger.info(
            f"changes detected: +{added} added, -{removed} removed, ~{changed} changed"
        )
        for show in diff.get("added", []):
            logger.info(f"  + NEW: {show['name']}")
        for show in diff.get("removed", []):
            logger.info(f"  - GONE: {show['name']}")
        for item in diff.get("changed", []):
            logger.info(f"  ~ CHANGED: {item['name']}")
            for ch in item.get("changes", []):
                logger.info(f"      {ch['field']}: {ch['old']} -> {ch['new']}")
