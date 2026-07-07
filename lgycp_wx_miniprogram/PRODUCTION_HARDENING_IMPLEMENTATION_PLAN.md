# 临港少年宫课程监控生产加固 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在维持每天北京时间 11:05 单次抓取和现有首次基线/后续通知语义的前提下，把课程归档升级为可读 schema v2，并让 API、解析、SMTP、归档及 Git 推送失败都留下可定位、脱敏的诊断证据。

**Architecture:** 继续使用 `lgycp_wx_miniprogram` 独立子项目。课程模型只规范化已确认的业务字段；存储层负责 v1→v2 内存迁移和原子保存；监控层产生候选和运行统计；HTTP 层返回脱敏元数据并执行有限重试；课程邮件复用仓库共享 SMTP 实现；诊断器贯穿固定阶段并输出 JSON/Job Summary；GitHub Actions 仍只有一个 cron，并在失败时上传 30 天 artifact。

**Tech Stack:** Python 3.11、requests、标准库 `dataclasses/decimal/json/traceback/zoneinfo/smtplib`、pytest、Ruff、PyYAML、GitHub Actions。

---

## 实施约束

- 不增加第二个 cron、健康检查或自动补跑。
- `published_at` 只来自小程序 `createTime`，7 天窗口两端均包含，未来时间不入选。
- 不推测报名开始/截止时间；接口目前没有该字段。
- 不保存图片、图片 URL、介绍正文、购买须知、请求/响应正文、headers、Cookie、Token 或 SMTP 值。
- API/解析/SMTP/保存失败时不覆盖旧归档；SMTP 成功但保存或 push 失败时允许下次重复通知，不能提前标记导致漏报。
- 当前没有经过等价验证的备用 API，因此只允许配置覆盖并 fail closed，不实现自动换源。
- 现有三个必需 GitHub Secrets 名称保持不变：`LGPAC_NOTIFY_EMAIL`、`LGPAC_SMTP_USER`、`LGPAC_SMTP_PASS`；可选的 server/port 也沿用现有名称。

## 文件映射

- Modify: `lgycp_wx_miniprogram/config.py` — 增加价格回退、课程类型和课程周期字段路径默认值。
- Modify: `lgycp_wx_miniprogram/models.py` — 精确金额转换、payload 契约摘要、课程规范化结果。
- Modify: `lgycp_wx_miniprogram/storage.py` — schema v2、v1 无损迁移、结构校验。
- Modify: `lgycp_wx_miniprogram/monitor.py` — 完整业务字段、缺失统计、运行摘要和成功收尾。
- Modify: `lgycp_wx_miniprogram/client.py` — 可重试状态、`Retry-After`、jitter 和脱敏 HTTP trace。
- Modify: `lgpac/notify.py` — 向后兼容的 `raise_on_error` 与异常链。
- Modify: `lgycp_wx_miniprogram/notify.py` — 只渲染课程邮件并调用共享发送逻辑。
- Create: `lgycp_wx_miniprogram/diagnostics.py` — 固定阶段、完整脱敏 traceback、JSON 和 Job Summary。
- Modify: `lgycp_wx_miniprogram/main.py` — 生产编排、失败关闭和诊断落盘。
- Modify: `.github/workflows/lgycp-wx-miniprogram-daily.yml` — 捕获阶段日志、总是写摘要、失败上传 artifact。
- Modify: `lgycp_wx_miniprogram/README.md` — 归档、故障排查和人工重跑说明。
- Modify: `lgycp_wx_miniprogram/data/archive.json` — 不手工改写；第一次完整成功运行自动从 v1 原子升级。
- Modify: `lgycp_wx_miniprogram/tests/test_config.py`
- Modify: `lgycp_wx_miniprogram/tests/test_models.py`
- Modify: `lgycp_wx_miniprogram/tests/test_storage.py`
- Modify: `lgycp_wx_miniprogram/tests/test_monitor.py`
- Modify: `lgycp_wx_miniprogram/tests/test_client.py`
- Modify: `lgycp_wx_miniprogram/tests/test_notify.py`
- Create: `lgycp_wx_miniprogram/tests/test_diagnostics.py`
- Modify: `lgycp_wx_miniprogram/tests/test_main.py`
- Modify: `lgycp_wx_miniprogram/tests/test_workflow.py`

### Task 1: 固定生产课程字段与金额语义

**Files:**
- Modify: `lgycp_wx_miniprogram/config.py`
- Modify: `lgycp_wx_miniprogram/models.py`
- Modify: `lgycp_wx_miniprogram/tests/fixtures/courses_response.json`
- Modify: `lgycp_wx_miniprogram/tests/test_config.py`
- Modify: `lgycp_wx_miniprogram/tests/test_models.py`

- [ ] **Step 1: 先写字段默认值和金额转换失败测试**

在 `test_config.py` 断言默认路径：

```python
assert settings.price_path == "subjectPrice"
assert settings.fallback_price_path == "price"
assert settings.course_type_path == "courseTypeName"
assert settings.start_date_path == "startDate"
assert settings.end_date_path == "endDate"
```

在脱敏 fixture 的课程中加入：

```json
{
  "subjectPrice": 64000,
  "price": 65000,
  "courseTypeName": "无人机创客营",
  "startDate": "2026-07-15",
  "endDate": "2026-08-15",
  "coursePicUrl": "https://example.invalid/must-not-be-stored.jpg"
}
```

在 `test_models.py` 增加以下核心断言：

```python
assert course.price_yuan == "640.00"
assert course.course_type == "无人机创客营"
assert course.course_start_date == "2026-07-15"
assert course.course_end_date == "2026-08-15"
assert not hasattr(course, "registration_time")
assert not hasattr(course, "image_url")
```

再增加参数化测试，覆盖 `subjectPrice` 优先、缺失时回退 `price`、`1` 分为 `0.01`、`64000.5` 分为 `640.01`，以及两者都缺失时为空字符串。金额实现必须使用 `Decimal` 和 `ROUND_HALF_UP`，不能经过二进制浮点除法。

- [ ] **Step 2: 运行目标测试确认 RED**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_config.py lgycp_wx_miniprogram/tests/test_models.py -q`

Expected: FAIL，缺少新 Settings 字段和 `Course` 业务字段。

- [ ] **Step 3: 实现最小字段模型和解析函数**

把 `Course` 收敛为归档和邮件实际需要的字段；身份 fallback 改为由名称、类型和课程起止日期组成，仍优先使用 `course_id`：

```python
@dataclass(frozen=True)
class Course:
    course_id: str | None
    title: str
    published_at: datetime
    price_yuan: str = ""
    course_type: str = ""
    course_start_date: str = ""
    course_end_date: str = ""
```

增加精确价格函数：

```python
def parse_price_yuan(primary: Any, fallback: Any) -> str:
    raw = primary if primary is not None and primary != "" else fallback
    if raw is None or raw == "":
        return ""
    try:
        cents = Decimal(str(raw))
    except InvalidOperation as exc:
        raise CourseParseError("invalid course price") from exc
    if not cents.is_finite() or cents < 0:
        raise CourseParseError("invalid course price")
    yuan = (cents / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return format(yuan, ".2f")
```

可选金额、类型和日期缺失时使用空字符串；必需的名称和 `createTime` 仍按现有规则使该 item 无效。不要读取 `coursePicUrl`。

- [ ] **Step 4: 运行测试确认 GREEN**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_config.py lgycp_wx_miniprogram/tests/test_models.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add lgycp_wx_miniprogram/config.py lgycp_wx_miniprogram/models.py lgycp_wx_miniprogram/tests/fixtures/courses_response.json lgycp_wx_miniprogram/tests/test_config.py lgycp_wx_miniprogram/tests/test_models.py
git commit -m "feat: normalize production course fields"
```

### Task 2: schema v2 与确定性 v1 迁移

**Files:**
- Modify: `lgycp_wx_miniprogram/storage.py`
- Modify: `lgycp_wx_miniprogram/tests/test_storage.py`

- [ ] **Step 1: 写 v2 空归档、迁移和拒绝未知版本测试**

固定空归档：

```python
assert empty_archive() == {
    "schema_version": 2,
    "initialized_at": None,
    "last_success_at": None,
    "last_run": None,
    "courses": {},
}
```

使用真实 v1 形状的测试数据，断言迁移后：原 ID、五个系统时间/布尔字段完全保留；五个新增业务字符串为空；顶层增加 `last_success_at` 和 `last_run`；输入字典不被修改。再断言 schema 3、非 object 课程记录、缺失 `courses` 都抛出固定安全消息的 `StorageError`。

- [ ] **Step 2: 运行目标测试确认 RED**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_storage.py -q`

Expected: FAIL，当前只接受 schema 1。

- [ ] **Step 3: 实现纯函数迁移和 v2 校验**

增加：

```python
COURSE_BUSINESS_DEFAULTS = {
    "course_name": "",
    "price_yuan": "",
    "course_type": "",
    "course_start_date": "",
    "course_end_date": "",
}

def migrate_v1(data: dict[str, Any]) -> dict[str, Any]:
    migrated = copy.deepcopy(data)
    migrated["schema_version"] = 2
    migrated["last_success_at"] = None
    migrated["last_run"] = None
    for record in migrated["courses"].values():
        for key, default in COURSE_BUSINESS_DEFAULTS.items():
            record.setdefault(key, default)
    return migrated
```

`load_archive()` 只允许 v1 自动迁移和 v2 原样读取；迁移只发生在内存，仍由完整成功运行的 `save_archive()` 原子替换文件。保持现有 `mkstemp`、`fsync` 和 `os.replace` 保护。

- [ ] **Step 4: 运行测试确认 GREEN**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_storage.py -q`

Expected: PASS，包括原子替换失败不破坏旧文件。

- [ ] **Step 5: 提交**

```bash
git add lgycp_wx_miniprogram/storage.py lgycp_wx_miniprogram/tests/test_storage.py
git commit -m "feat: migrate course archive to schema v2"
```

### Task 3: 归档业务字段、消失课程和成功摘要

**Files:**
- Modify: `lgycp_wx_miniprogram/monitor.py`
- Modify: `lgycp_wx_miniprogram/tests/test_monitor.py`

- [ ] **Step 1: 写完整记录和运行统计测试**

新增不可变结果类型的预期：

```python
@dataclass(frozen=True)
class ArchiveUpdate:
    candidates: list[Course]
    newly_seen_count: int
    missing_count: int
```

测试首次记录恰好包含：`course_name`、`price_yuan`、`course_type`、`published_at`、`course_start_date`、`course_end_date`、`first_seen_at`、`last_seen_at`、`baseline`、`notified_at`。明确断言 `coursePicUrl`、`image_url`、`registration_time`、`campus` 不存在。

增加“上一轮有 A/B，本轮只有 A”的测试：B 仍保留，B 的 `last_seen_at` 不变，`missing_count == 1`。增加相同 ID 的业务字段变化会覆盖最新业务字段、但 `first_seen_at` 和 `notified_at` 不变的测试。

- [ ] **Step 2: 写成功收尾测试**

增加 `finalize_success()` 测试，输入 HTTP/解析统计和已通知数，断言写入：

```python
archive["last_success_at"] == finished_at.isoformat()
archive["last_run"] == {
    "run_id": "run-123",
    "started_at": started_at.isoformat(),
    "finished_at": finished_at.isoformat(),
    "source_total": 53,
    "parsed_count": 52,
    "skipped_invalid_count": 1,
    "newly_seen_count": 1,
    "eligible_count": 1,
    "notified_count": 1,
    "missing_count": 1,
    "oldest_published_at": "2025-06-06T11:05:13+08:00",
    "newest_published_at": "2026-07-08T09:30:00+08:00",
}
```

- [ ] **Step 3: 运行目标测试确认 RED**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_monitor.py -q`

Expected: FAIL，当前记录只有系统字段，也没有摘要结果。

- [ ] **Step 4: 实现字段更新、missing 统计和收尾函数**

使用一个 `_course_record(course, timestamp, baseline)` helper 生成固定 key；更新旧课程时只更新六个业务字段和 `last_seen_at`。`missing_count` 使用“归档 identity 集合减本轮去重 identity 集合”的大小，不删除记录。

`finalize_success()` 必须在 SMTP 已完成且即将原子保存前调用；如果保存失败，磁盘上的旧归档不会含这次成功时间。最早/最新时间从本轮解析成功课程计算，ISO 输出使用上海时区。

- [ ] **Step 5: 回归现有通知状态机**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_monitor.py -q`

Expected: PASS，首次基线不通知、后续 7 天内新 ID 通知、超窗只归档、未成功通知持续重试、重复 ID 只产生一个候选。

- [ ] **Step 6: 提交**

```bash
git add lgycp_wx_miniprogram/monitor.py lgycp_wx_miniprogram/tests/test_monitor.py
git commit -m "feat: record course audit summaries"
```

### Task 4: HTTP 有限重试与脱敏元数据

**Files:**
- Modify: `lgycp_wx_miniprogram/client.py`
- Modify: `lgycp_wx_miniprogram/tests/test_client.py`

- [ ] **Step 1: 扩充 fake response 并写重试矩阵测试**

让 `FakeResponse` 支持 `headers`。参数化断言 408、425、429、500、503 会重试，400、401、403、404 不重试；总请求最多 3 次。断言 `Retry-After: 12` 调用 sleep 12 秒，`Retry-After: 90` 截断为 30 秒，非法值回退到指数退避+jitter。

增加注入点：

```python
sleeps = []
result = fetch_payload(
    settings(),
    session=session,
    sleep=sleeps.append,
    jitter=lambda: 0.25,
)
```

`fetch_response()` 的成功结果必须带原始 response 对象和只包含 method/scheme/host/path/status/attempts/elapsed 的 `HttpTrace`；`decode_response()` 才读取 JSON。测试 URL 即使含 `?token=secret`，trace 也不得含 query、headers、body 或 `secret`。保留 `fetch_payload()` 作为顺序调用这两个函数的兼容 wrapper。

- [ ] **Step 2: 运行目标测试确认 RED**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_client.py -q`

Expected: FAIL，当前只重试 5xx/连接错误且没有 trace。

- [ ] **Step 3: 实现固定三次 retry policy**

定义：

```python
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 30
RETRYABLE_STATUSES = {408, 425, 429}

@dataclass(frozen=True)
class HttpTrace:
    method: str
    scheme: str
    host: str
    path: str
    status_code: int | None
    attempts: int
    elapsed_ms: int

@dataclass(frozen=True)
class FetchedResponse:
    response: Any
    trace: HttpTrace
```

重试条件为 requests 连接/超时异常、上述集合和所有 5xx。非重试 4xx 立即抛 `ApiError(f"course API returned HTTP {status}")`。`Retry-After` 仅接受十进制秒数；否则等待 `min(2 ** (attempt - 1) + jitter(), 5.0)`。所有异常消息固定，不拼接原异常或响应正文。`decode_response(fetched)` 返回 `FetchResult(payload, trace)`；JSON 异常固定为 `ApiError("course API returned invalid JSON")`。

- [ ] **Step 4: 验证敏感内容不出现在错误和 trace**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_client.py -q`

Expected: PASS，测试 token 在 `str(ApiError)`、`HttpTrace` 和 captured logs 中均不存在。

- [ ] **Step 5: 提交**

```bash
git add lgycp_wx_miniprogram/client.py lgycp_wx_miniprogram/tests/test_client.py
git commit -m "feat: harden course API retries"
```

### Task 5: 复用共享 SMTP 并保留异常链

**Files:**
- Modify: `lgpac/notify.py`
- Modify: `lgycp_wx_miniprogram/notify.py`
- Modify: `lgycp_wx_miniprogram/tests/test_notify.py`
- Create: `lgycp_wx_miniprogram/tests/test_shared_notify.py`

- [ ] **Step 1: 写共享函数向后兼容测试**

覆盖四个契约：默认调用 SMTP 异常仍返回 `False`；默认调用缺配置仍返回 `False`；`raise_on_error=True` 时 SMTP 异常抛 `EmailDeliveryError("email delivery failed")` 且 `__cause__` 是原始异常；详细模式缺配置抛固定的 `EmailDeliveryError("email configuration is incomplete")`。

```python
with pytest.raises(EmailDeliveryError, match="email delivery failed") as error:
    send_email("subject", "body", raise_on_error=True)
assert isinstance(error.value.__cause__, smtplib.SMTPException)
```

- [ ] **Step 2: 写课程邮件只包含批准字段的测试**

邮件列固定为课程名、价格、类型、上架时间、课程开始、课程结束。断言 HTML 转义生效，且 body 中不存在 `coursePicUrl`、图片 URL、校区、报名时间。mock `lgpac.notify.send_email` 并断言课程路径调用：

```python
shared_send(subject, html_body, raise_on_error=True)
```

- [ ] **Step 3: 运行目标测试确认 RED**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_shared_notify.py lgycp_wx_miniprogram/tests/test_notify.py -q`

Expected: FAIL，共享函数没有详细模式，课程模块仍直接实现 SMTP。

- [ ] **Step 4: 实现兼容扩展与薄课程 adapter**

共享函数签名：

```python
class EmailDeliveryError(RuntimeError):
    """Safe email failure that preserves the original exception chain."""

def send_email(
    subject: str,
    html_body: str,
    *,
    raise_on_error: bool = False,
) -> bool:
```

现有调用不传关键字，行为保持不变。课程 `send_courses()` 对空列表返回 `True`；非空先生成 subject/body，再调用共享函数的详细模式。不要在课程模块保留第二份 `SMTP_SSL`、login 或 secret 读取。

- [ ] **Step 5: 运行邮件回归测试**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_shared_notify.py lgycp_wx_miniprogram/tests/test_notify.py -q`

Expected: PASS，SMTP mock 只存在于共享通知测试。

- [ ] **Step 6: 提交**

```bash
git add lgpac/notify.py lgycp_wx_miniprogram/notify.py lgycp_wx_miniprogram/tests/test_shared_notify.py lgycp_wx_miniprogram/tests/test_notify.py
git commit -m "refactor: share SMTP delivery with course monitor"
```

### Task 6: 每次运行的脱敏诊断 JSON 与 Job Summary

**Files:**
- Create: `lgycp_wx_miniprogram/diagnostics.py`
- Create: `lgycp_wx_miniprogram/tests/test_diagnostics.py`

- [ ] **Step 1: 写成功、失败、阶段和脱敏测试**

固定合法阶段：

```python
STAGES = (
    "configuration", "http_fetch", "json_decode", "contract_validation",
    "course_normalization", "archive_load", "candidate_selection",
    "smtp_delivery", "archive_save", "git_commit_push",
)
```

测试 `RunDiagnostics.stage(name)` 记录开始、结束和耗时；异常时保存 `failed_stage`、异常类型、固定 safe message 和包含测试文件/函数/异常链的 traceback。给异常字符串、URL query、header JSON 和 SMTP 环境变量植入不同 canary，断言输出 JSON/Markdown 都不含 canary。

成功 JSON 至少包含 run id、status、起止时间、总耗时、HTTP trace、契约 key 名/数量、运行时版本和业务计数。不要保存响应 value、课程 object 或邮件 body。

- [ ] **Step 2: 运行目标测试确认 RED**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_diagnostics.py -q`

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 实现诊断器和统一 sanitizer**

诊断器必须：

- 使用 `traceback.format_exception()` 保留文件、函数、行号和 cause chain，不启用 locals。
- 只接受调用者提供的固定 `safe_message` 作为顶层消息。
- 将已知配置值替换为 `[REDACTED]`，并把所有 `http(s)://host/path?query` 改为 `http(s)://host/path?[REDACTED]`。
- 只记录 `payload.keys()` 的排序 key 名，不记录 value。
- 通过同目录临时文件 + `os.replace()` 原子写诊断 JSON；诊断写入自身失败时只向 stderr 输出固定消息，不能覆盖业务异常。
- 如果存在 `GITHUB_STEP_SUMMARY`，追加简短 Markdown 表；本地运行没有该变量时静默跳过。

运行时字段从 `GITHUB_SHA`、`GITHUB_RUN_ID`、`platform.python_version()` 和 `requests.__version__` 取得；不存在时使用空字符串，不读取任意环境变量快照。

- [ ] **Step 4: 验证 GREEN 和无敏感值**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_diagnostics.py -q`

Expected: PASS，生成的 JSON 能被 `json.loads()` 读取，所有 canary 均为零命中。

- [ ] **Step 5: 提交**

```bash
git add lgycp_wx_miniprogram/diagnostics.py lgycp_wx_miniprogram/tests/test_diagnostics.py
git commit -m "feat: add sanitized run diagnostics"
```

### Task 7: 编排固定阶段并保持失败关闭

**Files:**
- Modify: `lgycp_wx_miniprogram/models.py`
- Modify: `lgycp_wx_miniprogram/main.py`
- Modify: `lgycp_wx_miniprogram/tests/test_models.py`
- Modify: `lgycp_wx_miniprogram/tests/test_main.py`

- [ ] **Step 1: 把契约验证与课程规范化拆成两个可诊断步骤**

在模型层定义：

```python
@dataclass(frozen=True)
class CoursePayload:
    items: list[Any]
    source_total: int
    top_level_keys: tuple[str, ...]

@dataclass(frozen=True)
class CourseExtraction:
    courses: list[Course]
    skipped_invalid_count: int
```

`validate_payload()` 只验证服务端 error、items path、非空 list 和分页完整性；`normalize_courses()` 才逐项解析。保留 `extract_courses()` 作为组合 wrapper，使既有外部调用与测试不突然失效。

- [ ] **Step 2: 写 main 的全阶段失败矩阵**

参数化让 `fetch_payload`、`validate_payload`、`normalize_courses`、`load_archive`、`update_archive`、`send_courses` 和 `save_archive` 分别失败，断言：

- 返回非零；配置错误仍返回 2，其余业务失败返回 1。
- 诊断 JSON 的 `failed_stage` 精确匹配。
- API/解析/SMTP/保存失败均不调用后续 archive save；保存失败时磁盘旧归档仍由 storage 测试保证。
- SMTP 失败时 `notified_at` 仍为空。
- 每个失败文件含 traceback，不含 fake token、SMTP 密码或课程 body。

- [ ] **Step 3: 写成功、首次基线和后续更新测试**

首次空归档：不调用邮件，保存全部 `baseline: true`，写 `last_success_at/last_run`。后续加入一个 7 天内新 ID：仅发一封聚合邮件，成功后标记并保存。后续加入超 7 天 ID：只归档不发。断言 source/parsed/skipped/new/missing/eligible/notified 各计数准确。

- [ ] **Step 4: 运行目标测试确认 RED**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_models.py lgycp_wx_miniprogram/tests/test_main.py -q`

Expected: FAIL，main 尚未接诊断器和新的结果类型。

- [ ] **Step 5: 实现固定顺序的 orchestration**

顺序必须是：configuration（`main()`）→ http_fetch（`fetch_response`）→ json_decode（`decode_response`）→ contract_validation → course_normalization → archive_load → candidate_selection → smtp_delivery（仅有候选时）→ mark_notified → finalize_success → archive_save。每一步由 main 的 `diagnostics.stage(...)` 包住，因此失败阶段没有猜测逻辑。

`run()` 接受可测试的 `diagnostics_path`，默认从 `LGYCP_DIAGNOSTICS_PATH` 读取；未设置时使用 `tempfile.gettempdir()/lgycp-wx-diagnostics.json`。顶层只捕获预期业务异常和最终兜底 `Exception`，二者都必须调用诊断失败收尾；不得用异常字符串作为 safe message。`KeyboardInterrupt`/`SystemExit` 不吞掉。

成功时先原子保存 v2 归档，再将诊断标记 success 并写 Job Summary。若诊断文件写失败，业务成功不应被改判失败，但 stderr 只打印固定诊断错误。

- [ ] **Step 6: 运行完整 Python 测试**

Run: `python -m pytest lgycp_wx_miniprogram/tests -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add lgycp_wx_miniprogram/models.py lgycp_wx_miniprogram/main.py lgycp_wx_miniprogram/tests/test_models.py lgycp_wx_miniprogram/tests/test_main.py
git commit -m "feat: orchestrate auditable course runs"
```

### Task 8: GitHub Actions 失败 artifact 和全程日志

**Files:**
- Modify: `.github/workflows/lgycp-wx-miniprogram-daily.yml`
- Modify: `lgycp_wx_miniprogram/tests/test_workflow.py`

- [ ] **Step 1: 写工作流结构测试**

除现有时间/手工触发/只提交归档测试外，新增断言：

- `schedule` 下恰好一个 cron，值仍为 `5 3 * * *`。
- env 设置 `LGYCP_DIAGNOSTICS_PATH: ${{ runner.temp }}/lgycp-wx-diagnostics.json`。
- install、test、monitor、commit/push 使用 `set -o pipefail` 和 `tee -a "$RUNNER_TEMP/lgycp-wx-workflow.log"`，确保命令失败不会被 pipe 吞掉。
- `actions/upload-artifact@v4` 使用 `if: failure()`、`retention-days: 30`，path 同时包含诊断 JSON 和 workflow log。
- `if: always()` 的摘要步骤存在。
- 没有第二个 schedule、备用 URL 或请求 secret。

- [ ] **Step 2: 运行目标测试确认 RED**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_workflow.py -q`

Expected: FAIL，当前没有 artifact、统一日志和 always summary。

- [ ] **Step 3: 修改 workflow**

每个 shell step 使用：

```yaml
run: |
  set -o pipefail
  python -m lgycp_wx_miniprogram.main 2>&1 | tee -a "$RUNNER_TEMP/lgycp-wx-workflow.log"
```

commit/push 循环同样整体用 `2>&1 | tee -a ...` 包住并保留 pipefail。追加：

```yaml
- name: append workflow status summary
  if: always()
  run: |
    echo "## Workflow result" >> "$GITHUB_STEP_SUMMARY"
    echo "- Prior steps: ${{ job.status }}" >> "$GITHUB_STEP_SUMMARY"

- name: upload failure diagnostics
  if: failure()
  uses: actions/upload-artifact@v4
  with:
    name: lgycp-wx-diagnostics-${{ github.run_id }}
    path: |
      ${{ runner.temp }}/lgycp-wx-diagnostics.json
      ${{ runner.temp }}/lgycp-wx-workflow.log
    if-no-files-found: warn
    retention-days: 30
```

artifact step必须放在失败后仍会执行的位置；上传失败不能触发任何归档修改。工作流 permissions 继续只保留 `contents: write`。

- [ ] **Step 4: 验证 YAML 和结构测试**

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_workflow.py -q`

Expected: PASS。

Run: `python -c 'import pathlib,yaml; yaml.safe_load(pathlib.Path(".github/workflows/lgycp-wx-miniprogram-daily.yml").read_text())'`

Expected: exit 0。

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/lgycp-wx-miniprogram-daily.yml lgycp_wx_miniprogram/tests/test_workflow.py
git commit -m "ci: preserve course monitor failure traces"
```

### Task 9: 运维文档、迁移演练和生产验收

**Files:**
- Modify: `lgycp_wx_miniprogram/README.md`
- Inspect only: `lgycp_wx_miniprogram/data/archive.json`

- [ ] **Step 1: 更新运维文档**

README 必须准确说明：每天 11:05 单 cron；首次/后续状态机；只有 `createTime` 决定 7 天；无报名时间字段；schema v2 字段；消失课程不删除；artifact 下载路径；按 `failed_stage` 和 traceback 排查；修复后从 Actions 使用 `workflow_dispatch` 手动重跑；SMTP 成功后保存/push 失败可能重复通知。

- [ ] **Step 2: 运行离线迁移演练，不改生产 archive**

使用 pytest 的 `tmp_path` 覆盖从当前 v1 shape 读取、完整成功、保存 v2、再次读取的 round trip。不得用 shell 重定向覆盖 `data/archive.json`；不得发送真实邮件。

Run: `python -m pytest lgycp_wx_miniprogram/tests/test_storage.py lgycp_wx_miniprogram/tests/test_main.py -q`

Expected: PASS。

- [ ] **Step 3: 全量静态和测试验证**

```bash
python -m pytest lgycp_wx_miniprogram/tests -q
python -m ruff check lgpac/notify.py lgycp_wx_miniprogram
python -m ruff format --check lgpac/notify.py lgycp_wx_miniprogram
python -m compileall -q lgpac/notify.py lgycp_wx_miniprogram
python -c 'import pathlib,yaml; yaml.safe_load(pathlib.Path(".github/workflows/lgycp-wx-miniprogram-daily.yml").read_text())'
git diff --check
```

Expected: 全部 exit 0。

- [ ] **Step 4: 只读公开 API smoke test**

只运行 client + contract validation + normalization；不加载生产归档、不调用 SMTP、不写仓库文件。输出只允许 HTTP status、总数、解析数、跳过数、最早/最新发布时间，不能打印 URL query、headers 或 course object。

Expected: HTTP 成功；`source_total == parsed_count + skipped_invalid_count`；列表非空；分页完整。

- [ ] **Step 5: 敏感字段和图片零命中审计**

先用测试生成一份失败诊断样本到临时目录，再运行：

```bash
rg -n 'coursePicUrl|image_url|registration_time|LGPAC_SMTP_PASS|Authorization|Cookie' lgycp_wx_miniprogram/data/archive.json /tmp/lgycp-wx-diagnostics.json
```

Expected: exit 1（零命中）。随后检查 staged diff 不包含邮箱、SMTP 授权码、header/body 实值或图片 URL。不要删除用户已有临时抓包目录；清理必须另行获得授权。

- [ ] **Step 6: 提交文档**

```bash
git add lgycp_wx_miniprogram/README.md
git commit -m "docs: document course monitor operations"
```

- [ ] **Step 7: 最终分支核对**

Run: `git status --short --branch`

Expected: feature branch clean；没有未跟踪凭据、抓包或诊断 artifact。

Run: `git log --oneline --decorate -12`

Expected: 本计划的实施提交均位于 `feature/lgycp-wx-miniprogram`，尚未在未授权情况下 push 或 merge。

## 计划自审清单

- [ ] 用户批准的六个业务字段均有来源和测试：ID 作为 archive key、名称、人民币价格、类型、发布时间、课程起止日期。
- [ ] `registration_time` 和所有图片字段在模型、归档、邮件、诊断中都不存在。
- [ ] v1 基线数据无损迁移；首次部署不会把 52 门存量课程当新增邮件发送。
- [ ] 7 天判断仍只看 `createTime`，首次 baseline、后续新增、超窗、重复 ID 和 SMTP 重试语义均有回归测试。
- [ ] 所有失败路径 fail closed，并留下脱敏 traceback；旧归档由原子替换保护。
- [ ] 共享 SMTP 默认布尔行为不变，只有课程监控请求详细异常链。
- [ ] workflow 只有一个 `5 3 * * *`，没有当晚补跑；失败 artifact 保留 30 天。
- [ ] HTTP fallback 只有配置覆盖，没有未经验证的自动备用接口。
- [ ] 文档中没有未完成占位标记、虚构报名字段或要求用户重新提供已经存在的三个 Secrets。
