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
import sys
import time
import urllib.request

from utils import TEMP_DIR
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, quote

# 脚本所在目录
SCRIPT_DIR = Path(__file__).parent

# 伪装成浏览器反而被 WAF 拦截的网站（如 mobius.blog 的 a8c CDN 会校验 UA 与
# TLS 指纹一致，非浏览器请求报 403），改用普通客户端 UA 即可放行完整正文
_PLAIN_UA_SITES = ["mobius.blog"]
_PLAIN_UA = "python-requests/2.31.0"

# 走系统代理会握手失败的网站（meta.appinn.net 经部分代理节点会
# SSL: UNEXPECTED_EOF_WHILE_READING），这些域名改直连
_NO_PROXY_SITES = ["meta.appinn.net"]


def sanitize_filename(name):
    """清理文件名中的非法字符（Windows 文件名兼容）"""
    # 去除 Windows 非法字符
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    # 去除空白字符和控制字符
    name = re.sub(r'[\x00-\x1f\x7f]', '', name)
    # 统一中文标点，避免全角符号影响跨平台文件名
    name = name.replace('：', ' ')   # 全角冒号
    name = name.replace('？', '')    # 全角问号
    name = name.replace('！', '')    # 全角感叹号
    name = name.replace('，', ' ')   # 全角逗号
    name = name.replace('、', ' ')   # 顿号
    name = name.replace('；', ' ')   # 全角分号
    name = name.replace('丨', ' ')   # 竖线
    name = name.replace('。', '')    # 句号
    # 去除方括号
    name = name.replace('[', '').replace(']', '')
    name = name.replace('【', '').replace('】', '')
    # 去除中文引号和内容级方角括号（来源包裹由 f-string 添加）
    name = name.replace('“', '「').replace('”', '」')
    # 去除单引号
    name = name.replace("'", "")
    # 去除井号
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
# 直连 opener（不使用系统代理），供 _NO_PROXY_SITES 使用
_direct_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_cookie_jar),
    urllib.request.ProxyHandler({}))


def fetch_url(url, timeout=30, retries=1):
    """下载网页内容，支持重试"""

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
            opener = _direct_opener if any(
                site in domain.lower() for site in _NO_PROXY_SITES) else _opener
            with opener.open(req, timeout=timeout) as response:
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

    # 处理所有文章（串行写入文件）
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

        # 统一下载原始 HTML，由步骤 3 渲染为干净 HTML
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
    """规范化输出目录中的文件名（统一「来源」标题格式）"""
    renamed = 0
    for f in output_dir.iterdir():
        if f.is_dir():
            continue
        if f.name == f"{output_dir.name}.html":
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
    print("规范化文件名...")
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
