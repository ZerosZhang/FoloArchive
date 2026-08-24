#!/usr/bin/env python3
"""
将文章 HTML 批量转换为 Markdown（支持多来源自动识别）

用法:
    # 自动扫描当天文件夹中的所有 HTML 并转换
    python Script/html_to_md.py

    # 转换指定 HTML 文件
    python Script/html_to_md.py <html文件路径>

输出:
    与 HTML 同目录下的同名 .md 文件
"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

# src/ 目录（strategies 包所在），core/ 由同目录导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import resolve_strategy
from utils import TEMP_DIR, OUTPUT_BASE_DIR


# =============================================================================
# 1. 通用 HTML → Markdown 转换器
# =============================================================================

class TextExtractor(HTMLParser):
    """从 HTML 片段中提取 Markdown，保留链接、图片、表格等"""

    SKIP_CLASSES = [
        "comment__footer",
        "emoji__reaction",
        "btns__wrapper",
        "post__body__extend__item__footer",
        "ss-community-card",
    ]

    BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                  "blockquote", "pre", "table", "tr", "td", "th", "figure"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self._link_url = None
        self._link_text = ""
        self._skip_depth = 0
        self._in_table = False
        self._in_tr = False
        self._td_parts = []
        self._row_cells = []
        self._table_rows = []
        self._is_header_row = False

    def _append_text(self, text):
        if self._link_url is not None:
            self._link_text += text
        elif self._in_tr:
            self._td_parts.append(text)
        else:
            self.parts.append(text)

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")

        if self._skip_depth > 0:
            self._skip_depth += 1
            return
        if any(skip in cls for skip in self.SKIP_CLASSES):
            self._skip_depth = 1
            return

        if tag == "a":
            # 跳过 headerlink 等空锚点，避免标题被污染为 [](#id)标题
            if "headerlink" in cls or "anchor" in cls:
                return
            self._link_url = attrs_dict.get("href", "")
            self._link_text = ""
        elif tag == "img":
            # 懒加载图片：src 为空时回退 data-src / data-lazy-src；仍无图源则忽略
            src = (attrs_dict.get("src", "") or attrs_dict.get("data-src", "")
                   or attrs_dict.get("data-lazy-src", ""))
            if not src:
                return
            alt = attrs_dict.get("alt", "")
            self._append_text(f"\n![{alt}]({src})\n")
        elif tag == "br":
            self._append_text("\n")
        elif tag in ("strong", "b"):
            self._append_text("**")
        elif tag in ("em", "i"):
            self._append_text("*")
        elif tag == "code":
            self._append_text("`")
        elif tag == "pre":
            self._append_text("\n```\n")
        elif tag == "blockquote":
            self._append_text("> ")
        elif tag == "hr":
            self._append_text("\n---\n")
        elif tag == "li":
            self._append_text("\n- ")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._append_text(f"\n{'#' * int(tag[1])} ")
        elif tag == "table":
            self._in_table = True
            self._table_rows = []
        elif tag == "tr":
            self._in_tr = True
            self._row_cells = []
            self._is_header_row = False
        elif tag == "th":
            self._is_header_row = True
            self._td_parts = []
        elif tag == "td":
            self._td_parts = []

    def handle_endtag(self, tag):
        if self._skip_depth > 0:
            self._skip_depth -= 1
            return

        if tag == "a" and self._link_url is not None:
            text = self._link_text.strip() or self._link_url
            link_md = f"[{text}]({self._link_url})"
            self._link_url = None
            self._link_text = ""
            if self._in_tr:
                self._td_parts.append(link_md)
            else:
                self.parts.append(link_md)
        elif tag in ("strong", "b"):
            self._append_text("**")
        elif tag in ("em", "i"):
            self._append_text("*")
        elif tag == "code":
            self._append_text("`")
        elif tag == "pre":
            self._append_text("\n```\n")
        elif tag == "td" or tag == "th":
            cell_text = "".join(self._td_parts).strip()
            self._row_cells.append(cell_text)
            self._td_parts = []
        elif tag == "tr":
            if self._row_cells:
                self._table_rows.append((self._is_header_row, self._row_cells))
            self._row_cells = []
            self._in_tr = False
        elif tag == "table":
            self._in_table = False
            if self._table_rows:
                # 确保表格前面有空行
                if self.parts and not self.parts[-1].endswith("\n\n"):
                    self.parts.append("\n")
                for i, (is_header, cells) in enumerate(self._table_rows):
                    row = "| " + " | ".join(cells) + " |"
                    self.parts.append(row + "\n")
                    if i == 0:
                        sep = "| " + " | ".join(["---"] * len(cells)) + " |"
                        self.parts.append(sep + "\n")
            self._table_rows = []
            self.parts.append("\n")
        elif tag in ("ul", "ol"):
            # 列表结束后补空行，与后续块级内容分隔
            self.parts.append("\n\n")
        elif tag == "li":
            # 列表项之间紧凑排列（下一个 li 的 "- " 自带换行）
            pass
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        text = data.replace("\r", " ").replace("\n", " ")
        text = re.sub(r" +", " ", text)
        self._append_text(text)

    def handle_entityref(self, name):
        import html
        self._append_text(html.unescape(f"&{name};"))

    def handle_charref(self, name):
        import html
        if name.startswith("x"):
            self._append_text(html.unescape(f"&#x{name[1:]};"))
        else:
            self._append_text(html.unescape(f"&#{name};"))

    def get_text(self):
        text = "".join(self.parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def format_datetime(dt_str):
    """将 ISO 格式时间转换为 2026年07月04日 08:00:40 格式"""
    if not dt_str:
        return ""
    try:
        # 处理 ISO 格式: 2026-07-04T08:00:40.947Z 或 2026-07-04T08:00:40Z
        dt_str = dt_str.replace("Z", "+00:00")
        from datetime import datetime
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%Y年%m月%d日 %H:%M:%S")
    except (ValueError, AttributeError):
        return dt_str


def _decode_html_entities(text):
    """解码常见 HTML 实体"""
    text = text.replace('&#34;', '"').replace('&#39;', "'")
    text = text.replace('&ldquo;', '"').replace('&rdquo;', '"')
    text = text.replace('&lsquo;', ''').replace('&rsquo;', ''')
    text = text.replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&amp;', '&')
    return text


# 占位符前缀，使用 UUID 风格避免与文章内容冲突
_PLACEHOLDER_PREFIX = "a1b2c3d4"


def html_to_md(html_text):
    # 存储代码块占位符
    code_blocks = []

    # 预处理：删除 script / style 块（正文中的广告 JS、内联样式等不应进入 Markdown）
    html_text = re.sub(r'<script[^>]*>.*?</script>', '', html_text, flags=re.DOTALL)
    html_text = re.sub(r'<style[^>]*>.*?</style>', '', html_text, flags=re.DOTALL)

    # 预处理：将 chroma 代码块转换为占位符
    # 结构：<div class=highlight><div class=chroma><table class=lntable>
    #   <tr><td class=lntd><pre>行号</pre></td><td class=lntd><pre><code class=language-xxx>代码</code></pre></td></tr>
    # </table></div></div>
    def replace_chroma_block(m):
        code_content = m.group(1)
        # 清理代码：移除 span 标签但保留内容
        code_content = re.sub(r'<span class=line><span class=cl>', '', code_content)
        code_content = re.sub(r'</span></span>', '\n', code_content)
        code_content = re.sub(r'<span[^>]*>', '', code_content)
        code_content = re.sub(r'</span>', '', code_content)
        code_content = _decode_html_entities(code_content)
        # 清理空行和首尾空白
        code_content = re.sub(r'\n\s*\n', '\n', code_content)
        code_content = code_content.strip()
        # 使用占位符，避免被 TextExtractor 处理
        idx = len(code_blocks)
        placeholder = f'{_PLACEHOLDER_PREFIX}_CODE_{idx}_END'
        code_blocks.append(f'\n```CSharp\n{code_content}\n```\n')
        return placeholder

    # 匹配第二个 <td> 中的代码（跳过行号 td）
    html_text = re.sub(
        r'<div class=highlight>.*?<table[^>]*>.*?<tr>.*?<td[^>]*>.*?</td>\s*<td[^>]*>.*?<code[^>]*>(.*?)</code>.*?</td>\s*</tr>.*?</table>.*?</div>',
        replace_chroma_block,
        html_text,
        flags=re.DOTALL
    )

    # 预处理：将 shiki 代码块转换为占位符（Astro / VitePress 等）
    # 结构：<pre class="shiki ..."><code><span class="line"><span>代码</span></span>...</code></pre>
    def replace_shiki_block(m):
        code_content = m.group(1)
        code_content = re.sub(r'<span class="line"><span>', '', code_content)
        code_content = re.sub(r'</span></span>', '\n', code_content)
        code_content = re.sub(r'<span[^>]*>', '', code_content)
        code_content = re.sub(r'</span>', '', code_content)
        code_content = _decode_html_entities(code_content)
        code_content = re.sub(r'\n\s*\n', '\n', code_content)
        code_content = code_content.strip()
        idx = len(code_blocks)
        placeholder = f'{_PLACEHOLDER_PREFIX}_CODE_{idx}_END'
        code_blocks.append(f'\n```\n{code_content}\n```\n')
        return placeholder

    html_text = re.sub(
        r'<pre[^>]*class="[^"]*shiki[^"]*"[^>]*><code[^>]*>(.*?)</code></pre>',
        replace_shiki_block,
        html_text,
        flags=re.DOTALL
    )

    # 预处理：将 notice 块转换为 Obsidian callout 格式
    def replace_notice_block(m):
        notice_type = m.group(1).lower()  # info, tip, warning 等
        full_match = m.group(0)
        # 提取 notice-content 中的文本
        content_match = re.search(r'<div class="?notice-content"?[^>]*>(.*?)</div>', full_match, re.DOTALL)
        if content_match:
            notice_text = content_match.group(1)
            notice_text = re.sub(r'<[^>]+>', '', notice_text)
            notice_text = _decode_html_entities(notice_text)
            notice_text = notice_text.strip()
            # 使用占位符避免被 TextExtractor 处理
            return f'{_PLACEHOLDER_PREFIX}_CALLOUT_{notice_type}_{notice_text}_END'
        return full_match

    html_text = re.sub(
        r'<div class="?notice\s+(\w+)"?[^>]*>.*?</div>\s*</div>',
        replace_notice_block,
        html_text,
        flags=re.DOTALL
    )

    parser = TextExtractor()
    parser.feed(html_text)
    result = parser.get_text()

    # 将 callout 占位符转换为 Obsidian callout 格式
    def replace_callout_placeholder(m):
        notice_type = m.group(1)
        content = m.group(2)
        return f'> [!{notice_type}]\n> {content}'

    result = re.sub(
        f'{_PLACEHOLDER_PREFIX}_CALLOUT_(\\w+?)_(.+?)_END',
        replace_callout_placeholder,
        result,
        flags=re.DOTALL
    )

    # 将代码块占位符替换回代码块
    for i, code_block in enumerate(code_blocks):
        result = result.replace(f'{_PLACEHOLDER_PREFIX}_CODE_{i}_END', code_block)

    return result


# =============================================================================
# 4. 元信息提取
# =============================================================================

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

    # 对于 Product Hunt 来源，优先使用标题匹配（因为 HTML 中的 og:url 可能是产品官网而不是 Product Hunt 链接）
    filename = html_path.stem
    is_product_hunt = "Product Hunt" in filename

    if is_product_hunt:
        # 优先从文件名中提取标题进行匹配
        m = re.search(r'「[^」]+」(.+)', filename)
        if m:
            file_title = m.group(1)
            # 标准化标题：去掉标点符号，只保留字母数字和空格
            def normalize_title(t):
                return re.sub(r'[^\w\s]', '', t).strip()

            normalized_file_title = normalize_title(file_title)
            # 取前20个字符进行匹配（避免末尾差异导致不匹配）
            prefix_length = min(20, len(normalized_file_title))
            file_prefix = normalized_file_title[:prefix_length]

            for article in article_list:
                json_title = article.get("title", "")
                normalized_json_title = normalize_title(json_title)
                json_prefix = normalized_json_title[:prefix_length]
                # 前缀匹配
                if file_prefix and json_prefix and file_prefix == json_prefix:
                    return {
                        "feed_title": article.get("feed_title", "未知来源"),
                        "url": article.get("url", ""),
                        "published": article.get("published", ""),
                        "summary": article.get("summary", ""),
                    }

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
# 5. 图片下载
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


def _collect_image_urls(md_text, base_url):
    """从 Markdown 文本中收集所有远程图片 URL"""
    img_pattern = re.compile(r"!\[(.*?)\]\((.+?)\)")
    base = _get_base_url(base_url)
    urls = []
    seen = set()

    for match in img_pattern.finditer(md_text):
        url = match.group(2).strip()
        if url.startswith(("http://", "https://", "//", "/")) and url not in seen:
            seen.add(url)
            full_url = url
            if full_url.startswith("//"):
                full_url = "https:" + full_url
            elif full_url.startswith("/"):
                full_url = base + full_url
            elif not full_url.startswith(("http://", "https://")):
                full_url = urljoin(base_url, full_url)
            urls.append((url, full_url))

    return urls


def _replace_image_paths(md_text, downloaded):
    """替换 Markdown 中的图片路径为本地路径"""
    img_pattern = re.compile(r"!\[(.*?)\]\((.+?)\)")

    def replace_img(match):
        alt = match.group(1)
        url = match.group(2).strip()
        if not url.startswith(("http://", "https://", "//", "/")):
            return match.group(0)
        if url in downloaded:
            return f"![{alt}](./assets/{downloaded[url]})"
        return match.group(0)

    return img_pattern.sub(replace_img, md_text)


def download_images(md_text, base_url, assets_dir, date_prefix=None):
    """
    下载 Markdown 中的远程图片到本地 assets 目录，替换为相对路径。
    图片统一命名为「日期+编号」格式，如：2026年06月11日_01.png
    返回替换后的 Markdown 文本。
    """
    assets_dir = Path(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)

    if not date_prefix:
        date_prefix = datetime.now().strftime("%Y年%m月%d日")

    urls_to_download = _collect_image_urls(md_text, base_url)
    if not urls_to_download:
        return md_text

    # 并发下载图片
    downloaded = {}
    counter = 0

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(_download_image, args) for args in urls_to_download]
        for future in as_completed(futures):
            orig_url, data, ext, error = future.result()
            if data is not None:
                counter += 1
                filename = f"{date_prefix}_{counter:02d}{ext}"
                local_path = assets_dir / filename
                while local_path.exists():
                    counter += 1
                    filename = f"{date_prefix}_{counter:02d}{ext}"
                    local_path = assets_dir / filename
                local_path.write_bytes(data)
                downloaded[orig_url] = local_path.name

    return _replace_image_paths(md_text, downloaded)


# =============================================================================
# 6. 单文件转换
# =============================================================================

def convert_file(html_path, download_img=True):
    """转换单个 HTML 文件，返回 (md_path, source_name, success, md_text, base_url)
    如果 download_img=False，不下载图片，返回原始 md_text 供批量下载"""
    html_path = Path(html_path)
    if not html_path.exists():
        return None, "", False, "", ""

    html_text = html_path.read_text(encoding="utf-8")
    title = extract_title(html_text)
    html_url = extract_html_url(html_text)

    # 文件名来源提示
    filename_hint = guess_source_from_filename(html_path.name)

    # 从 JSON 获取元信息
    meta = load_article_meta_from_json(html_path, title, html_url)
    feed_title = meta.get("feed_title", "") if meta else ""

    # 确定策略（文件名提示优先）
    strategy = resolve_strategy(html_text, filename_hint)
    if not strategy and feed_title:
        # JSON 中有来源但策略没匹配到，尝试用 feed_title 再匹配一次
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

    # 提取正文
    # 对于 Product Hunt 策略，传递 JSON 中的 URL 和 summary
    json_url = meta.get("url", "") if meta else ""
    json_summary = meta.get("summary", "") if meta else ""
    if strategy.name == "Product Hunt 热门":
        body_html = strategy.extract_body(html_text, json_url, json_summary)
    else:
        body_html = strategy.extract_body(html_text)
    has_content = False

    if body_html.strip():
        blocks = strategy.extract_blocks(body_html)
        if blocks:
            has_content = True
        else:
            blocks = []
    else:
        blocks = []

    # 组装 Markdown（Obsidian callout 格式）
    lines = []

    url = meta.get("url", "")
    published = meta.get("published", "")
    display_feed = meta.get("feed_title", "") or strategy.name

    if display_feed:
        lines.append(f"> [!summary]")
        lines.append(f"> 来源：{display_feed}")
        if published:
            lines.append(f"> 发布时间：{format_datetime(published)}")
        if url:
            lines.append(f"> 原文链接：[{url}]({url})")
        lines.append("")

    if has_content:
        for h2_title, block_html in blocks:
            if h2_title and any(skip in h2_title for skip in strategy.skip_titles):
                continue

            if h2_title:
                lines.append(f"## {h2_title}")
                lines.append("")

            body_md = html_to_md(block_html)
            if body_md:
                lines.append(body_md)
                lines.append("")
    else:
        lines.append("⚠️ 无法提取文章内容。")
        lines.append("")

    md_text = "\n".join(lines).strip() + "\n"

    # 如果需要下载图片
    if download_img:
        date_prefix = html_path.parent.name
        md_text = download_images(md_text, url, html_path.parent / "assets", date_prefix=date_prefix)
        md_path = html_path.with_suffix(".md")
        md_path.write_text(md_text, encoding="utf-8")
        return md_path, strategy.name, True, md_text, url

    # 不下载图片，返回原始 md_text
    return html_path.with_suffix(".md"), strategy.name, True, md_text, url


# =============================================================================
# 6. 批量转换主入口
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
    扫描指定日期文件夹中的所有 HTML 并批量转换。
    day_folder: 如 "2026年05月18日"，默认当天。
    返回 results 字典：{"success": [...], "failed": [...], "unknown": [...]}
    """
    if day_folder is None:
        day_folder = datetime.now().strftime("%Y年%m月%d日")

    # 文章输出目录在项目根（Python/ 的上级）
    output_dir = OUTPUT_BASE_DIR / day_folder
    if not output_dir.exists():
        print(f"错误: 文件夹不存在: {output_dir}")
        return {"success": [], "failed": [], "unknown": []}

    html_files = sorted(output_dir.glob("*.html"))
    if not html_files:
        print(f"未在 {output_dir} 中找到 HTML 文件")
        return {"success": [], "failed": [], "unknown": []}

    print(f"=" * 60)
    print(f"HTML → Markdown 批量转换")
    print(f"目标文件夹: {output_dir}")
    print(f"共 {len(html_files)} 个 HTML 文件")
    print(f"=" * 60)
    print()

    results = {
        "success": [],
        "failed": [],
        "unknown": [],
    }

    # 第一阶段：转换所有文章，收集图片 URL
    articles_data = []  # (html_path, md_path, source, md_text, base_url)
    all_image_urls = []  # (orig_url, full_url, base_url)
    seen_urls = set()

    for i, html_path in enumerate(html_files, 1):
        # 文件名按显示宽度截断（中文占 2 字符），确保状态列对齐
        stem = html_path.stem
        name = _truncate_display(stem, 40)
        if len(name) != len(stem):
            name = _truncate_display(stem, 39) + "…"
        pad = " " * (40 - _display_width(name))
        print(f"[{i:>{len(str(len(html_files)))}}/{len(html_files)}] {name}{pad}  ", end="", flush=True)
        md_path, source, success, md_text, base_url = convert_file(html_path, download_img=False)

        if success:
            print(f"✓ [{source}]", flush=True)
            results["success"].append((html_path.name, source))
            articles_data.append((html_path, md_path, source, md_text, base_url))

            # 收集图片 URL
            for url, full_url in _collect_image_urls(md_text, base_url):
                if url not in seen_urls:
                    seen_urls.add(url)
                    all_image_urls.append((url, full_url))
        elif source and source != "未知":
            print(f"✗ [{source}] 转换失败", flush=True)
            results["failed"].append((html_path.name, source))
        else:
            print(f"✗ 未知来源", flush=True)
            results["unknown"].append((html_path.name, "未知来源"))

    # 第二阶段：批量并发下载所有图片
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    date_prefix = day_folder
    downloaded = {}

    if all_image_urls:
        print(flush=True)
        print(f"正在批量下载 {len(all_image_urls)} 张图片...", flush=True)
        counter = 0

        with ThreadPoolExecutor(max_workers=30) as executor:
            futures = [executor.submit(_download_image, args) for args in all_image_urls]
            for future in as_completed(futures):
                orig_url, data, ext, error = future.result()
                if data is not None:
                    counter += 1
                    filename = f"{date_prefix}_{counter:02d}{ext}"
                    local_path = assets_dir / filename
                    while local_path.exists():
                        counter += 1
                        filename = f"{date_prefix}_{counter:02d}{ext}"
                        local_path = assets_dir / filename
                    local_path.write_bytes(data)
                    downloaded[orig_url] = local_path.name

        print(f"✓ 图片下载完成: {len(downloaded)}/{len(all_image_urls)}", flush=True)
        print(flush=True)

    # 第三阶段：替换图片路径并写入文件
    for html_path, md_path, source, md_text, base_url in articles_data:
        if downloaded:
            md_text = _replace_image_paths(md_text, downloaded)
        md_path.write_text(md_text, encoding="utf-8")

    # 汇总
    print()
    print("=" * 60)
    print("转换汇总")
    print("=" * 60)
    print(f"成功: {len(results['success'])} 个")
    print(f"失败: {len(results['failed'])} 个")
    print(f"未知来源: {len(results['unknown'])} 个")
    if downloaded:
        print(f"图片下载: {len(downloaded)} 张")

    if results["unknown"]:
        print()
        print("⚠️  以下文件未能识别来源，需要添加新的解析策略：")
        for name, _ in results["unknown"]:
            print(f"  - {name}")
        print()
        print("添加新来源的方法：")
        print("  1. 在 Script/strategies/ 中新建一个策略文件，继承 BaseStrategy")
        print("  2. 实现 detect()、extract_body()、extract_blocks() 方法")
        print("  3. 用 @register_strategy 装饰器注册，并在 strategies/__init__.py 中导入")
        print()

    if results["failed"]:
        print()
        print("⚠️  以下文件识别了来源但转换失败：")
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
        md_path, source, success, md_text, base_url = convert_file(html_path)
        if success:
            print(f"✓ 转换完成 [{source}]")
            print(f"  HTML: {html_path}")
            print(f"  Markdown: {md_path}")
        else:
            print(f"✗ 转换失败")
            if source and source != "未知":
                print(f"  识别来源: {source}")
            else:
                print(f"  未知来源，需要添加新的解析策略")
            sys.exit(1)
    else:
        # 批量模式：自动扫描当天文件夹
        scan_and_convert()


if __name__ == "__main__":
    from utils import fix_encoding
    fix_encoding()
    main()
