#!/usr/bin/env python3
"""
将文章 HTML 批量渲染为干净的、适合手机阅读的 HTML（支持多来源自动识别）

流程：从 result/temp_data/raw/<日期>/ 读取原始 HTML，
      每个来源用对应策略提取正文 → 清洗 → 套上响应式模板 →
      本地化 <img> 图片到同级 assets/ → 写入 result/<日期>/。

用法:
    # 自动扫描当天原始 HTML 并渲染
    python render_html.py

    # 渲染指定 HTML 文件
    python render_html.py <html文件路径>

输出:
    日期目录下的同名 .html 成品文件（HTML 只放内容，样式外链）
    + 数据根目录 result/style.css：所有日期、所有页面共用的一份样式表
"""

import hashlib
import html
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

# src/ 目录（strategies 包所在），core/ 由同目录导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import resolve_strategy
from utils import TEMP_DIR, RAW_DIR, OUTPUT_BASE_DIR


# =============================================================================
# 1. 响应式模板
# =============================================================================

# 公共样式表：放在数据根目录（result/），日期目录下的页面用 ../style.css 引用它。
# 一处修改，所有日期的文章页与索引页立刻跟着变。
STYLE_FILENAME = "style.css"
STYLE_HREF = f"../{STYLE_FILENAME}"

# 文章页与索引页共用的样式，保证手机端可读
BASE_CSS = """
* { box-sizing: border-box; }
body {
  margin: 0; padding: 16px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  line-height: 1.7; font-size: 16px; color: #1a1a1a; background: #f5f5f5;
}
.wrap { max-width: 1140px; margin: 0 auto; background: #fff; padding: 24px; border-radius: 8px; }
.article-header { border-bottom: 1px solid #e5e5e5; padding-bottom: 12px; margin-bottom: 16px; }
.article-title { font-size: 1.6em; line-height: 1.3; margin: 0 0 8px; }
.article-meta { font-size: 0.85em; color: #666; display: flex; flex-direction: column; gap: 2px; word-break: break-all; }
.article-meta a { color: #0b6bcb; }
.ai-summary { background: #f0f6ff; border-left: 4px solid #0b6bcb; padding: 12px 16px; border-radius: 4px; margin: 16px 0; }
.ai-summary:empty { display: none; }
.article-body img, .article-body video { max-width: 100%; height: auto; }
.article-body img { border-radius: 4px; }
.article-body pre { background: #f6f8fa; padding: 12px; border-radius: 6px; overflow-x: auto; }
.article-body code { font-family: ui-monospace, Consolas, "Microsoft YaHei", monospace; font-size: 0.9em; }
.article-body :not(pre) > code { background: #f0f0f0; padding: 1px 4px; border-radius: 3px; }
.article-body blockquote { margin: 12px 0; padding: 4px 16px; border-left: 3px solid #ddd; color: #555; }
.article-body table { border-collapse: collapse; display: block; overflow-x: auto; max-width: 100%; }
.article-body th, .article-body td { border: 1px solid #ddd; padding: 6px 10px; }
.article-body h2 { font-size: 1.25em; margin-top: 1.5em; }
.article-body a { color: #0b6bcb; word-break: break-all; }
.empty { color: #999; }
.index-source { margin: 24px 0 8px; font-size: 1.15em; }
.index-list { list-style: none; padding: 0; margin: 0; }
.index-item { padding: 10px 0; border-bottom: 1px solid #eee; }
.index-item > a { font-weight: 600; color: #0b6bcb; }
.index-item .digest { color: #555; font-size: 0.9em; margin-top: 4px; }
.index-item .digest-failed { color: #b3261e; }
.index-item .digest-warn { color: #8a6d00; }
.index-intro { color: #666; font-size: 0.9em; }
@media (max-width: 600px) {
  body { padding: 0; }
  .wrap { padding: 16px; border-radius: 0; }
}
""".strip()


def write_style_sheet(page_dir):
    """把公共样式表写到 page_dir 的**上一级**（数据根目录，如 result/），返回其路径

    日期目录下的页面都链 `../style.css`，所以样式表放在数据根目录而不是每个日期
    目录各一份：改这一个文件，所有日期的页面立刻跟着变。整个 result/ 共用一份。

    内容与现有文件一致时跳过写入：重渲染不会白白改动 mtime，对 NAS / 同步盘更友好。
    """
    path = Path(page_dir).parent / STYLE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_text(encoding="utf-8") == BASE_CSS:
            return path
    except OSError:
        pass
    path.write_text(BASE_CSS, encoding="utf-8")
    return path


def render_document(title, body_html, lang="zh-CN"):
    """用统一模板包裹正文，输出完整 HTML 文档

    HTML 只放内容，样式外链到数据根目录的 style.css（由 write_style_sheet() 生成）。
    """
    return (
        "<!DOCTYPE html>\n"
        f'<html lang="{lang}">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        f'<link rel="stylesheet" href="{STYLE_HREF}">\n'
        "</head>\n"
        "<body>\n"
        '<main class="wrap">\n'
        f"{body_html}\n"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )


# =============================================================================
# 2. HTML 清洗
# =============================================================================

def clean_html(fragment):
    """剥离脚本/样式/内嵌框架，移除事件属性与 javascript: 链接"""
    if not fragment:
        return ""
    # 无害化脚本、样式、内嵌框架
    fragment = re.sub(r"<script\b[^>]*>.*?</script>", "", fragment, flags=re.DOTALL | re.IGNORECASE)
    fragment = re.sub(r"<style\b[^>]*>.*?</style>", "", fragment, flags=re.DOTALL | re.IGNORECASE)
    fragment = re.sub(r"<iframe\b[^>]*>.*?</iframe>", "", fragment, flags=re.DOTALL | re.IGNORECASE)
    fragment = re.sub(r"<iframe\b[^>]*/?>", "", fragment, flags=re.IGNORECASE)
    # 移除 on* 事件属性（"值" / '值' / 裸值三种写法）
    fragment = re.sub(r'\son\w+\s*=\s*"[^"]*"', "", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"\son\w+\s*=\s*'[^']*'", "", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"\son\w+\s*=\s*[^\s>]+", "", fragment, flags=re.IGNORECASE)
    # javascript: 链接失效化
    fragment = re.sub(
        r'(href\s*=\s*["\'])\s*javascript:[^"\']*(["\'])',
        r"\1#\2", fragment, flags=re.IGNORECASE,
    )
    return fragment


# =============================================================================
# 3. 元信息提取
# =============================================================================

def format_datetime(dt_str):
    """将 ISO 格式时间转换为 2026年07月04日 08:00:40 格式"""
    if not dt_str:
        return ""
    try:
        dt_str = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%Y年%m月%d日 %H:%M:%S")
    except (ValueError, AttributeError):
        return dt_str


def extract_title(html_text):
    m = re.search(r"<title>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    if m:
        title = m.group(1).strip()
        if " - " in title:
            title = title.rsplit(" - ", 1)[0]
        elif " | " in title:
            title = title.rsplit(" | ", 1)[0]
        return title
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return "无标题"


def extract_html_url(html_text):
    m = re.search(r'<meta[^>]*(?:property|name)="og:url"[^>]*content="([^"]+)"', html_text, re.IGNORECASE)
    if m:
        url = m.group(1).strip()
        if url.startswith("/"):
            if "sspai.com" in html_text:
                url = "https://sspai.com" + url
            elif "appinn.com" in html_text:
                url = "https://www.appinn.com" + url
        return url

    m = re.search(r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"', html_text, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    return None


def guess_source_from_filename(filename):
    """从文件名 「来源」标题.html 中提取来源"""
    m = re.search(r'「([^」]+)」', filename)
    return m.group(1) if m else ""


def load_article_meta_from_json(html_path, html_title, html_url):
    """从 TempData/「日期」.json 中查找对应文章的元信息"""
    html_path = Path(html_path)
    date_folder = html_path.parent.name

    # 文章列表 JSON 统一在 result/temp_data/
    json_paths = [
        TEMP_DIR / f"「{date_folder}」.json",
        TEMP_DIR / f"{date_folder}.json",
    ]

    article_list = None
    for json_path in json_paths:
        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    article_list = json.load(f)
                break
            except (json.JSONDecodeError, IOError):
                continue

    if not article_list:
        return None

    filename = html_path.stem

    if html_url:
        for article in article_list:
            json_url = article.get("url", "")
            if json_url and (json_url == html_url or json_url.endswith(html_url) or html_url.endswith(json_url)):
                return {
                    "feed_title": article.get("feed_title", "未知来源"),
                    "url": json_url,
                    "published": article.get("published", ""),
                    "summary": article.get("summary", ""),
                }

    if html_title:
        for article in article_list:
            json_title = article.get("title", "")
            # 精确匹配
            if json_title == html_title:
                return {
                    "feed_title": article.get("feed_title", "未知来源"),
                    "url": article.get("url", ""),
                    "published": article.get("published", ""),
                    "summary": article.get("summary", ""),
                }
            # 模糊匹配：标题包含
            if json_title and html_title and (json_title in html_title or html_title in json_title):
                return {
                    "feed_title": article.get("feed_title", "未知来源"),
                    "url": article.get("url", ""),
                    "published": article.get("published", ""),
                    "summary": article.get("summary", ""),
                }

    # 尝试从文件名中提取标题进行匹配
    m = re.search(r'「[^」]+」(.+)', filename)
    if m:
        file_title = m.group(1)
        for article in article_list:
            json_title = article.get("title", "")
            if file_title and file_title in json_title:
                return {
                    "feed_title": article.get("feed_title", "未知来源"),
                    "url": article.get("url", ""),
                    "published": article.get("published", ""),
                    "summary": article.get("summary", ""),
                }

    return None


# =============================================================================
# 4. 图片本地化（基于 <img src> 解析）
# =============================================================================

def _get_base_url(url):
    """从完整 URL 中提取 scheme + netloc"""
    if not url:
        return ""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _guess_ext_from_content(response):
    """根据 HTTP Content-Type 猜测图片扩展名"""
    content_type = response.headers.get("Content-Type", "").lower()
    type_map = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/bmp": ".bmp",
    }
    for mime, ext in type_map.items():
        if mime in content_type:
            return ext
    return ".png"


def _get_referer_for_url(image_url):
    """根据图片 URL 的域名返回对应的 Referer，用于绕过反盗链"""
    parsed = urlparse(image_url)
    host = parsed.netloc.lower()
    if "sspai.com" in host:
        return "https://sspai.com/"
    if "weixin.qq.com" in host or "mmbiz" in host or "sinaimg.cn" in host:
        return "https://mp.weixin.qq.com/"
    if "hellogithub.com" in host:
        return "https://hellogithub.com/"
    return None


def _download_image(args):
    """下载单张图片的辅助函数，带重试"""
    orig_url, full_url = args
    last_error = None
    for attempt in range(3):
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            referer = _get_referer_for_url(full_url)
            if referer:
                headers["Referer"] = referer
            req = Request(full_url, headers=headers)
            with urlopen(req, timeout=10) as response:
                data = response.read()

                # 优先从 URL 获取扩展名，否则从 Content-Type 猜测
                parsed = urlparse(full_url)
                orig_ext = Path(parsed.path).suffix.lower()
                if orig_ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"):
                    ext = orig_ext if orig_ext != ".jpeg" else ".jpg"
                else:
                    ext = _guess_ext_from_content(response)

                return (orig_url, data, ext, None)
        except Exception as e:
            last_error = str(e)
            if attempt < 2:
                import time
                time.sleep(1)
    return (orig_url, None, None, last_error)


# 单个 <img ...> 标签
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


def _attr_pattern(attr):
    """匹配某属性（避免 src 误匹配到 data-src）"""
    return re.compile(
        rf'(?<![-\w]){re.escape(attr)}\s*=\s*(["\'])(.*?)\1',
        re.IGNORECASE | re.DOTALL,
    )


def _img_source(tag):
    """从 <img> 标签中取出图源，返回 (属性名, url)；支持懒加载属性"""
    for attr in ("src", "data-src", "data-lazy-src", "data-original"):
        m = _attr_pattern(attr).search(tag)
        if m and m.group(2).strip():
            return attr, m.group(2).strip()
    return None, ""


def _set_img_src(tag, local_url):
    """把 <img> 的 src 改写为本地路径，缺失时插入 src"""
    m = _attr_pattern("src").search(tag)
    if m:
        return tag[:m.start(2)] + local_url + tag[m.end(2):]
    return re.sub(r"<img\b", f'<img src="{local_url}"', tag, count=1, flags=re.IGNORECASE)


def _is_local_or_data(url):
    """已是本地相对路径或内联数据的图片无需下载"""
    return url.startswith(("data:", "#", "javascript:")) or url.startswith(
        ("./assets/", "assets/", "../assets/")
    )


def _resolve_full_url(url, base, page_url):
    """把图片地址解析为绝对 URL"""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return base + url
    if url.startswith(("http://", "https://")):
        return url
    if not page_url:
        return None
    resolved = urljoin(page_url, url)
    return resolved if resolved.startswith(("http://", "https://")) else None


def _collect_image_refs(html_text, base_url):
    """收集 <img> 远程图源。

    返回 (mapping, fulls)：
      - mapping: {原始 url: 绝对 url}
      - fulls:   去重后的绝对 url 列表（按出现顺序）
    """
    base = _get_base_url(base_url)
    mapping = {}
    fulls = []
    for m in _IMG_TAG_RE.finditer(html_text):
        _, url = _img_source(m.group(0))
        if not url or _is_local_or_data(url):
            continue
        full = _resolve_full_url(url, base, base_url)
        if not full:
            continue
        mapping.setdefault(url, full)
        if full not in fulls:
            fulls.append(full)
    return mapping, fulls


def _rewrite_images(html_text, downloaded):
    """把 <img> 的远程地址替换为 ./assets/<文件名>（downloaded: {原始 url: 文件名}）"""

    def repl(m):
        tag = m.group(0)
        _, url = _img_source(tag)
        if not url or url not in downloaded:
            return tag
        return _set_img_src(tag, f"./assets/{downloaded[url]}")

    return _IMG_TAG_RE.sub(repl, html_text)


def _asset_name(date_prefix, full_url, ext):
    """用 URL 摘要命名，保证重跑时同名复用（幂等）"""
    digest = hashlib.sha1(full_url.encode("utf-8")).hexdigest()[:10]
    return f"{date_prefix}_{digest}{ext}"


def localize_images(html_text, base_url, assets_dir, date_prefix=None):
    """下载 HTML 中的远程图片到 assets_dir，并把 src 改写为相对路径"""
    assets_dir = Path(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)

    if not date_prefix:
        date_prefix = datetime.now().strftime("%Y年%m月%d日")

    mapping, fulls = _collect_image_refs(html_text, base_url)
    if not fulls:
        return html_text

    full_to_name = {}
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(_download_image, (full, full)) for full in fulls]
        for future in as_completed(futures):
            orig, data, ext, _error = future.result()
            if data is not None:
                name = _asset_name(date_prefix, orig, ext)
                (assets_dir / name).write_bytes(data)
                full_to_name[orig] = name

    downloaded = {
        orig: full_to_name[full]
        for orig, full in mapping.items()
        if full in full_to_name
    }
    return _rewrite_images(html_text, downloaded)


# =============================================================================
# 5. 单文件渲染
# =============================================================================

def _render_article_body(blocks, strategy):
    """把策略返回的 blocks 渲染为 <article> 内容"""
    parts = []
    for h2_title, block_html in blocks:
        if h2_title and any(skip in h2_title for skip in strategy.skip_titles):
            continue
        if h2_title:
            parts.append(f"<h2>{html.escape(h2_title)}</h2>")
        cleaned = clean_html(block_html)
        if cleaned.strip():
            parts.append(cleaned)

    if not parts:
        return '<article class="article-body"><p class="empty">⚠️ 无法提取文章内容。</p></article>'
    return '<article class="article-body">\n' + "\n".join(parts) + "\n</article>"


def _render_header(title, meta, strategy_name):
    """渲染文章头部：标题 / 来源 / 发布时间 / 原文链接"""
    feed = (meta.get("feed_title") or "") or strategy_name
    url = meta.get("url", "") or ""
    published = format_datetime(meta.get("published", ""))

    meta_lines = []
    if feed:
        meta_lines.append(f'<span class="meta-source">来源：{html.escape(feed)}</span>')
    if published:
        meta_lines.append(f'<span class="meta-published">发布时间：{html.escape(published)}</span>')
    if url:
        safe_url = html.escape(url, quote=True)
        meta_lines.append(
            f'<span class="meta-origin">原文链接：'
            f'<a href="{safe_url}" rel="noopener" target="_blank">{html.escape(url)}</a></span>'
        )

    return (
        '<header class="article-header">\n'
        f'<h1 class="article-title">{html.escape(title)}</h1>\n'
        f'<div class="article-meta">{"".join(meta_lines)}</div>\n'
        "</header>"
    )


def _read_html_text(path, attempts=3, delay=0.5):
    """读取原始 HTML 文本

    Windows 下刚写完的文件可能被杀毒软件/同步盘短暂占用，表现为
    PermissionError（Errno 13）。这里重试几次，避免个别文件导致整批失败。
    """
    import time

    last_error = None
    for attempt in range(attempts):
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(delay)
    raise last_error


def convert_file(html_path, output_path=None, download_img=True):
    """渲染单个原始 HTML。

    返回 (output_path, source_name, success, html_text, base_url)；
    未匹配到策略时返回 (None, None, False, "", "")。
    """
    html_path = Path(html_path)
    if not html_path.exists():
        return None, "", False, "", ""

    html_text = _read_html_text(html_path)
    title = extract_title(html_text)
    html_url = extract_html_url(html_text)

    # 文件名来源提示
    filename_hint = guess_source_from_filename(html_path.name)

    # 从 JSON 获取元信息
    meta = load_article_meta_from_json(html_path, title, html_url)
    feed_title = meta.get("feed_title", "") if meta else ""

    # 确定策略（文件名提示优先，其次 JSON 的 feed_title）
    strategy = resolve_strategy(html_text, filename_hint)
    if not strategy and feed_title:
        strategy = resolve_strategy(html_text, feed_title)

    if not strategy:
        return None, None, False, "", ""

    # 兜底元信息
    if not meta:
        meta = {
            "feed_title": feed_title or strategy.name,
            "url": html_url or "",
            "published": "",
        }

    # 提取正文并渲染
    body_html = strategy.extract_body(html_text)
    blocks = strategy.extract_blocks(body_html) if body_html.strip() else []
    inner = _render_article_body(blocks, strategy)

    page_body = (
        _render_header(title, meta, strategy.name)
        + '\n<section class="ai-summary" data-folo="summary"></section>\n'
        + inner
    )
    page = render_document(title, page_body)

    if output_path is None:
        output_path = OUTPUT_BASE_DIR / html_path.parent.name / f"{html_path.stem}.html"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    base_url = meta.get("url") or html_url or ""

    if download_img:
        page = localize_images(page, base_url, output_path.parent / "assets", output_path.parent.name)

    write_style_sheet(output_path.parent)
    output_path.write_text(page, encoding="utf-8")
    return output_path, strategy.name, True, page, base_url


# =============================================================================
# 6. 批量渲染主入口
# =============================================================================

def _display_width(s):
    """字符串显示宽度（中文字符按 2 计算）"""
    return sum(2 if ord(c) > 0xFF else 1 for c in s)


def _truncate_display(s, max_width):
    """按显示宽度截断字符串"""
    width = 0
    for i, c in enumerate(s):
        w = 2 if ord(c) > 0xFF else 1
        if width + w > max_width:
            return s[:i]
        width += w
    return s


def scan_and_convert(day_folder=None):
    """
    扫描 result/temp_data/raw/<日期>/ 中的原始 HTML，渲染到 result/<日期>/。
    day_folder: 如 "2026年05月18日"，默认当天。
    返回 results 字典：{"success": [...], "failed": [...], "unknown": [...]}
    """
    if day_folder is None:
        day_folder = datetime.now().strftime("%Y年%m月%d日")

    input_dir = RAW_DIR / day_folder
    output_dir = OUTPUT_BASE_DIR / day_folder
    if not input_dir.exists():
        print(f"错误: 原始 HTML 目录不存在: {input_dir}")
        return {"success": [], "failed": [], "unknown": []}

    html_files = sorted(input_dir.glob("*.html"))
    if not html_files:
        print(f"未在 {input_dir} 中找到 HTML 文件")
        return {"success": [], "failed": [], "unknown": []}

    print("=" * 60)
    print("HTML 渲染（干净 HTML 输出）")
    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")
    print(f"共 {len(html_files)} 个 HTML 文件")
    print("=" * 60)
    print()

    results = {"success": [], "failed": [], "unknown": []}

    # 第一阶段：渲染所有文章，收集图片 URL
    articles_data = []  # (output_path, html_text, base_url)
    full_urls = []      # 去重后的图片绝对 URL
    total_width = len(str(len(html_files)))

    for i, html_path in enumerate(html_files, 1):
        stem = html_path.stem
        name = _truncate_display(stem, 40)
        if len(name) != len(stem):
            name = _truncate_display(stem, 39) + "…"
        pad = " " * (40 - _display_width(name))
        print(f"[{i:>{total_width}}/{len(html_files)}] {name}{pad}  ", end="", flush=True)

        out_path = output_dir / f"{html_path.stem}.html"
        try:
            out_path, source, success, page, base_url = convert_file(
                html_path, out_path, download_img=False
            )
        except Exception as exc:  # 单个文件失败不应中断整批
            detail = f"{type(exc).__name__}: {exc}"[:160]
            print(f"✗ 渲染异常 ({detail})", flush=True)
            results["failed"].append((html_path.name, f"读取失败 {detail}"))
            continue

        if success:
            print(f"✓ [{source}]", flush=True)
            results["success"].append((html_path.name, source))
            articles_data.append((out_path, page, base_url))

            _, fulls = _collect_image_refs(page, base_url)
            for full in fulls:
                if full not in full_urls:
                    full_urls.append(full)
        elif source and source != "未知":
            print(f"✗ [{source}] 渲染失败", flush=True)
            results["failed"].append((html_path.name, source))
        else:
            print("✗ 未知来源", flush=True)
            results["unknown"].append((html_path.name, "未知来源"))

    # 第二阶段：批量并发下载所有图片（按 URL 摘要命名，天然去重）
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    date_prefix = day_folder
    full_to_name = {}

    if full_urls:
        print(flush=True)
        print(f"正在批量下载 {len(full_urls)} 张图片...", flush=True)

        with ThreadPoolExecutor(max_workers=30) as executor:
            futures = [executor.submit(_download_image, (full, full)) for full in full_urls]
            for future in as_completed(futures):
                orig, data, ext, _error = future.result()
                if data is not None:
                    name_file = _asset_name(date_prefix, orig, ext)
                    (assets_dir / name_file).write_bytes(data)
                    full_to_name[orig] = name_file

        print(f"✓ 图片下载完成: {len(full_to_name)}/{len(full_urls)}", flush=True)
        print(flush=True)
    else:
        # 没有图片时不必保留空 assets 目录
        try:
            assets_dir.rmdir()
        except OSError:
            pass

    # 第三阶段：替换图片路径并写入文件
    for out_path, page, base_url in articles_data:
        if full_to_name:
            mapping, _ = _collect_image_refs(page, base_url)
            downloaded = {
                orig: full_to_name[full]
                for orig, full in mapping.items()
                if full in full_to_name
            }
            page = _rewrite_images(page, downloaded)
        out_path.write_text(page, encoding="utf-8")

    # 汇总
    print()
    print("=" * 60)
    print("渲染汇总")
    print("=" * 60)
    print(f"成功: {len(results['success'])} 个")
    print(f"失败: {len(results['failed'])} 个")
    print(f"未知来源: {len(results['unknown'])} 个")
    if full_to_name:
        print(f"图片下载: {len(full_to_name)} 张")

    if results["unknown"]:
        print()
        print("⚠️  以下文件未能识别来源，需要添加新的解析策略：")
        for name, _ in results["unknown"]:
            print(f"  - {name}")
        print()

    if results["failed"]:
        print()
        print("⚠️  以下文件识别了来源但渲染失败：")
        for name, source in results["failed"]:
            print(f"  - [{source}] {name}")

    print()
    print(f"输出目录: {output_dir.absolute()}")
    print("=" * 60)

    return results


def main():
    if len(sys.argv) > 1:
        # 单文件模式
        html_path = sys.argv[1]
        output_path, source, success, _page, _base_url = convert_file(html_path)
        if success:
            print(f"✓ 渲染完成 [{source}]")
            print(f"  原始 HTML: {html_path}")
            print(f"  输出 HTML: {output_path}")
        else:
            print("✗ 渲染失败")
            if source and source != "未知":
                print(f"  识别来源: {source}")
            else:
                print("  未知来源，需要添加新的解析策略")
            sys.exit(1)
    else:
        # 批量模式：自动扫描当天原始 HTML
        scan_and_convert()


if __name__ == "__main__":
    from utils import fix_encoding
    fix_encoding()
    main()
