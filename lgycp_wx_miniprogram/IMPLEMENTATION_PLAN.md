# 临港少年宫小程序新课程邮件订阅 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每天北京时间 11:05 请求一次小程序课程接口，仅将接口上架时间位于最近 7 天且尚未通知的课程汇总成一封邮件。

**Architecture:** `lgycp_wx_miniprogram` 是独立 Python 子项目。HTTP 客户端取得原始 JSON，字段路径适配器将其转换为稳定的 `Course` 模型，监控器完成 7 天过滤与状态迁移，SMTP 层发送成功后才标记通知；GitHub Actions 仅负责定时执行和提交非敏感归档。

**Tech Stack:** Python 3.11、requests、标准库 `dataclasses/zoneinfo/smtplib/json`、pytest、GitHub Actions。

---

## 文件映射

- Create: `lgycp_wx_miniprogram/requirements.txt` — 固定运行和测试依赖。
- Create: `lgycp_wx_miniprogram/__init__.py` — 将目录声明为可从仓库根目录执行的 Python package。
- Create: `lgycp_wx_miniprogram/config.py` — 环境变量解析与校验。
- Create: `lgycp_wx_miniprogram/models.py` — 课程模型、时间解析、字段路径适配和身份指纹。
- Create: `lgycp_wx_miniprogram/client.py` — HTTP 请求、有限重试和安全错误。
- Create: `lgycp_wx_miniprogram/storage.py` — 归档加载和原子保存。
- Create: `lgycp_wx_miniprogram/monitor.py` — 基线、7 天窗口、去重和状态迁移。
- Create: `lgycp_wx_miniprogram/notify.py` — HTML 邮件生成与 SMTP 发送。
- Create: `lgycp_wx_miniprogram/main.py` — 一次运行的编排和退出码。
- Create: `lgycp_wx_miniprogram/data/archive.json` — 初始空归档。
- Create: `lgycp_wx_miniprogram/tests/` — 全离线单元测试和脱敏 fixture。
- Create: `lgycp_wx_miniprogram/README.md` — 抓包输入、配置、运行和故障说明。
- Create: `.github/workflows/lgycp-wx-miniprogram-daily.yml` — 北京时间 11:05 的薄调度入口。
- Modify: `.gitignore` — 忽略本地秘密文件和未脱敏抓包文件。

## 实施前接口检查点

正式连接真实接口前，操作者需要从本人可访问的小程序会话取得请求和响应，并在本地确认以下事实：

- URL、HTTP 方法、JSON 请求体和必要请求头可由 `requests` 重放。
- 响应中存在课程数组、课程名称和课程上架/发布时间。
- 记录课程数组与字段的点分路径，例如 `data.list`、`courseId`、`publishTime`；这些路径通过环境变量注入，不把抓包内容提交到 Git。
- 如果请求依赖无法稳定重放的签名、验证码或访问控制，停止执行并报告阻塞，不扩展为 RPA 或绕过方案。

这一步是部署输入，不产生包含凭据的仓库文件。后续所有测试使用 Task 2 中的脱敏 fixture。

### Task 1: 配置边界与依赖

**Files:**
- Create: `lgycp_wx_miniprogram/__init__.py`
- Create: `lgycp_wx_miniprogram/requirements.txt`
- Create: `lgycp_wx_miniprogram/config.py`
- Create: `lgycp_wx_miniprogram/tests/test_config.py`
- Modify: `.gitignore`

- [ ] **Step 1: 写配置失败测试**

测试必须覆盖：缺少 API URL、缺少三个必需字段路径、非法 JSON 请求头、默认 `GET`、默认 15 秒超时，以及 SMTP 配置缺失。

```python
# lgycp_wx_miniprogram/tests/test_config.py
import pytest

from lgycp_wx_miniprogram.config import ConfigError, load_settings


REQUIRED = {
    "LGYCP_WX_API_URL": "https://example.invalid/courses",
    "LGYCP_WX_ITEMS_PATH": "data.list",
    "LGYCP_WX_TITLE_PATH": "name",
    "LGYCP_WX_PUBLISHED_PATH": "publishTime",
    "LGPAC_NOTIFY_EMAIL": "to@example.com",
    "LGPAC_SMTP_USER": "from@example.com",
    "LGPAC_SMTP_PASS": "secret",
}


def test_load_settings_requires_api_url(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("LGYCP_WX_API_URL")
    with pytest.raises(ConfigError, match="LGYCP_WX_API_URL"):
        load_settings()


def test_load_settings_rejects_invalid_headers_json(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LGYCP_WX_API_HEADERS_JSON", "not-json")
    with pytest.raises(ConfigError, match="LGYCP_WX_API_HEADERS_JSON"):
        load_settings()


def test_load_settings_uses_safe_defaults(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    settings = load_settings()
    assert settings.api_method == "GET"
    assert settings.timeout_seconds == 15
    assert settings.smtp_port == 465
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_config.py -q`

Expected: FAIL，原因是 `config` 模块尚不存在。

- [ ] **Step 3: 写最小配置实现**

实现不可打印请求头、请求体或 SMTP 密码。定义不可变 `Settings`，解析以下变量：

```python
# lgycp_wx_miniprogram/config.py
from dataclasses import dataclass
import json
import os
from typing import Any


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    api_url: str
    api_method: str
    api_headers: dict[str, str]
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


def load_settings() -> Settings:
    headers = _json_object("LGYCP_WX_API_HEADERS_JSON") or {}
    return Settings(
        api_url=_required("LGYCP_WX_API_URL"),
        api_method=(os.environ.get("LGYCP_WX_API_METHOD") or "GET").strip().upper(),
        api_headers={str(k): str(v) for k, v in headers.items()},
        api_body=_json_object("LGYCP_WX_API_BODY_JSON"),
        timeout_seconds=int(os.environ.get("LGYCP_WX_TIMEOUT_SECONDS", "15")),
        items_path=_required("LGYCP_WX_ITEMS_PATH"),
        id_path=os.environ.get("LGYCP_WX_ID_PATH") or None,
        title_path=_required("LGYCP_WX_TITLE_PATH"),
        published_path=_required("LGYCP_WX_PUBLISHED_PATH"),
        campus_path=os.environ.get("LGYCP_WX_CAMPUS_PATH") or None,
        term_path=os.environ.get("LGYCP_WX_TERM_PATH") or None,
        schedule_path=os.environ.get("LGYCP_WX_SCHEDULE_PATH") or None,
        price_path=os.environ.get("LGYCP_WX_PRICE_PATH") or None,
        remaining_path=os.environ.get("LGYCP_WX_REMAINING_PATH") or None,
        detail_url_path=os.environ.get("LGYCP_WX_DETAIL_URL_PATH") or None,
        notify_email=_required("LGPAC_NOTIFY_EMAIL"),
        smtp_user=_required("LGPAC_SMTP_USER"),
        smtp_pass=_required("LGPAC_SMTP_PASS"),
        smtp_server=(os.environ.get("LGPAC_SMTP_SERVER") or "smtp.qq.com").strip(),
        smtp_port=int(os.environ.get("LGPAC_SMTP_PORT") or "465"),
    )
```

`requirements.txt` 写入：

```text
requests>=2.28,<3
pytest>=8,<9
```

`.gitignore` 增加：

```text
lgycp_wx_miniprogram/.env
lgycp_wx_miniprogram/captures/
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_config.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add .gitignore lgycp_wx_miniprogram/__init__.py lgycp_wx_miniprogram/requirements.txt lgycp_wx_miniprogram/config.py lgycp_wx_miniprogram/tests/test_config.py
git commit -m "feat: add miniprogram monitor configuration"
```

### Task 2: 课程模型、字段适配与时间解析

**Files:**
- Create: `lgycp_wx_miniprogram/models.py`
- Create: `lgycp_wx_miniprogram/tests/fixtures/courses_response.json`
- Create: `lgycp_wx_miniprogram/tests/test_models.py`

- [ ] **Step 1: 写字段路径、时间和身份测试**

fixture 使用完全虚构的数据：

```json
{
  "data": {
    "list": [
      {
        "courseId": "course-001",
        "name": "少儿绘画",
        "publishTime": "2026-07-06 10:00:00",
        "campus": "临港校区",
        "term": "暑期",
        "schedule": "周六 10:00",
        "price": 800,
        "remaining": 5,
        "url": "https://example.invalid/course-001"
      }
    ]
  }
}
```

测试断言：点分路径能取得列表；无时区字符串按上海时区解释；ISO `Z` 和秒/毫秒时间戳可解析；缺少上架时间抛出 `CourseParseError`；有 ID 时直接使用 ID，没有 ID 时指纹稳定且不包含上架时间。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_models.py -q`

Expected: FAIL，原因是 `models` 模块尚不存在。

- [ ] **Step 3: 实现模型和解析器**

核心接口固定为：

```python
# lgycp_wx_miniprogram/models.py
from dataclasses import dataclass
from datetime import datetime
import hashlib
import logging
from typing import Any
from zoneinfo import ZoneInfo

from lgycp_wx_miniprogram.config import Settings


SHANGHAI = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger("lgycp_wx_miniprogram.models")


class CourseParseError(ValueError):
    pass


@dataclass(frozen=True)
class Course:
    course_id: str | None
    title: str
    published_at: datetime
    campus: str = ""
    term: str = ""
    schedule: str = ""
    price: str = ""
    remaining: str = ""
    detail_url: str = ""

    @property
    def identity(self) -> str:
        if self.course_id:
            return self.course_id
        parts = [self.title, self.campus, self.term, self.schedule]
        canonical = "\x1f".join(" ".join(p.split()).casefold() for p in parts)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lookup(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise CourseParseError(f"missing field path: {path}")
        current = current[part]
    return current


def parse_published_at(value: Any) -> datetime:
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        number = float(value)
        if number >= 1_000_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, tz=SHANGHAI)
    if not isinstance(value, str) or not value.strip():
        raise CourseParseError("missing published_at")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise CourseParseError("invalid published_at") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def _optional(item: dict[str, Any], path: str | None) -> str:
    if not path:
        return ""
    try:
        value = lookup(item, path)
    except CourseParseError:
        return ""
    return "" if value is None else str(value)


def parse_course(item: Any, settings: Settings) -> Course:
    if not isinstance(item, dict):
        raise CourseParseError("course item must be an object")
    title = str(lookup(item, settings.title_path)).strip()
    if not title:
        raise CourseParseError("missing title")
    return Course(
        course_id=_optional(item, settings.id_path) or None,
        title=title,
        published_at=parse_published_at(lookup(item, settings.published_path)),
        campus=_optional(item, settings.campus_path),
        term=_optional(item, settings.term_path),
        schedule=_optional(item, settings.schedule_path),
        price=_optional(item, settings.price_path),
        remaining=_optional(item, settings.remaining_path),
        detail_url=_optional(item, settings.detail_url_path),
    )


def extract_courses(payload: Any, settings: Settings) -> list[Course]:
    items = lookup(payload, settings.items_path)
    if not isinstance(items, list) or not items:
        raise CourseParseError("course list is empty or invalid")
    courses = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            logger.warning("skipping invalid course item at index %d", index)
            continue
        try:
            courses.append(parse_course(item, settings))
        except CourseParseError as exc:
            logger.warning("skipping course item at index %d: %s", index, exc)
    if not courses:
        raise CourseParseError("no valid courses in response")
    return courses
```

测试还要验证单条坏数据只产生警告并跳过，而全部无效时整批失败。若真实响应需要分页，先确认同一接口的分页参数，再在 `client.py` 中循环请求，不能静默只读第一页。

- [ ] **Step 4: 运行模型测试**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_models.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add lgycp_wx_miniprogram/models.py lgycp_wx_miniprogram/tests/fixtures/courses_response.json lgycp_wx_miniprogram/tests/test_models.py
git commit -m "feat: normalize miniprogram courses"
```

### Task 3: HTTP 客户端和有限重试

**Files:**
- Create: `lgycp_wx_miniprogram/client.py`
- Create: `lgycp_wx_miniprogram/tests/test_client.py`

- [ ] **Step 1: 写 HTTP 行为测试**

使用假的 `Session` 测试：成功返回 JSON；连接异常后重试并成功；HTTP 500 最多重试两次；HTTP 401 不重试；无效 JSON 抛出不包含请求头的 `ApiError`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_client.py -q`

Expected: FAIL，原因是 `client` 模块尚不存在。

- [ ] **Step 3: 实现客户端**

```python
# lgycp_wx_miniprogram/client.py
from typing import Any
import time

import requests

from lgycp_wx_miniprogram.config import Settings


class ApiError(RuntimeError):
    pass


def fetch_payload(settings: Settings, session: requests.Session | None = None) -> Any:
    http = session or requests.Session()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = http.request(
                settings.api_method,
                settings.api_url,
                headers=settings.api_headers,
                json=settings.api_body,
                timeout=settings.timeout_seconds,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise ApiError("course API request failed after retries") from exc
        if response.status_code >= 500 and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        if response.status_code >= 400:
            raise ApiError(f"course API returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError("course API returned invalid JSON") from exc
    raise ApiError("course API failed after retries") from last_error
```

- [ ] **Step 4: 运行客户端测试**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_client.py -q`

Expected: PASS，并验证异常文本不包含测试令牌。

- [ ] **Step 5: 提交**

```bash
git add lgycp_wx_miniprogram/client.py lgycp_wx_miniprogram/tests/test_client.py
git commit -m "feat: fetch miniprogram courses safely"
```

### Task 4: 归档原子存储

**Files:**
- Create: `lgycp_wx_miniprogram/storage.py`
- Create: `lgycp_wx_miniprogram/data/archive.json`
- Create: `lgycp_wx_miniprogram/tests/test_storage.py`

- [ ] **Step 1: 写存储测试**

覆盖不存在文件返回空状态、合法状态往返、非法 JSON 失败且不返回空状态、保存通过同目录临时文件原子替换。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_storage.py -q`

Expected: FAIL，原因是 `storage` 模块尚不存在。

- [ ] **Step 3: 实现存储**

```python
# lgycp_wx_miniprogram/storage.py
import json
import os
from pathlib import Path
import tempfile
from typing import Any


class StorageError(RuntimeError):
    pass


def empty_archive() -> dict[str, Any]:
    return {"schema_version": 1, "initialized_at": None, "courses": {}}


def load_archive(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_archive()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError(f"cannot read archive: {path}") from exc
    if data.get("schema_version") != 1 or not isinstance(data.get("courses"), dict):
        raise StorageError("unsupported archive structure")
    return data


def save_archive(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
```

初始 `archive.json`：

```json
{
  "schema_version": 1,
  "initialized_at": null,
  "courses": {}
}
```

- [ ] **Step 4: 运行存储测试**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_storage.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add lgycp_wx_miniprogram/storage.py lgycp_wx_miniprogram/data/archive.json lgycp_wx_miniprogram/tests/test_storage.py
git commit -m "feat: persist miniprogram course state"
```

### Task 5: 7 天窗口、基线和通知状态机

**Files:**
- Create: `lgycp_wx_miniprogram/monitor.py`
- Create: `lgycp_wx_miniprogram/tests/test_monitor.py`

- [ ] **Step 1: 写状态机边界测试**

使用固定 `now = 2026-07-07 11:05:00+08:00` 覆盖：正好 7 天包含、早一秒排除、未来时间排除；首次运行全部标记基线且无候选；第二次出现新且合格课程产生候选；旧课程不重复；新但超过 7 天不通知；失败重试所需的 `notified_at=None` 保留。

同一响应中身份相同的重复课程只能产生一个候选。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_monitor.py -q`

Expected: FAIL，原因是 `monitor` 模块尚不存在。

- [ ] **Step 3: 实现纯状态机**

```python
# lgycp_wx_miniprogram/monitor.py
from datetime import datetime, timedelta
from typing import Any

from lgycp_wx_miniprogram.models import Course, SHANGHAI


def in_recent_window(course: Course, now: datetime) -> bool:
    current = now.astimezone(SHANGHAI)
    return current - timedelta(days=7) <= course.published_at <= current


def update_archive(
    courses: list[Course], archive: dict[str, Any], now: datetime
) -> tuple[list[Course], dict[str, Any]]:
    timestamp = now.astimezone(SHANGHAI).isoformat()
    first_run = archive.get("initialized_at") is None
    if first_run:
        archive["initialized_at"] = timestamp
    records = archive.setdefault("courses", {})
    candidates = []
    processed = set()
    for course in courses:
        key = course.identity
        if key in processed:
            continue
        processed.add(key)
        record = records.get(key)
        if record is None:
            record = {
                "published_at": course.published_at.isoformat(),
                "first_seen_at": timestamp,
                "last_seen_at": timestamp,
                "baseline": first_run,
                "notified_at": None,
            }
            records[key] = record
        else:
            record["last_seen_at"] = timestamp
            record["published_at"] = course.published_at.isoformat()
        if (
            not first_run
            and not record["baseline"]
            and record["notified_at"] is None
            and in_recent_window(course, now)
        ):
            candidates.append(course)
    return candidates, archive


def mark_notified(
    candidates: list[Course], archive: dict[str, Any], now: datetime
) -> None:
    timestamp = now.astimezone(SHANGHAI).isoformat()
    for course in candidates:
        archive["courses"][course.identity]["notified_at"] = timestamp
```

- [ ] **Step 4: 运行状态机测试**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_monitor.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add lgycp_wx_miniprogram/monitor.py lgycp_wx_miniprogram/tests/test_monitor.py
git commit -m "feat: detect recently published courses"
```

### Task 6: 安全 HTML 邮件和 SMTP

**Files:**
- Create: `lgycp_wx_miniprogram/notify.py`
- Create: `lgycp_wx_miniprogram/tests/test_notify.py`

- [ ] **Step 1: 写邮件测试**

覆盖主题数量、所有可选字段、HTML 转义、无课程拒绝发送、SMTP 登录与单封发送、SMTP 失败返回 `False`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_notify.py -q`

Expected: FAIL，原因是 `notify` 模块尚不存在。

- [ ] **Step 3: 实现邮件层**

```python
# lgycp_wx_miniprogram/notify.py
from email.mime.text import MIMEText
from html import escape
import smtplib

from lgycp_wx_miniprogram.config import Settings
from lgycp_wx_miniprogram.models import Course


def build_message(courses: list[Course], settings: Settings) -> MIMEText:
    rows = []
    for course in courses:
        title = escape(course.title)
        if course.detail_url:
            title = f'<a href="{escape(course.detail_url, quote=True)}">{title}</a>'
        values = [
            title,
            escape(course.published_at.isoformat()),
            escape(course.campus),
            escape(course.term),
            escape(course.schedule),
            escape(course.price),
            escape(course.remaining),
        ]
        rows.append("<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>")
    body = (
        '<html><body><h2>临港少年宫新课程</h2><table>'
        '<tr><th>课程</th><th>上架时间</th><th>校区</th><th>学期</th>'
        '<th>上课时间</th><th>价格</th><th>剩余名额</th></tr>'
        + "".join(rows)
        + "</table></body></html>"
    )
    message = MIMEText(body, "html", "utf-8")
    message["Subject"] = f"[临港少年宫] {len(courses)} 门新课程"
    message["From"] = settings.smtp_user
    message["To"] = settings.notify_email
    return message


def send_courses(courses: list[Course], settings: Settings) -> bool:
    if not courses:
        return True
    message = build_message(courses, settings)
    try:
        with smtplib.SMTP_SSL(
            settings.smtp_server, settings.smtp_port, timeout=15
        ) as smtp:
            smtp.login(settings.smtp_user, settings.smtp_pass)
            smtp.sendmail(
                settings.smtp_user, [settings.notify_email], message.as_string()
            )
    except (OSError, smtplib.SMTPException):
        return False
    return True
```

- [ ] **Step 4: 运行邮件测试**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_notify.py -q`

Expected: PASS，且测试不连接真实 SMTP。

- [ ] **Step 5: 提交**

```bash
git add lgycp_wx_miniprogram/notify.py lgycp_wx_miniprogram/tests/test_notify.py
git commit -m "feat: email new miniprogram courses"
```

### Task 7: 单次运行编排与失败保护

**Files:**
- Create: `lgycp_wx_miniprogram/main.py`
- Create: `lgycp_wx_miniprogram/tests/test_main.py`

- [ ] **Step 1: 写编排测试**

通过依赖注入覆盖：抓取失败不保存；空或不可解析响应不保存；第一次运行保存基线但不发邮件；有候选时 SMTP 成功后标记并保存；SMTP 失败返回非零且不标记通知；无候选时保存 `last_seen_at`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_main.py -q`

Expected: FAIL，原因是 `main` 模块尚不存在。

- [ ] **Step 3: 实现编排**

`run()` 接受可替换函数，测试不访问网络、磁盘或 SMTP；CLI 入口使用真实依赖：

```python
# lgycp_wx_miniprogram/main.py
from datetime import datetime
import logging
from pathlib import Path

from lgycp_wx_miniprogram.client import ApiError, fetch_payload
from lgycp_wx_miniprogram.config import ConfigError, Settings, load_settings
from lgycp_wx_miniprogram.models import CourseParseError, SHANGHAI, extract_courses
from lgycp_wx_miniprogram.monitor import mark_notified, update_archive
from lgycp_wx_miniprogram.notify import send_courses
from lgycp_wx_miniprogram.storage import StorageError, load_archive, save_archive


ARCHIVE_PATH = Path(__file__).parent / "data" / "archive.json"
logger = logging.getLogger("lgycp_wx_miniprogram")


def run(settings: Settings, now: datetime | None = None) -> int:
    run_at = now or datetime.now(SHANGHAI)
    try:
        payload = fetch_payload(settings)
        courses = extract_courses(payload, settings)
        archive = load_archive(ARCHIVE_PATH)
    except (ApiError, CourseParseError, StorageError) as exc:
        logger.error("monitor failed: %s", exc)
        return 1
    candidates, updated = update_archive(courses, archive, run_at)
    if candidates:
        if not send_courses(candidates, settings):
            logger.error("email delivery failed")
            return 1
        mark_notified(candidates, updated, run_at)
    save_archive(ARCHIVE_PATH, updated)
    logger.info("checked %d courses; notified %d", len(courses), len(candidates))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        settings = load_settings()
    except ConfigError as exc:
        logger.error("configuration failed: %s", exc)
        return 2
    return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
```

在实现测试时用 `monkeypatch` 替换 `fetch_payload`、`send_courses`、`load_archive`、`save_archive` 和 `ARCHIVE_PATH`，严格验证保存调用次数和状态内容。

- [ ] **Step 4: 运行编排测试和全套测试**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_main.py -q`

Expected: PASS。

Run: `python -m pytest lgycp_wx_miniprogram/tests -q`

Expected: 全部 PASS，零真实网络请求。

- [ ] **Step 5: 提交**

```bash
git add lgycp_wx_miniprogram/main.py lgycp_wx_miniprogram/tests/test_main.py
git commit -m "feat: orchestrate daily course monitoring"
```

### Task 8: GitHub Actions 与运维文档

**Files:**
- Create: `.github/workflows/lgycp-wx-miniprogram-daily.yml`
- Create: `lgycp_wx_miniprogram/README.md`
- Create: `lgycp_wx_miniprogram/tests/test_workflow.py`

- [ ] **Step 1: 写工作流结构测试**

在 `lgycp_wx_miniprogram/tests/test_workflow.py` 读取 YAML 文本并断言：包含 `cron: "5 3 * * *"`、`workflow_dispatch`、Python 3.11、测试命令、运行入口、仅提交 `lgycp_wx_miniprogram/data/archive.json`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_workflow.py -q`

Expected: FAIL，原因是工作流尚不存在。

- [ ] **Step 3: 创建薄工作流**

```yaml
# .github/workflows/lgycp-wx-miniprogram-daily.yml
name: lgycp wx miniprogram daily

on:
  schedule:
    - cron: "5 3 * * *" # Beijing 11:05
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: lgycp-wx-miniprogram-daily
  cancel-in-progress: false

jobs:
  monitor:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    env:
      LGYCP_WX_API_URL: ${{ secrets.LGYCP_WX_API_URL }}
      LGYCP_WX_API_METHOD: ${{ vars.LGYCP_WX_API_METHOD }}
      LGYCP_WX_API_HEADERS_JSON: ${{ secrets.LGYCP_WX_API_HEADERS_JSON }}
      LGYCP_WX_API_BODY_JSON: ${{ secrets.LGYCP_WX_API_BODY_JSON }}
      LGYCP_WX_ITEMS_PATH: ${{ vars.LGYCP_WX_ITEMS_PATH }}
      LGYCP_WX_ID_PATH: ${{ vars.LGYCP_WX_ID_PATH }}
      LGYCP_WX_TITLE_PATH: ${{ vars.LGYCP_WX_TITLE_PATH }}
      LGYCP_WX_PUBLISHED_PATH: ${{ vars.LGYCP_WX_PUBLISHED_PATH }}
      LGYCP_WX_CAMPUS_PATH: ${{ vars.LGYCP_WX_CAMPUS_PATH }}
      LGYCP_WX_TERM_PATH: ${{ vars.LGYCP_WX_TERM_PATH }}
      LGYCP_WX_SCHEDULE_PATH: ${{ vars.LGYCP_WX_SCHEDULE_PATH }}
      LGYCP_WX_PRICE_PATH: ${{ vars.LGYCP_WX_PRICE_PATH }}
      LGYCP_WX_REMAINING_PATH: ${{ vars.LGYCP_WX_REMAINING_PATH }}
      LGYCP_WX_DETAIL_URL_PATH: ${{ vars.LGYCP_WX_DETAIL_URL_PATH }}
      LGPAC_NOTIFY_EMAIL: ${{ secrets.LGPAC_NOTIFY_EMAIL }}
      LGPAC_SMTP_SERVER: ${{ secrets.LGPAC_SMTP_SERVER }}
      LGPAC_SMTP_PORT: ${{ secrets.LGPAC_SMTP_PORT }}
      LGPAC_SMTP_USER: ${{ secrets.LGPAC_SMTP_USER }}
      LGPAC_SMTP_PASS: ${{ secrets.LGPAC_SMTP_PASS }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - name: install
        run: pip install -r lgycp_wx_miniprogram/requirements.txt
      - name: test
        run: python -m pytest lgycp_wx_miniprogram/tests -q
      - name: monitor
        run: python -m lgycp_wx_miniprogram.main
      - name: commit archive
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add lgycp_wx_miniprogram/data/archive.json
          if git diff --cached --quiet; then
            echo "no archive changes"
            exit 0
          fi
          git commit -m "lgycp-wx: daily course snapshot"
          for attempt in 1 2 3; do
            git push && exit 0
            echo "push failed; rebasing attempt ${attempt}/3"
            git pull --rebase origin main
          done
          exit 1
```

该工作流沿用仓库现有的 push/rebase 重试方式，避免与其他定时任务并发提交冲突。

- [ ] **Step 4: 编写 README 并运行测试**

README 必须列出：用途、北京时间调度、7 天语义、首次基线行为、抓包输入清单、所有 Secrets/Variables、手工运行命令、脱敏要求、401/403/空响应/邮件失败排查方法，以及无法重放接口时停止而非绕过的边界。

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_workflow.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/lgycp-wx-miniprogram-daily.yml lgycp_wx_miniprogram/README.md lgycp_wx_miniprogram/tests/test_workflow.py
git commit -m "ci: schedule daily miniprogram course monitor"
```

### Task 9: 真实接口契约验证和最终验收

**Files:**
- Modify only if evidence requires it: `lgycp_wx_miniprogram/models.py`
- Modify only if evidence requires it: `lgycp_wx_miniprogram/client.py`
- Modify: `lgycp_wx_miniprogram/README.md`

- [ ] **Step 1: 在本地环境注入抓包所得配置**

将 URL、认证头和请求体放入未提交的环境变量；将课程数组及字段点分路径配置为对应的 `LGYCP_WX_*_PATH`。执行前用 `git status --ignored --short` 确认 `.env` 和 `captures/` 被忽略。

- [ ] **Step 2: 运行只读接口验证**

Run: `python -m lgycp_wx_miniprogram.main`

Expected: 第一次运行日志显示有效课程数量、通知数量为 0，并生成不含 Token/Cookie 的 `data/archive.json`。

- [ ] **Step 3: 检查归档和日志安全性**

Run: `! rg -n -i "authorization|cookie|token|session|password|secret" lgycp_wx_miniprogram/data`

Expected: 无敏感值命中；若没有日志文件，只检查 `data/`。

- [ ] **Step 4: 运行完整验收**

Run: `python -m pytest lgycp_wx_miniprogram/tests -q`

Expected: 全部 PASS。

Run: `git diff --check`

Expected: 无输出，退出码 0。

Run: `git status --short`

Expected: 只包含预期归档或真实接口证据要求的代码改动，不包含 `.env`、抓包或凭据。

- [ ] **Step 5: 提交经过真实契约验证的调整**

仅当真实响应要求代码调整时提交：

```bash
git add lgycp_wx_miniprogram/models.py lgycp_wx_miniprogram/client.py lgycp_wx_miniprogram/README.md
git commit -m "fix: align course adapter with live contract"
```

如果无需代码调整，则不创建空提交。部署 GitHub Secrets/Variables 后先手工触发一次工作流确认基线，再等待下一次北京时间 11:05 的定时运行。

## 最终完成条件

- 全套离线测试通过，且测试不访问网络或 SMTP。
- 真实接口只读请求能够稳定重放并返回可解析课程。
- 第一次运行建立基线不发邮件；后续模拟新增课程恰好发送一次。
- 7 天窗口严格使用接口上架时间，缺失或非法时间不使用 `first_seen_at` 兜底。
- 请求失败、空响应、存储损坏和邮件失败均不会把课程误标为已通知。
- 每天北京时间 11:05 的工作流可手工触发且只提交非敏感归档。
- Git 历史中不存在 Token、Cookie、密码、原始抓包或个人信息。
