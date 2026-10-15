#!/usr/bin/env python3
"""
每日 HTML 文档总结脚本
功能：
1. 扫描当天日期文件夹下的所有 .html 文章（排除当天索引页）
2. 并行调用 AI API 生成摘要（默认断点续跑：已有摘要的文章跳过，--force 全部重算）
3. 将摘要注入每篇文章的 <section class="ai-summary" data-folo="summary"> 区块
"""

import html
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock
from openai import OpenAI

from utils import load_config, fix_encoding, OUTPUT_BASE_DIR

# 并发数（可根据 API 限制调整）
MAX_WORKERS = 10

# 重试等待基数（秒）：第 n 次失败后等待 RETRY_BACKOFF * 2^n
RETRY_BACKOFF = 2

# 服务端临时故障可重试的状态码（密钥/参数错误等不在其中，重试无意义）
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

# 是否让模型先「思考」再作答。摘要任务不需要——实测同一条 1.7k 字文章，
# 关掉后 completion 从 1626 降到 216 token、耗时从 9.5s 降到 2.6s，摘要质量不变。
THINKING_BODY = {"thinking": {"type": "disabled"}}

# 文件写入锁
file_lock = Lock()

# 摘要占位区块（与 render_html.py 的模板保持一致）
_SUMMARY_SECTION_RE = re.compile(
    r'(<section[^>]*data-folo="summary"[^>]*>)(.*?)(</section>)',
    re.DOTALL | re.IGNORECASE,
)


def create_client(config):
    """创建 OpenAI 客户端"""
    return OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
        timeout=60.0,
        max_retries=0,
    )


def get_today_folder():
    """获取当天日期文件夹路径"""
    today = datetime.now().strftime("%Y年%m月%d日")
    folder = OUTPUT_BASE_DIR / today
    return today, folder


def scan_html_files(folder, today):
    """扫描目录下的 .html 文章，排除当天索引页"""
    if not folder.exists():
        print(f"[!] 文件夹不存在: {folder}")
        return []

    index_filename = f"{today}.html"
    html_files = []

    for f in sorted(folder.glob("*.html")):
        if f.name == index_filename:
            continue
        if f.parent.name == "assets":
            continue
        html_files.append(f)

    return html_files


def extract_source(filename):
    """从文件名提取来源名称，如「少数派」文章标题.html -> 少数派"""
    match = re.match(r"「(.+?)」", filename)
    return match.group(1) if match else "其他"


def extract_display_title(filename):
    """提取显示标题（去掉来源前缀和 .html 后缀）"""
    name = filename.removesuffix(".html")
    match = re.match(r"「.+?」(.+)", name)
    return match.group(1).strip() if match else name


def _extract_text_for_llm(html_text):
    """把 HTML 转为纯文本供 AI 阅读（剥离样式、脚本与标签）"""
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    m = re.search(r"<body[^>]*>(.*?)</body>", text, flags=re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|h[1-6]|li|tr|blockquote|section|article)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _summary_to_html(summary):
    """把摘要纯文本转成若干 <p> 段落"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", summary.strip()) if p.strip()]
    parts = []
    for p in paragraphs:
        p = html.escape(p).replace("\n", "<br>")
        parts.append(f"<p>{p}</p>")
    return "\n".join(parts)


def _is_retryable(exc):
    """判断异常是否值得重试：网络类错误与 5xx/429 可重试，密钥、参数错误不重试"""
    status = getattr(exc, "status_code", None)
    if status is None:
        return True
    try:
        return int(status) in _RETRYABLE_STATUS
    except (TypeError, ValueError):
        return False


def summarize_article(client, model, content, filename, max_retries=3):
    """调用 API 生成文章摘要；可重试错误按 RETRY_BACKOFF 递增等待后重试"""
    truncated = content[:3000] if len(content) > 3000 else content

    system_prompt = """你是一个专业的文章摘要助手。你的任务是为文章撰写简洁、准确的概括性总结。

你的读者是高中生，他们没有任何行业背景知识。你需要用最通俗易懂的语言，让一个高中生也能完全看懂文章在说什么。

绝对禁止使用任何未解释的专业术语。如果必须使用，必须用括号解释。"""

    user_prompt = f"""请为以下文章撰写概括性总结。

【第一步：判断文章类型】
- 合集文章：标题包含"8点1氪""派早报""早报""周报""合集""汇总"等，或内容包含多个不相关主题的新闻
- 单一主题文章：围绕一个主题展开的深度分析或观点

【第二步：按格式输出】

如果是合集文章，格式如下：
第一行：一句大白话概括（15-30字）
第二行：空行
第三行：重点新闻：（列出2-3条最重要的新闻，每条一句话）

示例：
今天科技圈炸锅，AI升级、公司上市、机器人翻车全都有。

重点新闻：
- 韩国芯片巨头SK海力士要在美国上市，规模超大
- 99万的AI机器人续航只有2小时，公司回应说是行业常态
- 博物馆文物上出现"TCL"字样，原来是保护用的旧报纸

如果是单一主题文章，格式如下：
第一行：一句大白话概括（15-30字）
第二行：空行
第三行：背景：（1-2句），解释这篇文章涉及的领域、事件或概念的背景
第四行：观点：（1-3句），说明作者的核心观点或结论

示例：
企业开始限制员工用AI，因为太烧钱了。

背景：很多公司鼓励员工用AI工具提效，但AI处理文字要收费，费用很高。
观点：当AI从试用变成日常使用后，公司发现成本太高，开始设限制，区分战略性投入和日常消耗。

【语言要求】
- 像跟高中生聊天一样，用最简单的话
- 不要用"赋能""闭环""抓手"等商业黑话
- 不要用"旨在""聚焦""揭示"等书面语
- 如果必须用专业术语，必须用括号解释，如：Token（AI处理文字的收费单位）
- 单一主题文章总字数控制在60-100字
- 合集文章总字数控制在80-120字

文章文件名：{filename}

文章内容：
{truncated}"""

    last_error = "未知错误"

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_completion_tokens=2048,
                temperature=1.0,
                top_p=0.95,
                extra_body=THINKING_BODY,
                stream=False,
            )
            choice = response.choices[0]
            result = choice.message.content
            finish_reason = choice.finish_reason

            if result and result.strip():
                if finish_reason != "length":
                    return result.strip()
                last_error = "输出被截断（finish_reason=length）"
            else:
                last_error = "API 返回空内容"
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            if not _is_retryable(e):
                print(f"  ✗ {filename}: 不可重试的错误（{last_error[:80]}）", flush=True)
                return None, last_error

        if attempt < max_retries - 1:
            delay = RETRY_BACKOFF * (2 ** attempt)
            print(f"  ↻ {filename}: {last_error[:60]}，{delay}s 后重试"
                  f"（第 {attempt + 2}/{max_retries} 次）", flush=True)
            time.sleep(delay)

    return None, f"重试 {max_retries} 次仍失败：{last_error}"


def write_summary_to_html(html_file, summary):
    """把摘要写入文章 HTML 的摘要区块；已有内容则先清空再写，保证幂等"""
    with file_lock:
        content = html_file.read_text(encoding="utf-8")
        inner = _summary_to_html(summary)

        if _SUMMARY_SECTION_RE.search(content):
            content = _SUMMARY_SECTION_RE.sub(
                lambda m: m.group(1) + "\n" + inner + "\n" + m.group(3), content, count=1
            )
        elif "</header>" in content:
            content = content.replace(
                "</header>",
                "</header>\n"
                '<section class="ai-summary" data-folo="summary">\n'
                f"{inner}\n</section>",
                1,
            )
        else:
            content = content.replace(
                '<main class="wrap">',
                '<main class="wrap">\n'
                '<section class="ai-summary" data-folo="summary">\n'
                f"{inner}\n</section>",
                1,
            )

        html_file.write_text(content, encoding="utf-8")


def _existing_summary_text(html_content):
    """取出 HTML 中已有摘要区块的纯文本；无摘要区块或内容为空时返回空串"""
    m = _SUMMARY_SECTION_RE.search(html_content)
    if not m:
        return ""
    inner = re.sub(r"<br\s*/?>", "\n", m.group(2), flags=re.IGNORECASE)
    inner = re.sub(r"</p>", "\n", inner, flags=re.IGNORECASE)
    inner = re.sub(r"<[^>]+>", "", inner)
    return html.unescape(inner).strip()


def process_article(client, model, html_file, date, force=False):
    """处理单篇文章：AI 摘要 → 注入 HTML 摘要区块

    默认断点续跑：已有摘要的文章直接复用，不再调用 API；force=True 时全部重算。
    """
    filename = html_file.name
    display_title = extract_display_title(filename)
    source = extract_source(filename)

    try:
        content = html_file.read_text(encoding="utf-8")
    except Exception as e:
        return {"success": False, "filename": filename, "source": source, "error": f"读取失败: {e}"}

    if not content.strip():
        return {"success": False, "filename": filename, "source": source, "error": "文件内容为空"}

    if not force:
        existing = _existing_summary_text(content)
        if existing:
            return {
                "success": True,
                "skipped": True,
                "filename": filename,
                "display_title": display_title,
                "source": source,
                "summary": existing,
            }

    # 输入为纯文本（避免样式/标签干扰），写入时覆盖旧摘要区块
    result = summarize_article(client, model, _extract_text_for_llm(content), filename)

    if isinstance(result, tuple):
        return {"success": False, "filename": filename, "source": source, "error": result[1]}

    if result:
        write_summary_to_html(html_file, result)
        return {
            "success": True,
            "filename": filename,
            "display_title": display_title,
            "source": source,
            "summary": result,
        }
    return {"success": False, "filename": filename, "source": source, "error": "API 返回空结果"}


def main():
    limit = None
    date_str = None
    force = False

    for arg in sys.argv[1:]:
        if arg in ("--force", "-f"):
            force = True
        elif arg.isdigit():
            limit = int(arg)
        elif "年" in arg and "月" in arg and "日" in arg:
            date_str = arg

    config = load_config()
    client = create_client(config)
    model = config["model"]

    if date_str:
        today = date_str
        folder = OUTPUT_BASE_DIR / date_str
    else:
        today, folder = get_today_folder()

    if limit:
        print(f"限制处理数量: {limit} 篇")
    print(f"日期: {today}")
    print(f"目标文件夹: {folder}")
    print(f"模式: {'全部重算（--force）' if force else '断点续跑（已有摘要的跳过）'}")
    print()

    html_files = scan_html_files(folder, today)
    if not html_files:
        print("[!] 没有找到需要总结的 .html 文件")
        return

    if limit:
        html_files = html_files[:limit]

    # 按来源分组统计
    source_files = {}
    for f in html_files:
        source = extract_source(f.name)
        source_files.setdefault(source, []).append(f)

    total_count = len(html_files)
    print(f"找到 {total_count} 篇文章待处理，来源分布:")
    for source, files in sorted(source_files.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"  - {source}: {len(files)} 篇")
    print()

    print(f"开始并行处理（并发数: {MAX_WORKERS}）...")
    print()

    processed = 0
    skipped = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_file = {
            executor.submit(process_article, client, model, html_file, today, force): html_file
            for html_file in html_files
        }

        for future in as_completed(future_to_file):
            result = future.result()

            if result["success"]:
                processed += 1
                if result.get("skipped"):
                    skipped += 1
                    print(f"  · [跳过] {result['filename']}", flush=True)
                else:
                    print(f"  ✓ [{processed}/{total_count}] {result['filename']}", flush=True)
                    print(f"    {result['summary'][:50]}...", flush=True)
            else:
                failed += 1
                print(f"  ✗ {result['filename']}: {result['error']}", flush=True)

    print(f"\n{'='*50}")
    print("总结完成！")
    print(f"成功: {processed} 篇（其中跳过已有摘要 {skipped} 篇）")
    if failed > 0:
        print(f"失败: {failed} 篇")
    print("摘要已注入各文章的摘要区块")
    print(f"{'='*50}")


if __name__ == "__main__":
    fix_encoding()
    main()
