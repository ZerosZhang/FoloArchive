# archive_core.py — 归档核心流程

## 职责

CLI（`archive.py`）与 GUI（`archive_gui.py`）共用的 5 步执行逻辑，自身不依赖任何界面框架，通过回调注入日志、进度与停止检查。

## 接口

```python
run_archive(selected_steps, today, log, on_progress=None, should_stop=None) -> dict
```

| 参数 | 说明 |
|------|------|
| `selected_steps` | 要执行的步骤编号列表，如 `[1, 2, 3, 4, 5]` |
| `today` | 日期，如 `"2026年08月24日"` |
| `log(message)` | 日志回调 |
| `on_progress(value, text)` | 进度回调（百分比、说明），可为 `None` |
| `should_stop() -> bool` | 停止检查回调，可为 `None` |

返回结构：

```python
{
    "article_list":   文章列表或 None,
    "download":       (成功数, 失败数, 跳过数, 失败明细列表),
    "conversion":     scan_and_convert 的结果 dict,
    "summary_result": {"processed", "failed", "failures", "path"} 或 None,
    "step_times":     {步骤编号: 耗时秒},
    "error":          错误信息或 None,
}
```

## 流程

```
步骤 1 fetch    → folo_export.export_articles()
步骤 2 download → save_webpages.download_articles() + optimize_titles()
步骤 3 convert  → html_to_md.scan_and_convert()
步骤 4 summarize→ summarize_md（按来源分组，收集失败原因）
步骤 5 import   → import_to_db.import_articles()
汇总            → 成功/失败数量 + 失败原因明细 + 各步骤耗时
```

## 行为要点

- 每步之间检查 `should_stop()`，停止时提前返回
- 步骤 1 无文章列表时直接返回（`article_list=None`）
- 步骤 2 单独运行时从 `result/temp_data/「日期」.json` 加载列表（`_load_article_list`）
- 步骤 4 总结失败原因收集在 `failures` 列表，汇总时逐条输出
- 输出目录 `result/YYYY年MM月DD日/`，路径来自 `utils.OUTPUT_BASE_DIR`

## 步骤定义

```python
STEPS = [
    (1, "fetch", "获取未读文章列表（含认证）"),
    (2, "download", "下载网页并优化文件名"),
    (3, "convert", "HTML → Markdown 转换"),
    (4, "summarize", "AI 生成文章总结"),
    (5, "import", "导入文章到数据库"),
]
```
