# 临港少年宫小程序新课程订阅

这是一个独立、只读的课程监控器。它每天北京时间 11:05 请求一次小程序课程列表接口，使用接口返回的上架/发布时间筛选最近 7 天课程，并只向尚未通知过的课程发送一封汇总邮件。

## 行为边界

- 第一次成功运行只建立基线，不发送存量课程。
- 7 天窗口是闭区间：`运行时间 - 7 天 <= 上架时间 <= 运行时间`。
- 仅使用接口上架/发布时间；缺失或非法时间不会用本地首次发现时间代替。
- 不自动报名，不提交个人信息，不绕过验证码、签名或其他访问控制。
- 接口不能由普通 HTTP 客户端稳定重放时停止，不切换到 RPA。

## 抓包输入

从本人可正常访问的小程序会话中取得课程列表请求，确认它能由普通 HTTP 客户端重放。需要记录：

- 请求 URL 和 HTTP 方法；
- 必要请求头和 JSON 请求体；
- 课程数组在响应中的点分路径，例如 `data.list`；
- 课程名称和上架时间的字段路径；
- 可选的课程 ID、校区、学期、上课时间、价格、余量和详情链接字段路径；
- 接口是否分页；如果分页，必须确认如何取完所有页面。

不要把 Token、Cookie、原始 cURL、HAR、完整抓包或个人信息提交到 Git。`lgycp_wx_miniprogram/.env` 和 `lgycp_wx_miniprogram/captures/` 已被忽略。

## 配置

GitHub Secrets：

| 名称 | 用途 |
|---|---|
| `LGYCP_WX_API_URL` | 课程接口 URL |
| `LGYCP_WX_API_HEADERS_JSON` | 必需请求头的 JSON object；没有时保存 `{}` |
| `LGYCP_WX_API_BODY_JSON` | JSON 请求体；GET 或无 body 时可为空 |
| `LGPAC_NOTIFY_EMAIL` | 收件地址 |
| `LGPAC_SMTP_USER` | SMTP 发件账号 |
| `LGPAC_SMTP_PASS` | SMTP 授权码 |
| `LGPAC_SMTP_SERVER` | SMTP 服务器，空值时默认 `smtp.qq.com` |
| `LGPAC_SMTP_PORT` | SMTP SSL 端口，空值时默认 `465` |

GitHub Variables：

| 名称 | 必需 | 用途 |
|---|---:|---|
| `LGYCP_WX_API_METHOD` | 否 | 默认 `GET` |
| `LGYCP_WX_ITEMS_PATH` | 是 | 课程数组点分路径 |
| `LGYCP_WX_TITLE_PATH` | 是 | 课程名称字段路径 |
| `LGYCP_WX_PUBLISHED_PATH` | 是 | 课程上架/发布时间字段路径 |
| `LGYCP_WX_ID_PATH` | 否 | 稳定课程 ID；没有时使用内容指纹 |
| `LGYCP_WX_CAMPUS_PATH` | 否 | 校区字段路径 |
| `LGYCP_WX_TERM_PATH` | 否 | 学期字段路径 |
| `LGYCP_WX_SCHEDULE_PATH` | 否 | 上课时间字段路径 |
| `LGYCP_WX_PRICE_PATH` | 否 | 价格字段路径 |
| `LGYCP_WX_REMAINING_PATH` | 否 | 剩余名额字段路径 |
| `LGYCP_WX_DETAIL_URL_PATH` | 否 | 课程详情 URL 字段路径 |

字段路径相对于单条课程 object；课程数组路径相对于整个响应。路径只支持字典 key 的点分访问，不执行表达式。

## 本地验证

在仓库根目录安装依赖并运行测试：

```bash
python -m pip install -r lgycp_wx_miniprogram/requirements.txt
python -m pytest lgycp_wx_miniprogram/tests -q
```

将真实配置放入当前 shell 环境后执行一次只读检查：

```bash
python -m lgycp_wx_miniprogram.main
```

第一次成功运行会更新 `lgycp_wx_miniprogram/data/archive.json`，但不会发邮件。不要在没有确认基线行为前删除这个文件。

## 自动运行

`.github/workflows/lgycp-wx-miniprogram-daily.yml` 使用 `5 3 * * *`，即每天 UTC 03:05、北京时间 11:05。也可以从 GitHub Actions 手工触发。

工作流依次执行离线测试、课程检查和归档提交。只有监控成功且归档发生变化时才提交 `lgycp_wx_miniprogram/data/archive.json`。

## 故障排查

- `configuration failed`：检查必需 Secret/Variable 是否为空，JSON 配置是否为 object。
- HTTP 401/403：认证信息已失效或接口拒绝请求；更新 Secret，不要把认证头打印到日志。
- HTTP 5xx 或网络超时：运行器最多重试两次，仍失败时保留旧归档。
- `course list is empty or invalid`：响应结构、字段路径或分页规则可能变化；不要用空结果覆盖归档。
- `no valid courses in response`：课程缺少名称或可解析的接口上架时间。
- `email delivery failed`：课程不会标记为已通知，下次成功运行会重试。
- `archive save failed`：旧归档通过原子替换得到保护；检查磁盘权限或文件状态。

日志只应包含状态码、字段路径和课程数量，不应包含请求头、请求体、Token、Cookie、SMTP 密码或个人信息。
