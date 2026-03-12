"""
HTTP client with retry, rate-limiting, and structured logging.
"""
import time
import logging
import requests
from typing import Optional, Dict, Any

from lgpac.config import SiteConfig

logger = logging.getLogger("lgpac.client")


class ApiError(Exception):
    def __init__(self, status_code: int, message: str, path: str):
        self.status_code = status_code
        self.message = message
        self.path = path
        super().__init__(f"[{status_code}] {path}: {message}")


class ApiClient:
    def __init__(self, config: Optional[SiteConfig] = None):
        self.config = config or SiteConfig()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.config.user_agent,
            **self.config.default_headers,
        })
        self._last_request_time = 0.0
        self._request_count = 0

    def _rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self.config.request_delay:
            time.sleep(self.config.request_delay - elapsed)
        self._last_request_time = time.time()

    def get(self, url: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        return self._request("GET", url, params=params)

    def post(self, url: str, json_data: Optional[Dict] = None) -> Dict[str, Any]:
        return self._request("POST", url, json_data=json_data)

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        self._rate_limit()
        last_error = None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                logger.debug(f"[{method}] {url} (attempt {attempt})")
                resp = self.session.request(
                    method, url,
                    params=params,
                    json=json_data,
                    timeout=self.config.timeout,
                )
                self._request_count += 1

                if resp.status_code != 200:
                    raise ApiError(resp.status_code, resp.text[:200], url)

                data = resp.json()
                api_status = data.get("statusCode", 200)
                if api_status != 200:
                    raise ApiError(api_status, data.get("comments", ""), url)

                return data

            except requests.RequestException as e:
                last_error = e
                logger.warning(f"request error (attempt {attempt}): {e}")
                if attempt < self.config.max_retries:
                    time.sleep(2 ** attempt)

        raise last_error or RuntimeError("request failed after retries")

    @property
    def stats(self) -> Dict[str, int]:
        return {"total_requests": self._request_count}
