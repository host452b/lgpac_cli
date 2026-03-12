"""
site configuration and API route mapping.

gateway routing (extracted from JS bundle analysis):
  cyy_buyerapi          -> buyer service
  cyy_gatewayapi/home   -> home service (layouts, search, shop config)
  cyy_gatewayapi/show   -> show service (detail, sessions, seat plans)
  cyy_gatewayapi/trade  -> trade service
  cyy_gatewayapi/user   -> user service
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

TARGET_URL = os.environ.get("LGPAC_TARGET_URL", "http://lgpac.culture.cn")


@dataclass
class SiteConfig:
    base_url: str = TARGET_URL
    city_id: str = "3101"
    site_id: str = ""

    buyer_api: str = "cyy_buyerapi"
    home_api: str = "cyy_gatewayapi/home"
    show_api: str = "cyy_gatewayapi/show"

    request_delay: float = 0.5
    max_retries: int = 3
    timeout: int = 15
    default_page_size: int = 20

    output_dir: str = "data"
    debug: bool = False

    user_agent: str = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
        "Mobile/15E148 Safari/604.1"
    )

    default_headers: Dict[str, str] = field(default_factory=lambda: {
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })

    def url(self, gateway: str, path: str) -> str:
        return f"{self.base_url}/{gateway}/{path.lstrip('/')}"

    def buyer_url(self, path: str) -> str:
        return self.url(self.buyer_api, path)

    def home_url(self, path: str) -> str:
        return self.url(self.home_api, path)

    def show_url(self, path: str) -> str:
        return self.url(self.show_api, path)

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir)
