#!/usr/bin/env python3
"""
将文章列表中的网页保存到本地 HTML 文件
用法:
    python save_webpages.py [列表文件路径] [输出目录]

默认:
    列表文件: TempData/当天日期.json
    输出目录: 当天日期/
"""

import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from utils import TEMP_DIR
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, quote

# 脚本所在目录
SCRIPT_DIR = Path(__file__).parent

# 需要使用浏览器自动化的网站（有 WAF 或反爬机制）
_BROWSER_SITES = ["huxiu.com"]

# 伪装成浏览器反而被 WAF 拦截的网站（如 mobius.blog 的 a8c CDN 会校验 UA 与
# TLS 指纹一致，非浏览器请求报 403），改用普通客户端 UA 即可放行完整正文
_PLAIN_UA_SITES = ["mobius.blog"]
_PLAIN_UA = "python-requests/2.31.0"


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

# Node.js 路径
NODE_PATH = "/c/Program Files/nodejs"


def find_bash():
    """在 Windows 上自动查找 Git Bash 路径"""
    from shutil import which
    found = which("bash")
    if found:
        return found
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError("找不到 bash")


def run_bash(cmd):
    """运行 bash 命令"""
    bash_path = find_bash()
    env_cmd = f'export PATH="{NODE_PATH}:$PATH" && {cmd}'
    result = subprocess.run(
        [bash_path, "-c", env_cmd],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace"
    )
    return result


def extract_json(text):
    """从输出中提取 JSON"""
    if not text:
        return None
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text.strip()


def run_folo(args):
    """运行 Folo CLI 命令"""
    result = run_bash(f"npx --yes folocli@latest {' '.join(args)}")
    stdout_text = result.stdout or ""
    json_text = extract_json(stdout_text)
    try:
        return json.loads(json_text)
    except (json.JSONDecodeError, TypeError):
        return None


def fetch_article_content_from_folo(entry_id):
    """通过 Folo CLI 获取文章内容（HTML）"""
    result = run_folo(["entry", "read", entry_id])
    if result and result.get("ok"):
        content = result.get("data", {}).get("content", "")
        return content
    return None


def is_valid_content(text, min_length=100):
    """
    检查内容是否有效

    返回:
        "valid": 内容有效
        "deleted": 内容已删除/404（不应重试）
        "invalid": 内容无效（可重试）
    """
    if not text or not text.strip():
        return "invalid"

    text_lower = text.lower()[:500]

    # 先检查是否是 404/内容已删除（不应重试，不受长度限制）
    deleted_markers = [
        "该内容找不到或已被删除", "内容不存在", "文章已删除",
        "404", "页面不存在", "not found"
    ]
    for marker in deleted_markers:
        if marker.lower() in text_lower:
            return "deleted"

    # 检查是否是其他错误（可重试）
    error_markers = [
        "403", "500", "访问被拒绝", "请稍后再试", "网络错误", "加载失败"
    ]
    for marker in error_markers:
        if marker.lower() in text_lower:
            return "invalid"

    # 检查最小长度
    if len(text.strip()) < min_length:
        return "invalid"

    return "valid"


def html_to_text(html_content):
    """简单的 HTML 转文本（保留基本结构）"""
    if not html_content:
        return ""

    # 移除 script 和 style 标签
    html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL)
    html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL)

    # 处理标题（strip 处理标签内的换行和空白）
    html_content = re.sub(r'<h1[^>]*>(.*?)</h1>', lambda m: f'# {m.group(1).strip()}\n', html_content, flags=re.DOTALL)
    html_content = re.sub(r'<h2[^>]*>(.*?)</h2>', lambda m: f'## {m.group(1).strip()}\n', html_content, flags=re.DOTALL)
    html_content = re.sub(r'<h3[^>]*>(.*?)</h3>', lambda m: f'### {m.group(1).strip()}\n', html_content, flags=re.DOTALL)

    # 处理段落
    html_content = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n', html_content, flags=re.DOTALL)

    # 处理图片
    html_content = re.sub(r'<img[^>]*src="([^"]*)"[^>]*/?>', r'![图片](\1)', html_content)

    # 处理链接
    html_content = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', html_content, flags=re.DOTALL)

    # 处理粗体和斜体
    html_content = re.sub(r'<(?:strong|b)>(.*?)</(?:strong|b)>', r'**\1**', html_content, flags=re.DOTALL)
    html_content = re.sub(r'<(?:em|i)>(.*?)</(?:em|i)>', r'*\1*', html_content, flags=re.DOTALL)

    # 处理列表
    html_content = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', html_content, flags=re.DOTALL)

    # 移除其他 HTML 标签
    html_content = re.sub(r'<[^>]+>', '', html_content)

    # 清理空白
    html_content = re.sub(r'\n\s*\n', '\n\n', html_content)
    html_content = html_content.strip()

    return html_content


def sanitize_filename(name):
    """清理文件名中的非法字符（Windows + Obsidian wikilink 兼容）"""
    # 去除 Windows 非法字符
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    # 去除空白字符和控制字符
    name = re.sub(r'[\x00-\x1f\x7f]', '', name)
    # 去除中文标点（Obsidian wikilink 兼容）
    name = name.replace('：', ' ')   # 全角冒号
    name = name.replace('？', '')    # 全角问号
    name = name.replace('！', '')    # 全角感叹号
    name = name.replace('，', ' ')   # 全角逗号
    name = name.replace('、', ' ')   # 顿号
    name = name.replace('；', ' ')   # 全角分号
    name = name.replace('丨', ' ')   # 竖线
    name = name.replace('。', '')    # 句号
    # 去除方括号（Obsidian wikilink 兼容）
    name = name.replace('[', '').replace(']', '')
    name = name.replace('【', '').replace('】', '')
    # 去除中文引号和内容级方角括号（来源包裹由 f-string 添加）
    name = name.replace('“', '「').replace('”', '」')
    # 去除单引号（Obsidian wikilink 兼容）
    name = name.replace("'", "")
    # 去除井号（Obsidian wikilink 块引用分隔符）
    name = name.replace('#', '')
    # en dash / em dash → regular dash
    name = name.replace('–', '-').replace('—', '-')
    # 修复双连字符
    name = name.replace('--', '-')
    # 合并连续空格
    name = re.sub(r' +', ' ', name)
    name = name.strip()
    # 限制长度
    if len(name) > 80:
        name = name[:80]
    return name


# 全局 cookie jar，保持跨请求的 cookie（反爬关键）
_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_cookie_jar))


def _needs_browser(url):
    """检查 URL 是否需要使用浏览器自动化"""
    domain = urlparse(url).netloc.lower()
    return any(site in domain for site in _BROWSER_SITES)


def fetch_url_with_browser(url, timeout=30):
    """使用 Playwright headless 浏览器下载网页（用于有 WAF 的网站）"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "<!-- Playwright 未安装，请运行: pip install playwright -->\n"

    try:
        with sync_playwright() as p:
            # 查找系统 Edge 浏览器
            edge_path = None
            if sys.platform == "win32":
                import winreg
                try:
                    key = winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
                    )
                    edge_path = winreg.QueryValue(key, "")
                    winreg.CloseKey(key)
                except (WindowsError, FileNotFoundError):
                    pass

            if not edge_path:
                # 备用路径
                import os
                for check_path in [
                    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                ]:
                    if os.path.exists(check_path):
                        edge_path = check_path
                        break

            if not edge_path:
                return "<!-- 未找到 Edge 浏览器 -->\n"

            # 使用 Edge 启动（Edge 基于 Chromium，Playwright 兼容）
            browser = p.chromium.launch(
                headless=True,
                executable_path=edge_path,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
            )
            page = context.new_page()

            # 访问页面并等待加载
            page.goto(url, wait_until="networkidle", timeout=timeout * 1000)

            # 等待一下确保动态内容加载
            page.wait_for_timeout(2000)

            # 获取页面 HTML
            html = page.content()

            browser.close()
            return html

    except Exception as e:
        return f"<!-- 浏览器下载失败: {e} -->\n"


def fetch_url(url, timeout=30, retries=1):
    """下载网页内容，支持重试。对有 WAF 的网站自动使用浏览器"""
    # 检查是否需要浏览器
    if _needs_browser(url):
        return fetch_url_with_browser(url, timeout)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        # 不声明 Accept-Encoding，避免服务器返回压缩内容
        # urllib.request 不会自动解压，会导致乱码
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }

    # 针对特定站点添加 referer
    domain = urlparse(url).netloc
    referrers = {
        'sspai.com': 'https://sspai.com/',
        'appinn.com': 'https://www.appinn.com/',
        'ftium4.com': 'https://www.ftium4.com/',
        'iplaysoft.com': 'https://www.iplaysoft.com/',
        'producthunt.com': 'https://www.producthunt.com/',
    }
    for key, ref in referrers.items():
        if key in domain:
            headers['Referer'] = ref
            headers['Sec-Fetch-Site'] = 'same-origin'
            break

    # mobius.blog 等站点用普通客户端 UA，避免被 WAF 误判为伪装浏览器而 403
    if any(site in domain.lower() for site in _PLAIN_UA_SITES):
        headers['User-Agent'] = _PLAIN_UA

    last_error = None
    for attempt in range(retries + 1):
        try:
            # 对 URL 中的非 ASCII 字符进行编码
            # safe 字符遵循 RFC 3986 的 unreserved + 子定界符 + :/?#[]@
            safe_chars = ':/?#[]@!$&()*+,;='
            encoded_url = quote(url, safe=safe_chars)
            req = urllib.request.Request(encoded_url, headers=headers)
            with _opener.open(req, timeout=timeout) as response:
                # 尝试读取并处理编码
                html_bytes = response.read()

            # 尝试从 Content-Type 或 meta 标签推断编码
            charset = None
            content_type = response.headers.get('Content-Type', '')
            if 'charset=' in content_type:
                charset = content_type.split('charset=')[-1].split(';')[0].strip().lower()

            # 常见编码尝试顺序
            encodings = [charset, 'utf-8', 'gbk', 'gb2312', 'gb18030', 'big5', 'latin-1']
            for enc in encodings:
                if not enc:
                    continue
                try:
                    return html_bytes.decode(enc, errors='replace')
                except (UnicodeDecodeError, LookupError):
                    continue

            # 若全部失败，使用 utf-8 兜底
            return html_bytes.decode('utf-8', errors='replace')

        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            last_error = f"URL Error: {e.reason}"
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"

        if attempt < retries:
            time.sleep(1)

    return f"<!-- 下载失败: {last_error} -->\n"


def save_webpage(url, output_path, index, total):
    """保存单个网页"""
    print(f"[{index}/{total}] {url}", flush=True)
    print(f"      下载中...", end=" ", flush=True)

    content = fetch_url(url)

    if content.startswith("<!-- 下载失败"):
        print(f"失败", flush=True)
        print(f"      {content.strip()}", flush=True)
        return False

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        file_size = os.path.getsize(output_path)
        print(f"成功 ({file_size:,} bytes) -> {output_path}", flush=True)
        return True
    except Exception as e:
        print(f"保存失败: {e}", flush=True)
        return False


def _fetch_huxiu_content(entry_id, max_retries=3):
    """并发获取虎嗅文章内容的辅助函数"""
    for attempt in range(max_retries):
        html_content = fetch_article_content_from_folo(entry_id)
        if html_content:
            body_content = html_to_text(html_content)
            validity = is_valid_content(body_content)
            if validity == "valid":
                return {"status": "ok", "content": body_content}
            elif validity == "deleted":
                return {"status": "deleted", "content": ""}
            else:
                if attempt < max_retries - 1:
                    continue
                return {"status": "invalid", "content": ""}
        else:
            if attempt < max_retries - 1:
                continue
            return {"status": "empty", "content": ""}
    return {"status": "empty", "content": ""}


def download_articles(articles, output_dir, on_progress=None, overwrite=False):
    """
    下载文章网页到本地。返回 (success_count, fail_count, failed_urls)

    on_progress: 可选回调函数，签名 on_progress(index, total, article, status, info)
        - index: 当前序号 (1-based)
        - total: 总数
        - article: 当前文章 dict
        - status: 'skip' | 'success' | 'fail'
        - info: 额外信息（文件大小、错误原因等）
    overwrite: 是否覆盖已存在的文件（用于重试场景）
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    success_count = 0
    fail_count = 0
    skip_count = 0
    failed_urls = []

    # 阶段1: 并发获取所有虎嗅文章内容
    huxiu_articles = []  # (index, article) 需要获取内容的虎嗅文章
    for i, article in enumerate(articles, 1):
        feed_title = article.get("feed_title", "")
        url = article.get("url", "")
        is_huxiu = "虎嗅" in feed_title or "huxiu.com" in url
        entry_id = article.get("entry_id", "")
        if is_huxiu and entry_id:
            huxiu_articles.append((i, article))

    # 并发获取
    huxiu_content_map = {}  # entry_id -> {"status": ..., "content": ...}
    if huxiu_articles:
        print(f"  并发获取 {len(huxiu_articles)} 篇虎嗅文章内容...", flush=True)
        with ThreadPoolExecutor(max_workers=20) as executor:
            future_map = {}
            for idx, article in huxiu_articles:
                entry_id = article.get("entry_id", "")
                future = executor.submit(_fetch_huxiu_content, entry_id)
                future_map[future] = (idx, article, entry_id)

            done_count = 0
            for future in as_completed(future_map):
                idx, article, entry_id = future_map[future]
                result = future.result()
                huxiu_content_map[entry_id] = result
                done_count += 1
                status = result["status"]
                chars = len(result["content"]) if result["content"] else 0
                title = article.get("title", "")[:30]
                if status == "ok":
                    print(f"    [{done_count}/{len(huxiu_articles)}] ✓ {title}... ({chars} 字符)", flush=True)
                elif status == "deleted":
                    print(f"    [{done_count}/{len(huxiu_articles)}] ✗ {title}... (内容已删除)", flush=True)
                else:
                    print(f"    [{done_count}/{len(huxiu_articles)}] ✗ {title}... ({status})", flush=True)
        print()

    # 阶段2: 处理所有文章（串行写入文件）
    for i, article in enumerate(articles, 1):
        url = article.get("url", "")
        title = article.get("title", "无标题")
        feed_title = article.get("feed_title", "未知来源")

        if not url:
            failed_urls.append((i, title, url, "无 URL"))
            fail_count += 1
            if on_progress:
                on_progress(i, len(articles), article, "skip", "无 URL")
            continue

        # 生成文件名: 「来源」标题
        safe_title = sanitize_filename(title)
        safe_feed = sanitize_filename(feed_title)

        # 检查是否是 Product Hunt 或虎嗅文章（直接生成 Markdown，不下载 HTML）
        is_product_hunt = "Product Hunt" in feed_title
        is_huxiu = "虎嗅" in feed_title or "huxiu.com" in url

        if is_product_hunt or is_huxiu:
            # Product Hunt 和虎嗅文章：通过 Folo CLI 获取内容或使用 summary
            md_filename = f"「{safe_feed}」{safe_title}.md"
            md_path = output_dir / md_filename

            # 文件已存在则跳过（除非覆盖模式）
            if not overwrite and md_path.exists():
                skip_count += 1
                if on_progress:
                    on_progress(i, len(articles), article, "skip", "文件已存在")
                continue

            # 获取正文内容
            body_content = ""
            entry_id = article.get("entry_id", "")
            content_deleted = False

            # 虎嗅文章：从并发获取的结果中取内容
            if is_huxiu and entry_id and entry_id in huxiu_content_map:
                result = huxiu_content_map[entry_id]
                if result["status"] == "ok":
                    body_content = result["content"]
                elif result["status"] == "deleted":
                    content_deleted = True

            # 如果没有正文内容，使用 summary（仅作为备用）
            if not body_content and not is_huxiu:
                body_content = article.get("summary", "")

            # 生成 Markdown 内容（Obsidian callout 格式）
            published = article.get("published", "")
            md_lines = []
            md_lines.append(f"> [!summary]")
            md_lines.append(f"> 来源：{feed_title}")
            if published:
                md_lines.append(f"> 发布时间：{format_datetime(published)}")
            md_lines.append(f"> 原文链接：[{url}]({url})")
            md_lines.append("")

            if content_deleted:
                md_lines.append("> [!warning] 内容已删除")
                md_lines.append("> 原文已被删除或不可用")
                md_lines.append("")
            elif body_content:
                md_lines.append(body_content)
                md_lines.append("")

            try:
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write("\n".join(md_lines))
                success_count += 1
                source_type = "Product Hunt" if is_product_hunt else "虎嗅"
                status_info = "内容已删除" if content_deleted else f"{source_type} (直接生成 Markdown)"
                if on_progress:
                    on_progress(i, len(articles), article, "success", status_info)
            except Exception as e:
                failed_urls.append((i, title, url, f"保存失败: {e}"))
                fail_count += 1
                if on_progress:
                    on_progress(i, len(articles), article, "fail", str(e))
        else:
            # 其他文章下载 HTML
            html_filename = f"「{safe_feed}」{safe_title}.html"
            html_path = output_dir / html_filename

            # 文件已存在则跳过（除非覆盖模式）
            if not overwrite and html_path.exists():
                skip_count += 1
                if on_progress:
                    on_progress(i, len(articles), article, "skip", "文件已存在")
                continue

            # 下载
            content = fetch_url(url, retries=1)
            if content.startswith("<!-- 下载失败"):
                reason = content.replace("<!-- ", "").replace(" -->\n", "")
                failed_urls.append((i, title, url, reason))
                fail_count += 1
                if on_progress:
                    on_progress(i, len(articles), article, "fail", reason)
                continue

            try:
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                success_count += 1
                file_size = len(content.encode('utf-8'))
                if on_progress:
                    on_progress(i, len(articles), article, "success", f"{file_size:,} bytes")
            except Exception as e:
                failed_urls.append((i, title, url, f"保存失败: {e}"))
                fail_count += 1
                if on_progress:
                    on_progress(i, len(articles), article, "fail", str(e))

    return success_count, fail_count, skip_count, failed_urls


def optimize_titles(output_dir):
    """对输出目录中的文件做 Obsidian wikilink 兼容优化重命名"""
    renamed = 0
    for f in output_dir.iterdir():
        if f.is_dir():
            continue
        if f.name == f"{output_dir.name}.md":
            continue
        stem = f.stem
        m = re.match(r'「([^」]+)」(.*)', stem)
        if m:
            new_source = sanitize_filename(m.group(1))
            new_title = sanitize_filename(m.group(2))
            new_stem = f'「{new_source}」{new_title}'
        else:
            new_stem = sanitize_filename(stem)
        if new_stem != stem:
            new_path = f.with_stem(new_stem)
            try:
                f.rename(new_path)
                renamed += 1
            except OSError as e:
                print(f"  ⚠ 重命名失败: {f.name} -> {e}")
    return renamed


def main():
    today = datetime.now().strftime("%Y年%m月%d日")

    # 默认路径：按 folo_export.py 的实际文件名格式（result/temp_data/）
    default_list = TEMP_DIR / f"「{today}」.json"
    fallback_list = TEMP_DIR / f"{today}.json"
    default_output = Path(today)

    # 解析命令行参数
    list_path = None
    output_dir = None
    overwrite = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--overwrite":
            overwrite = True
            i += 1
        elif list_path is None:
            list_path = Path(args[i])
            i += 1
        elif output_dir is None:
            output_dir = Path(args[i])
            i += 1
        else:
            i += 1

    # 如果没有指定列表文件，自动查找当天的文件
    if list_path is None:
        if default_list.exists():
            list_path = default_list
        elif fallback_list.exists():
            list_path = fallback_list
        else:
            list_path = default_list

    # 如果没有指定输出目录，默认用当天日期文件夹
    if output_dir is None:
        output_dir = default_output

    print(f"列表文件: {list_path}")
    print(f"输出目录: {output_dir}")
    if overwrite:
        print(f"模式: 覆盖已存在的文件")
    print()

    # 读取列表
    if not list_path.exists():
        print(f"错误: 列表文件不存在: {list_path}")
        print("请先运行 folo_export.py 获取文章列表")
        sys.exit(1)

    with open(list_path, 'r', encoding='utf-8') as f:
        articles = json.load(f)

    print(f"共 {len(articles)} 篇文章")
    print()

    success_count, fail_count, skip_count, failed_urls = download_articles(articles, output_dir, overwrite=overwrite)

    print()
    print("优化文件名为 Obsidian 兼容格式...")
    renamed = optimize_titles(output_dir)
    if renamed > 0:
        print(f"✓ 已优化 {renamed} 个文件名")
    else:
        print("✓ 文件名无需优化")

    print(f"{'='*60}")
    print(f"完成!")
    print(f"成功: {success_count} 篇")
    if skip_count > 0:
        print(f"跳过: {skip_count} 篇（文件已存在）")
    if fail_count > 0:
        print(f"失败: {fail_count} 篇")
    print(f"输出目录: {output_dir.absolute()}")

    if failed_urls:
        print()
        print("⚠️  以下 URL 获取失败，请手动检查：")
        print()
        for idx, title, url, reason in failed_urls:
            print(f"  [{idx}] {title}")
            print(f"      原因: {reason}")
            if url:
                print(f"      URL: {url}")
            print()

    print(f"{'='*60}")


if __name__ == "__main__":
    from utils import fix_encoding
    fix_encoding()
    main()
