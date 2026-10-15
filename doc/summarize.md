# core/summarize.py — AI 生成文章摘要

## 职责

调用 DeepSeek API 为当天目录下的 HTML 文章生成摘要，注入每篇文章的摘要区块
（`<section class="ai-summary" data-folo="summary">`）。**默认断点续跑：已有摘要的文章不再调用 API，直接复用；加 `--force` 才全部重算。** 不涉及任何数据库或关键词。

## 用法

```bash
.venv/Scripts/python.exe src/core/summarize.py                 # 当天文章（跳过已有摘要）
.venv/Scripts/python.exe src/core/summarize.py --force         # 当天文章，全部重算
.venv/Scripts/python.exe src/core/summarize.py 5               # 限制处理 5 篇（调试）
.venv/Scripts/python.exe src/core/summarize.py 2026年07月07日   # 指定日期
```

`--force` / `-f` 与日期、篇数上限可任意组合。

## 配置

`src/config.json`（模板见 `src/config.example.json`）：
```json
{"api_key": "sk-xxx", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"}
```

## 行为要点

- 并发数 `MAX_WORKERS = 10`（`ThreadPoolExecutor`），文件写入加锁
- 客户端建为 `timeout=60.0`、`max_retries=0`——单条请求最多阻塞 60 秒，重试由本模块自己管
- **关掉模型思考**：请求带 `extra_body={"thinking": {"type": "disabled"}}`（模块常量 `THINKING_BODY`）。这个模型默认会先思考，摘要任务用不上——实测同一条 1.7k 字文章，关闭后 `completion` 从 1626 降到 216 token、耗时从 9.5s 降到 2.6s，摘要质量不变。`reasoning_effort="minimal"` 无效（思考 token 仍有 1276）
- 扫描 `result/YYYY年MM月DD日/` 下的 `.html` 文件，**排除当天索引页** `YYYY年MM月DD日.html`
- **断点续跑**：`process_article()` 先读 `data-folo="summary"` 区块，区块里有非空文本就直接复用、不调 API（返回带 `skipped=True`）；只有 `force=True` 才重新生成
- 摘要注入 `data-folo="summary"` 区块；写入时先清掉旧区块内容再写，保证不叠加、区块唯一
- 区块缺失时按 `</header>` 或 `<main class="wrap">` 位置补插
- 调用 API 前先把 HTML 转成纯文本（剥离脚本、样式与标签），避免样式干扰
- 摘要提示词：判断合集/单一主题两种格式，输出大白话概括；不再要求关键词
- **重试策略**：最多 3 次。可重试错误（网络中断、超时，以及 408/409/425/429/5xx）按 `RETRY_BACKOFF * 2^n` 等待 2s → 4s 递增后重试；401/400 等不可重试错误立即返回，不浪费重试次数
- 每次重试打印一行 `↻ 文件名: 原因，Ns 后重试（第 n/3 次）`，不再静默卡住
- 摘要被截断（`finish_reason=length`）也按重试处理
- **不生成索引页**：当日索引页 `YYYY年MM月DD日.html` 由 `archive_core._write_index_page` 生成；该页以 `temp_data/「日期」.json` 的条目为准，本模块摘要失败的条目会在页内标注（`digest-warn`）

## 输出

摘要写入各文章 HTML 的摘要区块，控制台按来源分组打印处理进度（`✓` 新生成 / `·` 跳过 / `✗` 失败）与失败原因。
