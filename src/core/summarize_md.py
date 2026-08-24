#!/usr/bin/env python3
"""
每日 Markdown 文档总结脚本
功能：
1. 扫描当天日期文件夹下的所有 .md 文件
2. 并行调用 MiMo API 生成摘要
3. 将摘要写入每篇文章文件顶部（Obsidian abstract 语法）
4. 将原始文章 + 摘要导入 SQLite 数据库
"""

import json
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock
from openai import OpenAI

from utils import load_config, fix_encoding, DB_PATH, OUTPUT_BASE_DIR

# 并发数（可根据 API 限制调整）
MAX_WORKERS = 10

# 文件写入锁
file_lock = Lock()


def create_client(config):
    """创建 OpenAI 客户端"""
    return OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
    )


def get_today_folder():
    """获取当天日期文件夹路径（相对于项目根目录）"""
    today = datetime.now().strftime("%Y年%m月%d日")
    folder = OUTPUT_BASE_DIR / today
    return today, folder


def scan_md_files(folder, today):
    """扫描目录下的 .md 文件，排除总结文件"""
    if not folder.exists():
        print(f"[!] 文件夹不存在: {folder}")
        return []

    summary_filename = f"{today}.md"
    md_files = []

    for f in sorted(folder.glob("*.md")):
        if f.name == summary_filename:
            continue
        if f.parent.name == "assets":
            continue
        md_files.append(f)

    return md_files


def extract_source(filename):
    """从文件名提取来源名称，如「少数派」文章标题 -> 少数派"""
    match = re.match(r"「(.+?)」", filename)
    return match.group(1) if match else "其他"


def extract_display_title(filename):
    """提取显示标题（去掉来源前缀和 .md 后缀）"""
    name = filename.removesuffix(".md")
    match = re.match(r"「.+?」(.+)", name)
    return match.group(1).strip() if match else name


def summarize_article(client, model, content, filename, max_retries=3):
    """调用 API 生成文章摘要，支持重试"""
    truncated = content[:3000] if len(content) > 3000 else content

    system_prompt = """你是一个专业的文章摘要助手。你的任务是为 Markdown 文章撰写简洁、准确的概括性总结。

你的读者是高中生，他们没有任何行业背景知识。你需要用最通俗易懂的语言，让一个高中生也能完全看懂文章在说什么。

绝对禁止使用任何未解释的专业术语。如果必须使用，必须用括号解释。"""

    user_prompt = f"""请为以下 Markdown 文章撰写概括性总结。

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

【第三步：提取关键词】
在最后一行，用"关键词："开头，列出3-5个关键词，用逗号分隔。关键词应该是文章的核心概念，方便后续检索相关文章。

示例：
关键词：AI, 芯片, 算力

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
                stream=False,
            )
            choice = response.choices[0]
            result = choice.message.content
            finish_reason = choice.finish_reason

            if result and result.strip():
                if finish_reason == "length":
                    if attempt < max_retries - 1:
                        continue
                return result.strip()
            if attempt < max_retries - 1:
                continue
        except Exception as e:
            if attempt < max_retries - 1:
                continue
            return None, str(e)

    return None, "API 返回空内容或截断（已重试 {} 次）".format(max_retries)


def write_abstract_to_file(md_file, summary):
    """将摘要写入文章文件顶部（Obsidian abstract 语法）"""
    with file_lock:
        content = md_file.read_text(encoding="utf-8")

        # 如果已有 abstract，先移除
        if content.startswith("> [!abstract]"):
            lines = content.split("\n")
            i = 0
            while i < len(lines) and (lines[i].startswith("> ") or lines[i].strip() == ""):
                i += 1
            content = "\n".join(lines[i:])

        # 构建 abstract 块
        abstract_lines = ["> [!abstract]"]
        for line in summary.split("\n"):
            line = line.strip()
            if line:
                abstract_lines.append(f"> {line}")
        abstract_block = "\n".join(abstract_lines) + "\n\n"

        # 写入文件
        md_file.write_text(abstract_block + content, encoding="utf-8")


def extract_keywords_from_summary(summary):
    """从摘要中提取关键词"""
    keywords = []
    if "关键词：" in summary:
        keywords_part = summary.split("关键词：")[-1].strip()
        keywords = [kw.strip() for kw in re.split(r'[,，]', keywords_part) if kw.strip()]
    return keywords


def import_article_to_db(date, filename, title, source, summary, keywords):
    """将单篇文章导入数据库"""
    obsidian_link = f"[[{filename}|{title}]]"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 检查是否已存在
    cursor.execute(
        "SELECT id FROM articles WHERE date = ? AND article_filename = ?",
        (date, filename)
    )
    existing = cursor.fetchone()

    if existing:
        article_id = existing[0]
        cursor.execute("""
            UPDATE articles
            SET title = ?, source = ?, summary = ?, obsidian_link = ?
            WHERE id = ?
        """, (title, source, summary, obsidian_link, article_id))
        cursor.execute("DELETE FROM article_keywords WHERE article_id = ?", (article_id,))
    else:
        cursor.execute("""
            INSERT INTO articles (date, summary_file, article_filename, title, source, summary, obsidian_link)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (date, f"{date}.md", filename, title, source, summary, obsidian_link))
        article_id = cursor.lastrowid

    # 插入关键词
    for keyword in keywords:
        cursor.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (keyword,))
        cursor.execute("SELECT id FROM keywords WHERE keyword = ?", (keyword,))
        keyword_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT OR IGNORE INTO article_keywords (article_id, keyword_id)
            VALUES (?, ?)
        """, (article_id, keyword_id))

    conn.commit()
    conn.close()


def process_article(client, model, md_file, date):
    """处理单篇文章：AI 摘要 → 写入文件 → 入库"""
    filename = md_file.name
    display_title = extract_display_title(filename)
    source = extract_source(filename)

    try:
        content = md_file.read_text(encoding="utf-8")
    except Exception as e:
        return {"success": False, "filename": filename, "source": source, "error": f"读取失败: {e}"}

    if not content.strip():
        return {"success": False, "filename": filename, "source": source, "error": "文件内容为空"}

    # 如果已有 abstract，跳过
    if content.startswith("> [!abstract]"):
        # 从 abstract 块中提取摘要文本
        abstract_lines = []
        for line in content.split("\n"):
            if line.startswith("> "):
                text = line[2:]
                if text == "[!abstract]":
                    continue
                abstract_lines.append(text)
            elif line.strip() == "" and abstract_lines:
                break
            else:
                break
        existing_summary = "\n".join(abstract_lines).strip()
        keywords = extract_keywords_from_summary(existing_summary)
        return {
            "success": True,
            "filename": filename,
            "display_title": display_title,
            "source": source,
            "summary": existing_summary,
            "keywords": keywords,
            "skipped": True,
        }

    # 调用 API 生成摘要
    result = summarize_article(client, model, content, filename)

    if isinstance(result, tuple):
        return {"success": False, "filename": filename, "source": source, "error": result[1]}

    if result:
        # 提取关键词
        keywords = extract_keywords_from_summary(result)
        summary_text = result.split("关键词：")[0].strip() if "关键词：" in result else result

        # 写入文章文件
        write_abstract_to_file(md_file, summary_text)

        # 导入数据库
        import_article_to_db(date, filename, display_title, source, summary_text, keywords)

        return {
            "success": True,
            "filename": filename,
            "display_title": display_title,
            "source": source,
            "summary": summary_text,
            "keywords": keywords,
        }
    else:
        return {"success": False, "filename": filename, "source": source, "error": "API 返回空结果"}


def main():
    limit = None
    date_str = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i].isdigit():
            limit = int(args[i])
        elif "年" in args[i] and "月" in args[i] and "日" in args[i]:
            date_str = args[i]
        i += 1

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
    print()

    md_files = scan_md_files(folder, today)
    if not md_files:
        print("[!] 没有找到需要总结的 .md 文件")
        return

    if limit:
        md_files = md_files[:limit]

    # 按来源分组统计
    source_files = {}
    for f in md_files:
        source = extract_source(f.name)
        if source not in source_files:
            source_files[source] = []
        source_files[source].append(f)

    total_count = len(md_files)
    print(f"找到 {total_count} 篇文章待处理，来源分布:")
    for source, files in sorted(source_files.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"  - {source}: {len(files)} 篇")
    print()

    # 并行处理
    print(f"开始并行处理（并发数: {MAX_WORKERS}）...")
    print()

    processed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_file = {
            executor.submit(process_article, client, model, md_file, today): md_file
            for md_file in md_files
        }

        for future in as_completed(future_to_file):
            result = future.result()

            if result["success"]:
                processed += 1
                if result.get("skipped"):
                    print(f"  ○ [{processed}/{total_count}] {result['filename']} (已有摘要，跳过)", flush=True)
                else:
                    print(f"  ✓ [{processed}/{total_count}] {result['filename']}", flush=True)
                    print(f"    {result['summary'][:50]}...", flush=True)
            else:
                failed += 1
                print(f"  ✗ {result['filename']}: {result['error']}", flush=True)

    # 输出统计
    print(f"\n{'='*50}")
    print(f"总结完成！")
    print(f"成功: {processed} 篇")
    if failed > 0:
        print(f"失败: {failed} 篇")
    print(f"摘要已写入各文章文件，已导入数据库")
    print(f"{'='*50}")


if __name__ == "__main__":
    fix_encoding()
    main()
