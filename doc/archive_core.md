# archive_core.py — 归档核心流程

## 职责

CLI（`archive.py`）与网页版界面（`webui.py`）共用的 4 步执行逻辑，自身不依赖任何界面框架，通过回调注入日志、进度与停止检查。

## 接口

```python
run_archive(selected_steps, today, log, on_progress=None, should_stop=None) -> dict
```

| 参数 | 说明 |
|------|------|
| `selected_steps` | 要执行的步骤编号列表，如 `[1, 2, 3, 4]` |
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
    "failures":       字符串列表，汇总任一环节的失败原因（供失败邮件判定）,
}
```

## 流程

```
步骤 1 fetch    → folo_export.export_articles()
步骤 2 download → save_webpages.download_articles() + optimize_titles()
                  （输出 result/temp_data/raw/<日期>/）
步骤 3 convert  → render_html.scan_and_convert()
                  （从 raw 读，成品写入 result/<日期>/）
步骤 4 summarize→ summarize：AI 摘要注入 + _write_index_page 生成当日索引页
汇总            → 成功/失败数量 + 失败原因明细 + 各步骤耗时
```

## 行为要点

- 每步之间检查 `should_stop()`，停止时提前返回
- 步骤 1 无文章列表时直接返回（`article_list=None`）；认证失败/抓取异常写入 `failures`
- 步骤 2 单独运行时从 `result/temp_data/「日期」.json` 加载列表（`_load_article_list`）
- 步骤 3 原始 HTML 与成品 HTML 分目录存放：原始在 `temp_data/raw/<日期>/`，成品在 `result/<日期>/`，避免同名相互覆盖
- 步骤 4 生成 `result/<日期>/YYYY年MM月DD日.html` 索引页，按来源分组（篇数降序 + 中文序号），每篇链接到成品 HTML 并附一句摘要；文章链接带 `target="_blank"`，**在新标签页打开**，避免从总览跳转时覆盖当前页面
- 失败聚合：步骤 1（认证/抓取）、步骤 2（逐条下载失败）、步骤 3（渲染失败/未识别来源）、步骤 4（逐条摘要失败）以及整体 `error` 全部追加进 `failures`
- 输出目录 `result/YYYY年MM月DD日/`，路径来自 `utils.OUTPUT_BASE_DIR` / `utils.RAW_DIR`

## 步骤定义

```python
STEPS = [
    (1, "fetch", "获取 folo 的未读文章列表"),
    (2, "download", "下载网页并优化文件名"),
    (3, "convert", "渲染为 HTML"),
    (4, "summarize", "AI 摘要与索引页生成"),
]
```

## 进度区间

`on_progress` 的百分比按 4 步推进：步骤 1 → 10；步骤 2 → 20~50；步骤 3 → 60；步骤 4 → 80；结束后由调用方补 100。
