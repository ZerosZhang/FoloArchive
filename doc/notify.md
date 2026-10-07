# core/notify.py — 失败邮件通知

## 职责

归档流程结束后，若存在失败项，把失败详情通过 SMTP 邮件发出。设计为**可选项**：未配置时静默跳过，发信失败也绝不影响归档主流程。

## 配置（`src/config.json` 的 `mail` 段）

```json
"mail": {
  "enabled": false,
  "smtp_host": "smtp.qq.com",
  "smtp_port": 465,
  "use_ssl": true,
  "username": "you@example.com",
  "password": "邮箱授权码",
  "from": "you@example.com",
  "to": ["you@example.com"]
}
```

- `enabled` 缺省视为开启；只有显式写 `false` 才关闭
- 关键字段：`smtp_host`、`smtp_port`、`username`、`password`、`from`（`to` 可省略，默认发给发件人自己）
- **字段命名兼容**（模板用左侧，也接受右侧常见简写）：
  - `smtp_host` ← `smtp_server` / `host`
  - `username` ← `sender` / `from`
  - `password` ← `auth_code` / `pass`
  - `from` ← `sender` / `username`
- `to` 兼容单个字符串或列表
- `use_ssl` 缺省时按端口推断（465 → SSL，其余 → STARTTLS）
- 邮件为**明文密码**存储，注意 `config.json` 权限，勿提交 git

## 接口

| 函数 | 说明 |
|------|------|
| `load_mail_config()` | 读 `mail` 段并规范化；文件缺失 / 无 `mail` 段 / `enabled` 为假 / 字段不全 → 返回 `None`（**不 `sys.exit`**，避免「没配邮件」变成「归档退出」） |
| `send_failure_mail(mail_config, subject, body)` | `smtplib` + `EmailMessage` 发信；支持 SSL(465) 与 STARTTLS(587)；返回 `(success, message)`，任何异常都被吞掉 |
| `format_failure_report(today, failures, step_times)` | 把失败列表整理成纯文本正文（日期、逐条失败、各步骤耗时与合计） |

- 网络超时 `SMTP_TIMEOUT = 30` 秒，避免异常时长时间挂起
- 失败详情来自 `archive_core.run_archive()` 返回值的 `failures` 字段；步骤编号到中文名的映射见 `STEP_NAMES`
- `failures` **只包含系统性/流程级失败**（步骤 1 抓取或列表加载失败、流程 `error`、索引页写入失败）。
  下载 / 渲染 / AI 摘要的逐条内容失败不进 `failures`，改在当日索引页 `YYYY年MM月DD日.html` 内就地标注
  （`digest-warn` / `digest-failed`），因此**不会**再触发失败邮件

## 自测

```bash
.venv/Scripts/python.exe src/core/notify.py   # 只打印当前邮件配置与正文样例，不发信
```

## 接入状态

已被 `archive.py` 与网页版界面 `webui.py` 接入：每次运行结束后若 `failures` 非空即尝试发信；未配置 `mail` 段时只记一条日志。由于内容级失败已改在索引页标注，邮件只会在抓取/流程级问题上触发。
