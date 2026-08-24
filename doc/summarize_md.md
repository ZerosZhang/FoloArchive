# core/summarize_md.py — AI 生成文章摘要

## 职责

调用 DeepSeek API 为 Markdown 文章生成摘要，写入文章文件顶部（Obsidian `> [!abstract]` 语法），并同步导入 SQLite。

## 用法

```bash
.venv/Scripts/python.exe src/core/summarize_md.py                 # 当天文章
.venv/Scripts/python.exe src/core/summarize_md.py 5               # 限制处理 5 篇（调试）
.venv/Scripts/python.exe src/core/summarize_md.py 2026年07月07日   # 指定日期
```

## 配置

`src/config.json`（模板见 `src/config.example.json`）：
```json
{"api_key": "sk-xxx", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"}
```

## 行为要点

- 并发数 `MAX_WORKERS = 10`（`ThreadPoolExecutor`），文件写入加锁
- 扫描 `result/YYYY年MM月DD日/` 下 `.md` 文件，排除同名总结文件
- 已有 `[!abstract]` 的文章跳过（复用原摘要）
- 摘要提示词：判断合集/单一主题两种格式，输出大白话概括 + 关键词，禁止术语黑话
- API 失败重试 3 次；摘要截断（`finish_reason=length`）时重试
- 关键词写入数据库 `article_keywords` 关联表
- 输出每日总结文件 `result/YYYY年MM月DD日/YYYY年MM月DD日.md`（按来源分组）
