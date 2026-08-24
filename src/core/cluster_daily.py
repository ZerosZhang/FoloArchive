#!/usr/bin/env python3
"""
每日汇总二次聚类脚本
功能：
1. 读取当日汇总 + 过去 7 天汇总
2. AI 分析跨日关联（时间线）和当日主题聚类
3. 替换当日汇总文件内容（从按来源分组 → 按主题聚类）
"""

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from openai import OpenAI

from utils import CONFIG_PATH, DB_PATH, RESULT_DIR, OUTPUT_BASE_DIR


def load_config():
    if not CONFIG_PATH.exists():
        print(f"[!] 配置文件不存在: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    for key in ["api_key", "base_url", "model"]:
        if key not in config or not config[key]:
            print(f"[!] 配置文件缺少必要字段: {key}")
            sys.exit(1)
    return config


def create_client(config):
    return OpenAI(api_key=config["api_key"], base_url=config["base_url"])


def parse_date(date_str):
    """解析 '2026年07月08日' 格式的日期"""
    m = re.match(r"(\d{4})年(\d{2})月(\d{2})日", date_str)
    if not m:
        return None
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def format_date(dt):
    return dt.strftime("%Y年%m月%d日")


def get_articles_from_db(date_str):
    """从数据库中读取指定日期的文章"""
    db_path = DB_PATH
    if not db_path.exists():
        return []

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT article_filename, title, source, summary
        FROM articles
        WHERE date = ?
    """, (date_str,))

    articles = []
    for row in cursor.fetchall():
        articles.append({
            "filename": row[0],
            "title": row[1],
            "source": row[2],
            "summary": row[3],
        })

    conn.close()
    return articles


def extract_source(filename):
    """从文件名提取来源名称"""
    match = re.match(r"「(.+?)」", filename)
    return match.group(1) if match else "其他"


def extract_display_title(filename):
    """提取显示标题"""
    name = filename.removesuffix(".md")
    match = re.match(r"「.+?」(.+)", name)
    return match.group(1).strip() if match else name


def get_source_categories():
    """从数据库读取来源分类"""
    db_path = DB_PATH
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT source_name, category FROM sources")
    result = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    return result


def scan_article_files(folder, date_str):
    """扫描文章文件，从 [!abstract] 块中提取摘要"""
    if not folder.exists():
        return []

    summary_filename = f"{date_str}.md"
    articles = []

    for f in sorted(folder.glob("*.md")):
        if f.name == summary_filename:
            continue
        if f.parent.name == "assets":
            continue

        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            continue

        # 提取 abstract 块
        summary = ""
        if content.startswith("> [!abstract]"):
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
            summary = "\n".join(abstract_lines).strip()

        if not summary:
            continue

        filename = f.name
        title = extract_display_title(filename)
        source = extract_source(filename)

        articles.append({
            "filename": filename,
            "title": title,
            "source": source,
            "summary": summary,
        })

    return articles


def get_past_days(target_date_str, days=7):
    """获取过去 N 天的日期字符串列表"""
    target_date = parse_date(target_date_str)
    if not target_date:
        return []
    result = []
    for i in range(1, days + 1):
        dt = target_date - timedelta(days=i)
        result.append(format_date(dt))
    return result


def build_articles_brief(articles, max_per_day=None):
    """将文章列表转为精简文本（标题+一句话摘要）"""
    lines = []
    for i, art in enumerate(articles):
        if max_per_day and i >= max_per_day:
            lines.append(f"  ...（还有 {len(articles) - max_per_day} 篇）")
            break
        # 取摘要的前 80 字
        brief = art["summary"][:80].replace("\n", " ")
        lines.append(f"  - [{art['source']}] {art['title']}: {brief}")
    return "\n".join(lines)


def call_ai(client, model, system_prompt, user_prompt, max_retries=3, max_completion_tokens=16384):
    """调用 AI API，支持重试"""
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_completion_tokens=max_completion_tokens,
                temperature=0.7,
                top_p=0.9,
                stream=False,
            )
            result = response.choices[0].message.content
            finish_reason = response.choices[0].finish_reason

            if finish_reason == 'length':
                print(f"  [!] AI 输出被截断 (attempt {attempt+1})")
                # 如果输出被截断，但有内容，仍然返回
                if result and result.strip():
                    return result.strip()
            elif result and result.strip():
                return result.strip()
            else:
                print(f"  [!] AI 返回为空 (attempt {attempt+1})")
        except Exception as e:
            print(f"  [!] AI 调用异常 (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                continue
            return None
    return None


def step1_cross_day_analysis(client, model, past_days_data, target_date):
    """步骤 2：跨日关联分析，识别持续发展的事件"""
    # 构建过去 7 天的精简内容
    parts = []
    for date_str, articles in past_days_data:
        day_label = date_str.replace("2026年", "").replace("月", "/").replace("日", "")
        brief = build_articles_brief(articles, max_per_day=50)
        parts.append(f"【{day_label}】共 {len(articles)} 篇:\n{brief}")

    articles_text = "\n\n".join(parts)

    system_prompt = """你是一个新闻分析师。你的任务是从过去 7 天的新闻标题和摘要中，识别出持续发展的事件（同一事件在多天出现）。

输出格式要求：
- 每个事件一个条目
- 格式：事件名 | 时间线
- 时间线格式：日期: 关键进展（一句话）
- 只输出有跨天发展的事件（至少出现在 2 天），不要输出单日事件
- 按重要性排序，最多输出 15 个事件

示例输出：
三星存储芯片业绩与股价 | 7/3: 利润超英伟达市场却吓一跳 → 7/5: 芯片股抛售潮席卷全球 → 7/8: 日赚43亿但市场提前定价完美
Anthropic发现AI思考区域 | 7/8: 发现Claude内部J-space结构 → 7/8: 杨立昆反驳称只是考试能力"""

    user_prompt = f"""以下是过去 7 天的新闻标题和摘要，请识别出持续发展的事件。

目标日期：{target_date}

{articles_text}

请输出跨日事件时间线（只输出有跨天发展的事件）："""

    return call_ai(client, model, system_prompt, user_prompt)


def step2_cluster_titles(client, model, today_articles, target_date):
    """步骤 2：纯标题聚类（只发送标题，返回 JSON 分类结果）"""
    # 构建文章标题列表（只包含序号、来源、标题）
    articles_lines = []
    for i, art in enumerate(today_articles):
        articles_lines.append(f"{i+1}. [{art['source']}] {art['title']}")

    articles_text = "\n".join(articles_lines)

    system_prompt = """你是一个新闻编辑。你的任务是将当日新闻按主题分类。

规则：
1. 按主题分组（如 AI与芯片、汽车出行、金融投资、消费零售、社会民生、体育文娱、科技产品、出海创业、生活杂谈等）
2. 每个主题包含一组文章序号
3. **极其重要：不要遗漏任何文章！每篇文章都必须出现在某个主题中。如果某篇文章不好归类，就放到"生活杂谈"主题下。**
4. **极其重要：每篇文章只能出现在一个主题中，不能重复分类。**

输出格式（严格遵守 JSON）：
```json
{
  "categories": [
    {
      "name": "主题名称",
      "article_indices": [1, 3, 5]
    },
    {
      "name": "另一个主题",
      "article_indices": [2, 4]
    }
  ]
}
```

只输出 JSON，不要输出任何其他内容。"""

    user_prompt = f"""日期：{target_date}
共 {len(today_articles)} 篇文章。

以下是当日所有文章的标题：

{articles_text}

请按主题分类，输出 JSON 格式的结果："""

    result = call_ai(client, model, system_prompt, user_prompt)
    if not result:
        print("  [!] AI 返回为空")
        return None

    # 尝试从结果中提取 JSON
    try:
        # 移除可能的 markdown 代码块标记
        json_str = result.strip()
        if json_str.startswith("```"):
            json_str = re.sub(r'^```\w*\n?', '', json_str)
            json_str = re.sub(r'\n?```$', '', json_str)

        # 尝试修复常见的 JSON 格式问题
        # 1. 移除尾部的逗号
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)

        # 2. 尝试解析
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"  [!] JSON 解析失败: {e}")
        print(f"  [!] AI 返回内容前200字: {result[:200]}")

        # 保存原始响应用于调试
        debug_file = RESULT_DIR / "ai_response_debug.txt"
        with open(debug_file, "w", encoding="utf-8") as f:
            f.write(result)
        print(f"  [!] 原始响应已保存到: {debug_file}")

        return None


def validate_clustering(total_articles, clustering_result):
    """验证聚类结果，返回 (categories, missing_indices)"""
    if not clustering_result or "categories" not in clustering_result:
        return [], list(range(1, total_articles + 1))

    all_indices = set(range(1, total_articles + 1))
    classified_indices = set()
    valid_categories = []

    for cat in clustering_result["categories"]:
        cat_name = cat.get("name", "未命名")
        article_indices = cat.get("article_indices", [])

        # 过滤无效索引
        valid_indices = []
        for idx in article_indices:
            if idx in all_indices and idx not in classified_indices:
                valid_indices.append(idx)
                classified_indices.add(idx)

        if valid_indices:
            valid_categories.append({
                "name": cat_name,
                "article_indices": valid_indices
            })

    missing_indices = all_indices - classified_indices
    return valid_categories, missing_indices


def format_articles_for_summary(articles):
    """将文章列表格式化为摘要调用的输入格式"""
    lines = []
    for i, art in enumerate(articles):
        brief = art["summary"][:150].replace("\n", " ")
        lines.append(f"{i+1}. [{art['source']}] {art['title']}\n   摘要: {brief}\n   文件: {art['filename']}")
    return "\n".join(lines)


def summarize_category(client, model, category_name, articles):
    """对单个类别进行总结（独立 AI 调用）"""
    if not articles:
        return None

    articles_text = format_articles_for_summary(articles)

    system_prompt = """你是一个新闻编辑。你的任务是对某个主题下的新闻进行聚类整理。

规则：
1. 将报道同一事件的多篇文章合并为一条（只保留一个摘要，列出所有相关文章链接）
2. 每个事件需要提取关键词（2-4个字，3-5个）
3. 每个事件给出要点总结（1-3句话）
4. 不要遗漏任何文章！每篇文章都必须出现在某个事件的"相关文章"中。
5. 相关文章中只能引用上面提供的文章列表中的文章！绝对不能编造、推测或凭记忆添加任何链接。

相关文章链接格式（Obsidian wikilink）：
- 统一格式：[[文件名|显示标题]]
- 文件名包含来源前缀，如：[[「虎嗅」文章标题.md|文章标题]]

输出格式（严格遵守）：
## 主题名（N 篇）

主题概述：一句话说明这个主题今天的核心动态。

### 事件标题
关键词：词1, 词2, 词3

今日要点：
- 要点1
- 要点2

相关文章：
- [[「虎嗅」文章标题.md|文章标题]]
- [[「36氪」另一篇文章.md|另一篇文章]]

### 另一个事件标题
关键词：词4, 词5, 词6

今日要点：
- 要点1

相关文章：
- [[「虎嗅」xxx.md|xxx]]"""

    user_prompt = f"""主题：{category_name}
共 {len(articles)} 篇文章。

以下是该主题下的所有文章：

{articles_text}

请按事件聚类整理，输出 Markdown 格式的结果（从 ## 主题名开始）："""

    return call_ai(client, model, system_prompt, user_prompt)


def assemble_clustered_content(categories_summaries):
    """将各个类别的总结组装成完整文档"""
    if not categories_summaries:
        return ""

    parts = []
    ordinals = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十"]

    for i, (cat_name, summary) in enumerate(categories_summaries):
        if not summary:
            continue

        # 添加序号（一、二、三...）
        if i < len(ordinals):
            ordinal = ordinals[i]
            # 替换 ## 标题，使用传入的类别名称
            # 匹配 ## 开头的标题行（可能包含序号和篇数）
            def replace_title(match):
                return f'## {ordinal}、{cat_name}（{match.group(1)}篇）'
            summary = re.sub(
                r'^##\s+.+?（(\d+)篇）',
                replace_title,
                summary,
                count=1,
                flags=re.MULTILINE
            )
        parts.append(summary)

    return "\n\n---\n\n".join(parts)


def fix_section_counts(clustered_content):
    """修正聚类结果中每个主题的篇数（基于今日文章链接数，不含历史）"""
    lines = clustered_content.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # 检测 ## 主题标题
        m = re.match(r'^(##\s+.+?)（\d+\s*篇）$', line)
        if m:
            prefix = m.group(1)
            # 统计该主题下的今日文章链接数（排除"历史相关文章"部分）
            link_count = 0
            in_history = False
            j = i + 1
            while j < len(lines):
                if lines[j].startswith("## "):
                    break
                if lines[j].strip() == "历史相关文章：":
                    in_history = True
                elif lines[j].startswith("### "):
                    in_history = False
                elif lines[j].strip().startswith("- [[") and not in_history:
                    link_count += 1
                j += 1
            result.append(f"{prefix}（{link_count}篇）")
            i += 1
            continue
        result.append(line)
        i += 1
    return "\n".join(result)


def assemble_final(target_date, total_count, clustered_content):
    """组装最终文件内容"""
    header = f"本文件汇总了当日收录的 {total_count} 篇文章，按主题聚类整理，合并重复内容，关联过去 7 天时间线。\n\n---\n\n"
    return header + clustered_content


def filter_related_articles(clustered_content, today_filenames):
    """从"相关文章"中移除所有非今日文章链接"""
    lines = clustered_content.split("\n")
    result = []
    in_related = False  # 是否在"相关文章："部分（非"历史相关文章"）

    for line in lines:
        stripped = line.strip()

        if stripped == "相关文章：":
            in_related = True
            result.append(line)
            continue
        elif stripped == "历史相关文章：":
            in_related = False
            result.append(line)
            continue
        elif stripped.startswith("### ") or stripped.startswith("## "):
            in_related = False
            result.append(line)
            continue

        if in_related and stripped.startswith("- [["):
            # 提取文件名
            m = re.match(r'- \[\[「(.+?)」(.+?)\.md\|', stripped)
            if m:
                source, title = m.groups()
                filename = f"「{source}」{title}.md"
                if filename not in today_filenames:
                    continue  # 跳过非今日文章

        result.append(line)

    return "\n".join(result)


def add_history_by_keywords(clustered_content, target_date, days=7):
    """从聚类结果中提取关键词，用关键词从数据库查历史相关文章"""
    db_path = DB_PATH
    if not db_path.exists():
        print("  [!] 数据库不存在，跳过")
        return clustered_content

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    start_date_str = start_date.strftime("%Y年%m月%d日")
    # 排除今日
    exclude_date = target_date

    lines = clustered_content.split("\n")
    result = []
    i = 0
    enriched_count = 0

    while i < len(lines):
        line = lines[i]
        result.append(line)

        # 检测 ### 事件标题
        if line.startswith("### "):
            event_title = line[4:].strip()

            # 向后找关键词行
            keywords = []
            j = i + 1
            while j < len(lines) and not lines[j].startswith("### ") and not lines[j].startswith("## "):
                if lines[j].strip().startswith("关键词：") or lines[j].strip().startswith("关键词:"):
                    kw_text = lines[j].strip().replace("关键词：", "").replace("关键词:", "")
                    keywords = [kw.strip() for kw in re.split(r"[,，]", kw_text) if kw.strip()]
                    break
                j += 1

            # 继续输出后续行，直到找到"相关文章："之后的所有链接
            i += 1
            while i < len(lines):
                curr = lines[i]

                if curr.startswith("### ") or curr.startswith("## "):
                    break

                result.append(curr)

                # 在相关文章的链接之后，插入历史相关文章
                if curr.strip() == "相关文章：":
                    i += 1
                    while i < len(lines) and lines[i].strip().startswith("- [["):
                        result.append(lines[i])
                        i += 1
                    # 用关键词查历史文章
                    if keywords:
                        history_links = query_by_keywords(cursor, keywords, start_date_str, exclude_date)
                        if history_links:
                            result.append("")
                            result.append("历史相关文章：")
                            result.extend(history_links)
                            enriched_count += 1
                    continue

                i += 1
            continue

        i += 1

    conn.close()
    if enriched_count > 0:
        print(f"  为 {enriched_count} 个事件添加了历史相关文章")
    return "\n".join(result)


def query_by_keywords(cursor, keywords, start_date_str, exclude_date):
    """用关键词列表查询数据库中的历史文章"""
    generic_keywords = {'AI', '公司', '市场', '行业', '技术', '产品', '用户', '企业', '发展', '中国', '美国', '全球'}

    all_related = []
    for kw in keywords:
        if kw in generic_keywords:
            continue
        cursor.execute("""
            SELECT DISTINCT a.date, a.title, a.source, a.obsidian_link
            FROM articles a
            JOIN article_keywords ak ON a.id = ak.article_id
            JOIN keywords k ON ak.keyword_id = k.id
            WHERE k.keyword LIKE ?
            AND a.date >= ?
            AND a.date != ?
            ORDER BY a.date DESC
        """, (f"%{kw}%", start_date_str, exclude_date))
        all_related.extend(cursor.fetchall())

    # 去重
    seen = set()
    unique = []
    for date, title, source, link in all_related:
        key = f"{date}_{title}"
        if key not in seen:
            seen.add(key)
            unique.append(link)

    if not unique:
        return []

    return [f"- {link}" for link in unique]


def find_missing_articles(today_articles, clustered_content):
    """检查哪些文章没有出现在聚类结果中"""
    # 提取聚类结果中的所有链接文件名
    linked = set()
    for match in re.finditer(r'\[\[「(.+?)」(.+?)\.md\|', clustered_content):
        source, title = match.groups()
        linked.add(f"「{source}」{title}.md")

    # 找出缺失的文章
    missing = []
    for art in today_articles:
        if art["filename"] not in linked:
            missing.append(art)

    return missing


def append_missing_articles(clustered_content, missing_articles):
    """将缺失的文章追加到聚类结果末尾"""
    if not missing_articles:
        return clustered_content

    lines = [
        "",
        "---",
        "",
        f"## 未归类文章（{len(missing_articles)} 篇）",
        "",
        "以下文章未被归入上述主题，按来源列出。",
        "",
    ]

    # 按来源分组
    by_source = {}
    for art in missing_articles:
        source = art["source"]
        if source not in by_source:
            by_source[source] = []
        by_source[source].append(art)

    for source, articles in sorted(by_source.items(), key=lambda x: -len(x[1])):
        lines.append(f"### {source}（{len(articles)} 篇）")
        lines.append("")
        for art in articles:
            lines.append(f"- [[{art['filename']}|{art['title']}]]")
        lines.append("")

    return clustered_content + "\n".join(lines)


def enrich_with_related_articles(clustered_content, days=7, target_date=None):
    """为聚类结果中的每个事件标题查找过去 N 天的相关文章"""
    db_path = DB_PATH
    if not db_path.exists():
        print("  [!] 数据库不存在，跳过关联查询")
        return clustered_content

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 计算日期范围
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    start_date_str = start_date.strftime("%Y年%m月%d日")
    end_date_str = end_date.strftime("%Y年%m月%d日")

    # 匹配 ### 事件标题 和其下的 相关文章： 部分
    lines = clustered_content.split("\n")
    result_lines = []
    i = 0
    enriched_count = 0

    while i < len(lines):
        line = lines[i]
        result_lines.append(line)

        # 检测 ### 事件标题
        if line.startswith("### "):
            event_title = line[4:].strip()
            # 标记：后续需要查询历史相关文章
            needs_enrichment = True

            # 查找该事件后面的相关文章部分，在其后插入过去的相关文章
            # 先继续输出后续行，直到找到"相关文章："或下一个 ###
            i += 1
            found_related = False
            while i < len(lines):
                curr = lines[i]

                if curr.startswith("### ") or curr.startswith("## "):
                    # 到了下一个事件/主题，还没找到相关文章，就插入
                    if not found_related and needs_enrichment:
                        past_links = query_past_related(cursor, event_title, start_date_str, end_date_str, exclude_date=target_date)
                        if past_links:
                            result_lines.append("")
                            result_lines.append("历史相关文章：")
                            result_lines.extend(past_links)
                            enriched_count += 1
                    break

                result_lines.append(curr)

                if curr.strip() == "相关文章：":
                    found_related = True
                    # 继续收集现有的相关文章行
                    i += 1
                    while i < len(lines) and lines[i].strip().startswith("- [["):
                        result_lines.append(lines[i])
                        i += 1
                    # 在现有相关文章后追加过去的相关文章
                    if needs_enrichment:
                        past_links = query_past_related(cursor, event_title, start_date_str, end_date_str, exclude_date=target_date)
                        if past_links:
                            result_lines.append("")
                            result_lines.append("历史相关文章：")
                            result_lines.extend(past_links)
                            enriched_count += 1
                    continue

                i += 1
            continue

        i += 1

    conn.close()
    if enriched_count > 0:
        print(f"  为 {enriched_count} 个事件添加了历史相关文章")
    return "\n".join(result_lines)


def query_past_related(cursor, event_title, start_date_str, end_date_str, exclude_date=None):
    """用事件标题反向匹配数据库中的关键词，查找相关文章"""
    query = """
        SELECT DISTINCT k.keyword, a.date, a.title, a.source, a.obsidian_link
        FROM keywords k
        JOIN article_keywords ak ON k.id = ak.keyword_id
        JOIN articles a ON ak.article_id = a.id
        WHERE ? LIKE '%' || k.keyword || '%'
        AND LENGTH(k.keyword) >= 2
        AND a.date >= ? AND a.date <= ?
    """
    params = [event_title, start_date_str, end_date_str]
    if exclude_date:
        query += " AND a.date != ?"
        params.append(exclude_date)
    query += " ORDER BY a.date DESC"
    cursor.execute(query, params)
    results = cursor.fetchall()

    # 通用关键词集合
    generic_keywords = {'AI', '公司', '市场', '行业', '技术', '产品', '用户', '企业', '发展', '中国', '美国', '全球'}

    # 按文章分组，记录每篇文章匹配到的关键词
    articles_map = {}
    for kw, date, title, source, link in results:
        key = f"{date}_{title}"
        if key not in articles_map:
            articles_map[key] = {"date": date, "title": title, "source": source, "link": link, "keywords": set()}
        articles_map[key]["keywords"].add(kw)

    # 只保留匹配到至少一个非通用关键词的文章
    unique = []
    for key, info in articles_map.items():
        non_generic = info["keywords"] - generic_keywords
        if non_generic:
            unique.append((info["date"], info["title"], info["source"], info["link"]))

    if not unique:
        return []

    links = []
    for date, title, source, link in unique:
        links.append(f"- {link}")
    return links


def main():
    # 解析命令行参数
    date_str = None
    args = sys.argv[1:]
    for arg in args:
        if "年" in arg and "月" in arg and "日" in arg:
            date_str = arg

    if not date_str:
        date_str = format_date(datetime.now())

    project_root = OUTPUT_BASE_DIR
    target_folder = project_root / date_str
    target_file = target_folder / f"{date_str}.md"

    if not target_folder.exists():
        print(f"[!] 当日文件夹不存在: {target_folder}")
        sys.exit(1)

    print(f"目标日期: {date_str}")
    print(f"文件夹: {target_folder}")
    print()

    # 加载配置
    config = load_config()
    client = create_client(config)
    model = config["model"]

    # 步骤 1：从文章文件中读取摘要
    print("步骤 1: 从文章文件中读取摘要...")
    today_articles = scan_article_files(target_folder, date_str)
    print(f"  当日文章: {len(today_articles)} 篇")

    if not today_articles:
        print("[!] 没有解析到文章，退出")
        sys.exit(1)

    # 按来源分类拆分文章
    source_cats = get_source_categories()
    news_articles = []      # 新闻热点
    blog_articles = []      # 个人博客
    tool_articles = []      # 软件工具
    unknown_articles = []   # 未归类

    for art in today_articles:
        cat = source_cats.get(art["source"])
        if cat == "新闻热点":
            news_articles.append(art)
        elif cat == "个人博客":
            blog_articles.append(art)
        elif cat == "软件工具":
            tool_articles.append(art)
        else:
            unknown_articles.append(art)

    print(f"  新闻热点: {len(news_articles)} 篇")
    print(f"  个人博客: {len(blog_articles)} 篇")
    print(f"  软件工具: {len(tool_articles)} 篇")
    if unknown_articles:
        print(f"  未归类来源: {len(unknown_articles)} 篇")

    # 步骤 2：纯标题聚类（AI）
    print("步骤 2: 纯标题聚类（AI）...")
    clustering_result = step2_cluster_titles(client, model, news_articles, date_str)
    if not clustering_result:
        print("[!] 标题聚类失败，退出")
        sys.exit(1)

    # 保存原始聚类结果用于调试
    debug_file = RESULT_DIR / "clustering_debug.json"
    with open(debug_file, "w", encoding="utf-8") as f:
        json.dump(clustering_result, f, ensure_ascii=False, indent=2)
    print(f"  原始聚类结果已保存到: {debug_file}")

    # 步骤 3：验证聚类结果
    print("步骤 3: 验证聚类结果...")
    categories, missing_indices = validate_clustering(len(news_articles), clustering_result)
    print(f"  有效类别: {len(categories)} 个")
    print(f"  聚类文章: {sum(len(cat['article_indices']) for cat in categories)} 篇")
    if missing_indices:
        print(f"  未归类文章: {len(missing_indices)} 篇，将添加到'未归类'类别")
        categories.append({
            "name": "未归类",
            "article_indices": list(missing_indices)
        })

    # 步骤 4：逐类别总结（每个类别独立 AI 调用）
    print("步骤 4: 逐类别总结...")
    categories_summaries = []
    for i, cat in enumerate(categories):
        cat_name = cat["name"]
        article_indices = cat["article_indices"]
        # 获取该类别的文章列表
        cat_articles = [news_articles[idx - 1] for idx in article_indices if 1 <= idx <= len(news_articles)]
        print(f"  [{i+1}/{len(categories)}] {cat_name}（{len(cat_articles)} 篇）...")
        summary = summarize_category(client, model, cat_name, cat_articles)
        if summary:
            categories_summaries.append((cat_name, summary))
        else:
            print(f"    [!] {cat_name} 总结失败")

    if not categories_summaries:
        print("[!] 所有类别总结失败，退出")
        sys.exit(1)

    # 步骤 5：组装聚类结果
    print("步骤 5: 组装聚类结果...")
    clustered_content = assemble_clustered_content(categories_summaries)

    # 过滤"相关文章"中的非今日文章
    today_filenames = {art["filename"] for art in news_articles}
    clustered_content = filter_related_articles(clustered_content, today_filenames)
    print("  已过滤相关文章中的非今日文章")

    # 步骤 5b：检查并补充遗漏的文章
    print("步骤 5b: 检查遗漏文章...")
    missing_after_summary = find_missing_articles(news_articles, clustered_content)
    if missing_after_summary:
        print(f"  发现 {len(missing_after_summary)} 篇遗漏文章，补充到末尾...")
        clustered_content = append_missing_articles(clustered_content, missing_after_summary)
    else:
        print("  无遗漏文章")

    print()

    # 步骤 6：用关键词从数据库查历史相关文章
    print("步骤 6: 查找历史相关文章...")
    clustered_content = add_history_by_keywords(clustered_content, date_str)
    print()

    # 步骤 7：追加个人博客和软件工具文章
    other_sections = []
    if blog_articles:
        other_sections.append(("个人博客", blog_articles))
    if tool_articles:
        other_sections.append(("软件工具", tool_articles))
    if unknown_articles:
        other_sections.append(("未归类来源", unknown_articles))

    if other_sections:
        lines = ["", "---", ""]
        for cat_name, arts in other_sections:
            lines.append(f"## {cat_name}（{len(arts)} 篇）")
            lines.append("")
            for art in arts:
                lines.append(f"- [[{art['filename']}|{art['title']}]]")
            lines.append("")
        clustered_content += "\n".join(lines)
        print(f"  追加: {', '.join(f'{n}({len(a)}篇)' for n, a in other_sections)}")

    # 步骤 8：修正篇数并组装
    print("步骤 8: 修正篇数并覆盖原文件...")
    clustered_content = fix_section_counts(clustered_content)
    final_content = assemble_final(date_str, len(today_articles), clustered_content)
    target_file.write_text(final_content, encoding="utf-8")
    print(f"  已覆盖: {target_file}")

    print()
    print(f"{'='*50}")
    print(f"二次聚类完成！")
    print(f"文件: {target_file}")
    print(f"文章数: {len(today_articles)} 篇")
    print(f"{'='*50}")


if __name__ == "__main__":
    from utils import fix_encoding
    fix_encoding()
    main()
