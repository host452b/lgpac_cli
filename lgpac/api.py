"""
public API endpoint wrappers.

API routing map (discovered from JS bundle analysis):
  buyerApi   = cyy_buyerapi             -> /pub/v1/shows/...
  homeApi    = cyy_gatewayapi/home      -> /pub/v5/layouts, /pub/v3/show_list/search, ...
  showApi    = cyy_gatewayapi/show      -> /pub/v3/show_static_data, /pub/v3/show/sessions, ...
  tradeApi   = cyy_gatewayapi/trade     -> /buyer/v3/payments, ...
  userApi    = cyy_gatewayapi/user      -> /buyer/v3/user, ...
"""
import logging
from typing import Dict, Any, List, Optional

from lgpac.config import SiteConfig
from lgpac.client import ApiClient

logger = logging.getLogger("lgpac.api")


class LgpacApi:
    def __init__(self, client: Optional[ApiClient] = None, config: Optional[SiteConfig] = None):
        self.config = config or SiteConfig()
        self.client = client or ApiClient(self.config)

    # ------------------------------------------------------------------ #
    # homepage / shop
    # ------------------------------------------------------------------ #

    def get_shop_configs(self) -> Dict[str, Any]:
        """shop config: name, avatar, categories, bottom nav, themes."""
        url = self.config.home_url("pub/v5/shop/configs")
        return self.client.get(url)

    def get_homepage_layout(self, page: str = "HOME") -> Dict[str, Any]:
        """full homepage component layout."""
        url = self.config.home_url(f"pub/v5/layouts?page={page}")
        return self.client.get(url)

    # ------------------------------------------------------------------ #
    # categories
    # ------------------------------------------------------------------ #

    def get_backend_categories(self, level: int = 2) -> Dict[str, Any]:
        """all system-level show categories."""
        url = self.config.buyer_url(f"pub/v1/shows/backend_categories?level={level}")
        return self.client.get(url)

    # ------------------------------------------------------------------ #
    # show list / search
    # ------------------------------------------------------------------ #

    def search_shows(
        self,
        page_index: int = 0,
        length: int = 20,
        show_type: str = "",
        city_id: str = "",
    ) -> Dict[str, Any]:
        """paginated show search. returns searchData list + isLastPage flag."""
        params = f"length={length}&pageIndex={page_index}"
        if show_type:
            params += f"&showType={show_type}"
        if city_id:
            params += f"&cityId={city_id}"
        url = self.config.home_url(f"pub/v3/show_list/search?{params}")
        return self.client.get(url)

    def search_all_shows(self, show_type: str = "") -> List[Dict]:
        """iterate through all pages and return the full show list."""
        all_shows = []
        page_index = 0

        while True:
            resp = self.search_shows(
                page_index=page_index,
                length=self.config.default_page_size,
                show_type=show_type,
            )
            data = resp.get("data", {})
            items = data.get("searchData", [])
            all_shows.extend(items)

            is_last = data.get("isLastPage", True)
            if is_last or not items:
                break
            page_index += 1

        logger.info(f"fetched {len(all_shows)} shows (type={show_type or 'all'})")
        return all_shows

    # ------------------------------------------------------------------ #
    # show detail
    # ------------------------------------------------------------------ #

    def get_show_static(self, show_id: str) -> Dict[str, Any]:
        """show static data: name, poster, venue, price range, content URL."""
        url = self.config.show_url(
            f"pub/v3/show_static_data/{show_id}"
            f"?locationCityId={self.config.city_id}&siteId={self.config.site_id}"
        )
        return self.client.get(url)

    def get_show_dynamic(self, show_id: str) -> Dict[str, Any]:
        """show dynamic data: sale status, seat pick type."""
        url = self.config.show_url(
            f"pub/v3/show_dynamic_data/{show_id}"
            f"?locationCityId={self.config.city_id}&siteId={self.config.site_id}"
        )
        return self.client.get(url)

    # ------------------------------------------------------------------ #
    # sessions
    # ------------------------------------------------------------------ #

    def get_sessions_static(self, show_id: str) -> Dict[str, Any]:
        """session list with times, limitations, combo info."""
        url = self.config.show_url(f"pub/v3/show/{show_id}/sessions_static_data")
        return self.client.get(url)

    # ------------------------------------------------------------------ #
    # seat plans (ticket tiers)
    # ------------------------------------------------------------------ #

    def get_seat_plans_static(self, show_id: str, session_id: str) -> Dict[str, Any]:
        """all ticket tiers for a given session: prices, combo packs, availability."""
        url = self.config.show_url(
            f"pub/v3/show/{show_id}/show_session/{session_id}/seat_plans_static_data"
        )
        return self.client.get(url)

    # ------------------------------------------------------------------ #
    # service notes
    # ------------------------------------------------------------------ #

    def get_service_notes(self, show_id: str) -> Dict[str, Any]:
        """refund policy, seat picking info, ticket exchange info."""
        url = self.config.show_url(f"pub/v3/show/{show_id}/service_notes")
        return self.client.get(url)

    # ------------------------------------------------------------------ #
    # show content (HTML description hosted on CDN)
    # ------------------------------------------------------------------ #

    def get_show_content(self, content_url: str) -> Optional[str]:
        """fetch the rich-text show description from CDN."""
        if not content_url:
            return None
        try:
            resp = self.client.session.get(content_url, timeout=self.config.timeout)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            logger.warning(f"failed to fetch content from {content_url}: {e}")
        return None
