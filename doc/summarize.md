# core/summarize.py — AI 生成文章摘要

## 职责

调用 DeepSeek API 为当天目录下的 HTML 文章生成摘要，注入每篇文章的摘要区块
（`<section class="ai-summary" data-folo="summary">`）。**每次运行都会重新生成并覆盖旧摘要**（同一天重跑不会跳过），**不再涉及任何数据库或关键词。**

## 用法

```bash
.venv/Scripts/python.exe src/core/summarize.py                 # 当天文章
.venv/Scripts/python.exe src/core/summarize.py 5               # 限制处理 5 篇（调试）
.venv/Scripts/python.exe src/core/summarize.py 2026年07月07日   # 指定日期
```

## 配置

`src/config.json`（模板见 `src/config.example.json`）：
```json
{"api_key": "sk-xxx", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"}
```

## 行为要点

- 并发数 `MAX_WORKERS = 10`（`ThreadPoolExecutor`），文件写入加锁
- 扫描 `result/YYYY年MM月DD日/` 下的 `.html` 文件，**排除当天索引页** `YYYY年MM月DD日.html`
- 摘要注入 `data-folo="summary"` 区块；**每次都调用 API 重新生成并覆盖写入**（`write_summary_to_html` 先清掉旧区块内容再写，保证不叠加、区块唯一），因此同一天重跑会重算摘要
- 区块缺失时按 `</header>` 或 `<main class="wrap">` 位置补插
- 调用 API 前先把 HTML 转成纯文本（剥离脚本、样式与标签），避免样式干扰
- 摘要提示词：判断合集/单一主题两种格式，输出大白话概括；不再要求关键词
- API 失败重试 3 次；摘要截断（`finish_reason=length`）时重试
- **不生成索引页**：当日索引页 `YYYY年MM月DD日.html` 由 `archive_core._write_index_page` 生成

## 输出

摘要写入各文章 HTML 的摘要区块，控制台按来源分组打印处理进度与失败原因。
