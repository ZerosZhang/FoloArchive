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
- 输出：`result/<日期>/「来源」标题.html` + `assets/` 图片目录，以及**数据根目录**的 `result/style.css`

原始 HTML 与成品 HTML 分目录存放，避免同名 `.html` 相互覆盖。

**HTML 只放内容，样式全在数据根目录的一份 `style.css`**：成品页 `<head>` 里只有
`<link rel="stylesheet" href="../style.css">`，没有任何 `<style>` 块。
`write_style_sheet(page_dir)` 把 `BASE_CSS` 写到 `page_dir` 的**上一级**（即 `result/`），
所以所有日期、所有页面（文章页 + 索引页）共用同一份样式：

- 改 `result/style.css` 一个文件，全部页面立刻跟着变，页面文件本身不用动；
- 内容与现有文件一致时跳过写入，重渲染不会白白改动 mtime（对 NAS / 同步盘友好）。

> 代价：`result/` 根下的 `style.css` 是日期目录之外的公共文件。单独把**一个**日期目录
> 拷到 NAS 会掉样式，需要连 `result/style.css` 一起带（整个 `result/` 同步则不受影响）。
> webui 预览不受影响：页面 URL 是 `/archive/<日期>/x.html`，`../style.css` 会被浏览器
> 解析成 `/archive/style.css`，正好命中 `result/style.css`。

## 渲染流程

1. **提取元信息**：标题 / 原文链接（`og:url`、`canonical`）/ 来源（文件名提示 + `result/temp_data/「日期」.json` 匹配 feed_title、发布时间）
2. **策略识别**：`resolve_strategy(html, filename_hint)`，文件名提示优先于 JSON 的 feed_title
3. **提取正文**：`strategy.extract_body()` + `strategy.extract_blocks()` → `[(h2标题或None, 块HTML)]`，跳过 `skip_titles`
4. **清洗**：剥离 `<script>/<style>/<iframe>`、`on*` 事件属性、`javascript:` 链接
5. **套模板**：`render_document()` 输出含 `<!DOCTYPE html>`、`<meta charset>`、`<meta name="viewport">` 与外链 `../style.css` 的完整文档；`<header>` 保留来源/发布时间/原文链接，正文包在 `<article class="article-body">`，并预留 `<section class="ai-summary" data-folo="summary">` 空区块
6. **图片本地化**：从 `<img src>`（兼容 `data-src`/`data-lazy-src`/`data-original` 懒加载属性）收集远程 URL，并发下载到 `assets/`（用 URL 摘要命名，重跑幂等），把 `src` 改写为 `./assets/xxx`；已是 `./assets/...` 或 `data:` 的图片跳过
7. **写样式**：`write_style_sheet()` 把 `BASE_CSS` 落到数据根目录 `result/style.css`（内容相同则跳过）

## 输出与状态

`scan_and_convert()` 返回 `{"success": [...], "failed": [...], "unknown": [...]}`：

| 状态 | 含义 |
|------|------|
| success | 识别来源并成功渲染 |
| failed | 识别了来源但渲染失败，或**原始文件读取失败**（记为「读取失败 异常类型: 原因」，如 `读取失败 PermissionError: [Errno 13] ...`） |
| unknown | 未匹配任何策略（需新增策略） |

日志按显示宽度对齐（中文占 2 字符），状态列统一 `✓ [来源]` / `✗ [来源] 渲染失败` / `✗ 未知来源`。

**容错**：单个文件出错不会中断整批 —— `scan_and_convert()` 的循环对每个文件都做了异常保护，出错仅计入 `failed` 并继续；`convert_file()` 读取原始 HTML 时会**重试 3 次**（间隔 0.5 秒），用于应对 Windows 上杀毒软件 / 同步盘对新写入文件的短暂占用（表现为 `PermissionError: [Errno 13]`）。

## 新增来源

见 `doc/strategies.md`。
