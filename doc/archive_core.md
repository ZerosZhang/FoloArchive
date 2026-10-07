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
    "summary_result": {"processed", "failed", "failures", "path", "entries", "index_error"} 或 None,
    "step_times":     {步骤编号: 耗时秒},
    "error":          错误信息或 None,
    "failures":       字符串列表，**仅**汇总系统性/流程级失败（供失败邮件判定）,
}
```

## 流程

```
步骤 1 fetch    → folo_export.export_articles()
                  （当天 JSON 已存在时跳过，改为 _load_article_list 加载）
步骤 2 download → save_webpages.download_articles(overwrite=True) + optimize_titles()
                  （输出 result/temp_data/raw/<日期>/）
步骤 3 convert  → render_html.scan_and_convert()
                  （从 raw 读，成品写入 result/<日期>/）
步骤 4 summarize→ summarize：AI 摘要注入 + _write_index_page 生成当日索引页
汇总            → 成功/失败数量 + 失败原因明细 + 各步骤耗时
```

## 同一天重复运行

同一天再次整轮执行（`[1,2,3,4]`）时：

| 步骤 | 行为 |
|------|------|
| 1 fetch | **跳过抓取**，复用已有的 `result/temp_data/「日期」.json`（避免重复标记已读） |
| 2 download | **重新下载并覆盖** `temp_data/raw/<日期>/` 下的原始 HTML |
| 3 convert | **重新渲染并覆盖** `result/<日期>/` 下的成品 HTML |
| 4 summarize | **重新调用 API 生成摘要并覆盖**旧摘要区块，同时重建索引页 |

即「同一天重跑 = 复用列表 + 重下载 + 重渲染 + 重算摘要」。若要连同列表一起重抓，需先删除当天的 JSON 文件（或用 `--date` 指定另一个日期）。

## 行为要点

- 每步之间检查 `should_stop()`，停止时提前返回
- 步骤 1 **当天列表已存在则跳过抓取**：先算 `json_path = TEMP_DIR / f"「{today}」.json"`，若已存在，则打印跳过日志（并提示可删除该文件或改用 `--date` 强制重抓），再用 `_load_article_list(today)` 从该 JSON 加载列表，**不调用 `export_articles()`**（该步骤会把 Folo 未读标记为已读，绝不能在重跑时触发）；仅当 JSON 不存在时才调用 `export_articles()`
- 步骤 1 抓取分支无文章列表时直接返回（`article_list=None`）；认证失败/抓取异常写入 `failures`。跳过分支若无法加载列表则记入 `failures` 并返回，不作网络请求
- 步骤 2 单独运行时从 `result/temp_data/「日期」.json` 加载列表（`_load_article_list`）
- 步骤 2 以 **`overwrite=True`** 调用 `download_articles`，同一天重跑时重新下载并覆盖旧的原始 HTML（`save_webpages.py` 自身的 CLI 默认仍为 `overwrite=False`）
- 步骤 3 原始 HTML 与成品 HTML 分目录存放：原始在 `temp_data/raw/<日期>/`，成品在 `result/<日期>/`，避免同名相互覆盖；`scan_and_convert` 对 raw 目录下所有 `*.html` 逐个重渲染，**本来就是覆盖写**
- 步骤 4 每次都重新调用 API 生成摘要并覆盖旧摘要区块，**不再因「已有摘要」而跳过**
- 步骤 4 生成 `result/<日期>/YYYY年MM月DD日.html` 索引页，按来源分组（篇数降序 + 中文序号），每篇链接到成品 HTML 并附摘要；文章链接带 `target="_blank"`，**在新标签页打开**，避免从总览跳转时覆盖当前页面
- 索引页同样**只放内容**：`_write_index_page()` 调 `render_document()` 外链 `../style.css`，并调 `write_style_sheet()` 把样式落到数据根目录 `result/style.css`（与所有日期的文章页共用同一份样式表）
- **索引页以文章列表 JSON 为准**：`_build_index_entries()` 逐条遍历 `result/temp_data/「日期」.json`，用 `_expected_filename()`（`「{sanitize_filename(feed_title)}」{sanitize_filename(title)}.html`，与步骤 2 命名规则一致）推出期望文件名 → **JSON 有多少条，索引页就列多少条**；匹配时先精确命中，未命中再用 `_normalize_filename()`（去掉 `_1` 后缀）宽松匹配；目录中不在 JSON 里的成品文件也会补列，避免历史数据被漏掉
- 索引页条目三态：`ok`（有成品 + 有摘要）、`summary-failed`（有成品但摘要失败，页内 `.digest-warn` 标注并附原文链接）、`missing`（没有成品，页内 `.digest-failed` 标注「点击标题跳转原文」，**标题直链 JSON 的 `url`**）
- 缺失原因来自步骤 2/3 收集的 `missing_notes`（`下载失败：…` / `渲染失败：…` / 未识别来源），步骤 3 未跑或映射不到时用兜底文案
- `_summarize_step(today, log, article_list=None, missing_notes=None, should_stop=None)`：拿不到 `article_list` 时自行从 JSON 加载；**没有可总结的 HTML 时不直接返回**，只要拿得到 JSON 就照样出页（全 `missing`）；`config.json` 无效时摘要全部标为「未配置可用的 AI API」但仍出页；返回值的 `index_error` 记录索引页写入异常
- 索引页摘要块由 `_summary_digest()` 生成：**保留 AI 摘要原有的分行**（概括 / 背景 / 观点，或合集的「重点新闻：」条目），每行一个 `<br>`、跳过空行，不再把换行压成空格拼成一整段
- 失败聚合口径：**只有系统性/流程级失败**进 `failures` —— 步骤 1（认证/抓取失败、列表加载失败）、步骤 2 无法加载列表、整体 `error`、步骤 4 索引页写入失败；**步骤 2/3/4 的逐条内容失败不再进 `failures`**（不进邮件），只在索引页内标注，明细仍走日志与返回值的 `download` / `conversion` / `summary_result`
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
