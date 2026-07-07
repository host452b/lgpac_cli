import pytest

from lgycp_wx_miniprogram.config import ConfigError, load_settings


REQUIRED = {
    "LGPAC_NOTIFY_EMAIL": "to@example.com",
    "LGPAC_SMTP_USER": "from@example.com",
    "LGPAC_SMTP_PASS": "secret",
}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(REQUIRED) + [
        "LGYCP_WX_API_METHOD",
        "LGYCP_WX_API_HEADERS_JSON",
        "LGYCP_WX_API_PARAMS_JSON",
        "LGYCP_WX_API_BODY_JSON",
        "LGYCP_WX_TIMEOUT_SECONDS",
        "LGYCP_WX_ID_PATH",
        "LGYCP_WX_CAMPUS_PATH",
        "LGYCP_WX_TERM_PATH",
        "LGYCP_WX_SCHEDULE_PATH",
        "LGYCP_WX_PRICE_PATH",
        "LGYCP_WX_REMAINING_PATH",
        "LGYCP_WX_DETAIL_URL_PATH",
        "LGPAC_SMTP_SERVER",
        "LGPAC_SMTP_PORT",
    ]:
        monkeypatch.delenv(name, raising=False)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize(
    "name",
    [
        "LGPAC_NOTIFY_EMAIL",
        "LGPAC_SMTP_USER",
        "LGPAC_SMTP_PASS",
    ],
)
def test_load_settings_requires_mandatory_values(monkeypatch, name):
    monkeypatch.delenv(name)

    with pytest.raises(ConfigError, match=name):
        load_settings()


@pytest.mark.parametrize("raw", ["not-json", "[]", '"text"'])
def test_load_settings_rejects_invalid_headers_json(monkeypatch, raw):
    monkeypatch.setenv("LGYCP_WX_API_HEADERS_JSON", raw)

    with pytest.raises(ConfigError, match="LGYCP_WX_API_HEADERS_JSON"):
        load_settings()


def test_load_settings_parses_json_and_safe_defaults(monkeypatch):
    monkeypatch.setenv("LGYCP_WX_API_HEADERS_JSON", '{"Authorization":"redacted"}')
    monkeypatch.setenv("LGYCP_WX_API_PARAMS_JSON", '{"pageNo":1}')
    monkeypatch.setenv("LGYCP_WX_API_BODY_JSON", '{"page":1}')
    monkeypatch.setenv("LGYCP_WX_API_METHOD", "")
    monkeypatch.setenv("LGPAC_SMTP_SERVER", "")
    monkeypatch.setenv("LGPAC_SMTP_PORT", "")

    settings = load_settings()

    assert settings.api_method == "GET"
    assert settings.api_headers == {"Authorization": "redacted"}
    assert settings.api_params == {"pageNo": "1"}
    assert settings.api_body == {"page": 1}
    assert settings.timeout_seconds == 15
    assert settings.smtp_server == "smtp.qq.com"
    assert settings.smtp_port == 465


def test_load_settings_treats_whitespace_defaults_as_empty(monkeypatch):
    monkeypatch.setenv("LGYCP_WX_API_METHOD", "   ")
    monkeypatch.setenv("LGPAC_SMTP_SERVER", "   ")

    settings = load_settings()

    assert settings.api_method == "GET"
    assert settings.smtp_server == "smtp.qq.com"


def test_load_settings_reads_optional_field_paths(monkeypatch):
    monkeypatch.setenv("LGYCP_WX_ID_PATH", "courseId")
    monkeypatch.setenv("LGYCP_WX_DETAIL_URL_PATH", "links.detail")

    settings = load_settings()

    assert settings.id_path == "courseId"
    assert settings.detail_url_path == "links.detail"
    assert settings.campus_path == "centerName"


def test_load_settings_uses_verified_public_course_api_defaults(monkeypatch):
    for name in [
        "LGYCP_WX_API_URL",
        "LGYCP_WX_ITEMS_PATH",
        "LGYCP_WX_ID_PATH",
        "LGYCP_WX_TITLE_PATH",
        "LGYCP_WX_PUBLISHED_PATH",
        "LGYCP_WX_CAMPUS_PATH",
    ]:
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.api_url == (
        "https://lg-venue.xports.cn/aisports-api/api/training/queryTrainings0103"
    )
    assert settings.api_params == {
        "channelId": "11",
        "centerId": "32057878",
        "pageNo": "1",
        "pageSize": "999",
        "userLatitude": "",
        "userLongitude": "",
        "serviceId": "",
        "courseAttrId": "",
    }
    assert settings.items_path == "pageInfo.list"
    assert settings.id_path == "courseId"
    assert settings.title_path == "courseName"
    assert settings.published_path == "createTime"
    assert settings.campus_path == "centerName"
