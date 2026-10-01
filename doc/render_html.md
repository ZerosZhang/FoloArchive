# core/render_html.py — 原始 HTML → 干净 HTML

## 职责

将步骤 2 下载的原始 HTML 批量渲染为干净的、适合手机阅读的 HTML（策略模式自动识别来源），并把 `<img>` 图片本地化到同级 `assets/`。

## 用法

```bash
# 自动扫描当天原始 HTML 并渲染
.venv/Scripts/python.exe src/core/render_html.py

# 渲染指定 HTML 文件
.venv/Scripts/python.exe src/core/render_html.py <html文件路径>
```

## 输入输出

- 输入：`result/temp_data/raw/<日期>/` 下的原始 HTML
- 输出：`result/<日期>/「来源」标题.html` + `assets/` 图片目录

原始 HTML 与成品 HTML 分目录存放，避免同名 `.html` 相互覆盖。

## 渲染流程

1. **提取元信息**：标题 / 原文链接（`og:url`、`canonical`）/ 来源（文件名提示 + `result/temp_data/「日期」.json` 匹配 feed_title、发布时间）
2. **策略识别**：`resolve_strategy(html, filename_hint)`，文件名提示优先于 JSON 的 feed_title
3. **提取正文**：`strategy.extract_body()` + `strategy.extract_blocks()` → `[(h2标题或None, 块HTML)]`，跳过 `skip_titles`
4. **清洗**：剥离 `<script>/<style>/<iframe>`、`on*` 事件属性、`javascript:` 链接
5. **套模板**：`render_document()` 输出含 `<!DOCTYPE html>`、`<meta charset>`、`<meta name="viewport">` 与内联响应式 CSS 的完整文档；`<header>` 保留来源/发布时间/原文链接，正文包在 `<article class="article-body">`，并预留 `<section class="ai-summary" data-folo="summary">` 空区块
6. **图片本地化**：从 `<img src>`（兼容 `data-src`/`data-lazy-src`/`data-original` 懒加载属性）收集远程 URL，并发下载到 `assets/`（用 URL 摘要命名，重跑幂等），把 `src` 改写为 `./assets/xxx`；已是 `./assets/...` 或 `data:` 的图片跳过

## 输出与状态

`scan_and_convert()` 返回 `{"success": [...], "failed": [...], "unknown": [...]}`：

| 状态 | 含义 |
|------|------|
| success | 识别来源并成功渲染 |
| failed | 识别了来源但渲染失败 |
| unknown | 未匹配任何策略（需新增策略） |

日志按显示宽度对齐（中文占 2 字符），状态列统一 `✓ [来源]` / `✗ [来源] 渲染失败` / `✗ 未知来源`。

## 新增来源

见 `doc/strategies.md`。
