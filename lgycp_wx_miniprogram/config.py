"""Environment-backed configuration without persisted credentials."""

from dataclasses import dataclass
import json
import os
from typing import Any


DEFAULT_API_URL = (
    "https://lg-venue.xports.cn/aisports-api/api/training/queryTrainings0103"
)
DEFAULT_API_PARAMS = {
    "channelId": "11",
    "centerId": "32057878",
    "pageNo": "1",
    "pageSize": "999",
    "userLatitude": "",
    "userLongitude": "",
    "serviceId": "",
    "courseAttrId": "",
}


class ConfigError(ValueError):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True)
class Settings:
    api_url: str
    api_method: str
    api_headers: dict[str, str]
    api_params: dict[str, str]
    api_body: dict[str, Any] | None
    timeout_seconds: int
    items_path: str
    id_path: str | None
    title_path: str
    published_path: str
    campus_path: str | None
    term_path: str | None
    schedule_path: str | None
    price_path: str | None
    remaining_path: str | None
    detail_url_path: str | None
    notify_email: str
    smtp_user: str
    smtp_pass: str
    smtp_server: str
    smtp_port: int


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"missing {name}")
    return value


def _optional(name: str) -> str | None:
    return os.environ.get(name, "").strip() or None


def _defaulted(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def _json_object(name: str) -> dict[str, Any] | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid {name}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a JSON object")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError as exc:
        raise ConfigError(f"invalid {name}") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be positive")
    return value


def load_settings() -> Settings:
    headers = _json_object("LGYCP_WX_API_HEADERS_JSON") or {}
    params = _json_object("LGYCP_WX_API_PARAMS_JSON") or DEFAULT_API_PARAMS
    return Settings(
        api_url=_defaulted("LGYCP_WX_API_URL", DEFAULT_API_URL),
        api_method=(os.environ.get("LGYCP_WX_API_METHOD") or "").strip().upper()
        or "GET",
        api_headers={str(key): str(value) for key, value in headers.items()},
        api_params={str(key): str(value) for key, value in params.items()},
        api_body=_json_object("LGYCP_WX_API_BODY_JSON"),
        timeout_seconds=_positive_int("LGYCP_WX_TIMEOUT_SECONDS", 15),
        items_path=_defaulted("LGYCP_WX_ITEMS_PATH", "pageInfo.list"),
        id_path=_defaulted("LGYCP_WX_ID_PATH", "courseId"),
        title_path=_defaulted("LGYCP_WX_TITLE_PATH", "courseName"),
        published_path=_defaulted("LGYCP_WX_PUBLISHED_PATH", "createTime"),
        campus_path=_defaulted("LGYCP_WX_CAMPUS_PATH", "centerName"),
        term_path=_optional("LGYCP_WX_TERM_PATH"),
        schedule_path=_optional("LGYCP_WX_SCHEDULE_PATH"),
        price_path=_optional("LGYCP_WX_PRICE_PATH"),
        remaining_path=_optional("LGYCP_WX_REMAINING_PATH"),
        detail_url_path=_optional("LGYCP_WX_DETAIL_URL_PATH"),
        notify_email=_required("LGPAC_NOTIFY_EMAIL"),
        smtp_user=_required("LGPAC_SMTP_USER"),
        smtp_pass=_required("LGPAC_SMTP_PASS"),
        smtp_server=(os.environ.get("LGPAC_SMTP_SERVER") or "").strip()
        or "smtp.qq.com",
        smtp_port=_positive_int("LGPAC_SMTP_PORT", 465),
    )
