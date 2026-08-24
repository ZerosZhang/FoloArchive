#!/usr/bin/env python3
"""
从总结文件导入文章数据到 SQLite 数据库
用法: python import_to_db.py [日期]
      python import_to_db.py 2026年07月07日
      python import_to_db.py  # 导入当天
"""

import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from utils import DB_PATH, OUTPUT_BASE_DIR


def extract_keywords(title, summary):
    """从标题和摘要中提取关键词"""
    keywords = set()

    # 从标题提取关键词（按标点分割，取有意义的片段）
    # 例如："AI硬件交易逻辑的变化" -> ["AI硬件", "交易逻辑", "变化"]
    title_parts = re.split(r'[，。！？、；：""''（）\s]+', title)
    for part in title_parts:
        if len(part) >= 2 and len(part) <= 10:
            keywords.add(part)

    # 从摘要提取关键词（取前100字，按标点分割）
    summary_parts = re.split(r'[，。！？、；：""''（）\s]+', summary[:100])
    for part in summary_parts:
        if len(part) >= 2 and len(part) <= 8:
            keywords.add(part)

    # 过滤掉无意义的词
    stop_words = {'的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好', '自己', '这', '这个', '那个', '什么', '怎么', '为什么', '可以', '可能', '应该', '已经', '还是', '就是', '不是', '但是', '而且', '或者', '因为', '所以', '如果', '虽然', '然而', '并且', '或者', '以及', '等', '等等', '之', '其', '中', '与', '及', '或', '但', '而', '却', '又', '再', '才', '已', '正', '将', '会', '能', '可以', '可能', '应该', '必须', '需要', '想要', '希望', '喜欢', '讨厌', '害怕', '担心', '相信', '知道', '认为', '觉得', '感觉', '看起来', '似乎', '好像', '大概', '也许', '可能', '或许', '肯定', '一定', '绝对', '当然', '确实', '真的', '假的', '对的', '错的', '好的', '坏的', '美', '丑', '大', '小', '多', '少', '高', '低', '长', '短', '快', '慢', '新', '旧', '老', '少', '年轻', '年老', '男', '女', '公', '母', '雌', '雄', '正', '负', '加', '减', '乘', '除', '等', '不等', '大于', '小于', '等于', '约等于', '近似', '大约', '左右', '上下', '前后', '里外', '内外', '东西', '南北', '上下', '左右', '前后', '里外', '内外', '东西', '南北'}
    keywords = {kw for kw in keywords if kw not in stop_words and len(kw) >= 2}

    return list(keywords)[:10]  # 最多返回10个关键词


def parse_summary_file(summary_file_path):
    """解析总结文件，提取文章信息"""
    articles = []

    try:
        content = summary_file_path.read_text(encoding='utf-8')
    except Exception as e:
        print(f"  读取失败: {e}")
        return articles

    # 提取日期（从文件名）
    date_match = re.search(r'(\d{4}年\d{2}月\d{2}日)', summary_file_path.name)
    if not date_match:
        return articles
    date = date_match.group(1)

    # 提取来源和文章
    current_source = None
    lines = content.split('\n')

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 检测来源标题（## 一、虎嗅（124 篇））
        source_match = re.match(r'^##\s+[一-龠-9]+、(.+?)（\d+ 篇）', line)
        if source_match:
            current_source = source_match.group(1).strip()
            i += 1
            continue

        # 检测文章标题（### [[「虎嗅」xxx.md|xxx]]）
        title_match = re.match(r'^###\s+\[\[(.+?)\|(.+?)\]\]', line)
        if title_match and current_source:
            article_filename = title_match.group(1)
            title = title_match.group(2)

            # 收集摘要内容（直到下一个标题或空行）
            summary_lines = []
            i += 1
            while i < len(lines):
                next_line = lines[i].strip()
                if next_line.startswith('###') or next_line.startswith('##'):
                    break
                if next_line:
                    summary_lines.append(next_line)
                i += 1

            summary = ' '.join(summary_lines)

            # 构建 Obsidian 链接（日期部分不含 .md）
            date_part = summary_file_path.name.removesuffix(".md")
            obsidian_link = f"[[{date_part}#{article_filename} {title}]]"

            # 提取关键词
            keywords = extract_keywords(title, summary)

            articles.append({
                'date': date,
                'summary_file': summary_file_path.name,
                'article_filename': article_filename,
                'title': title,
                'source': current_source,
                'summary': summary,
                'obsidian_link': obsidian_link,
                'keywords': keywords
            })
        else:
            i += 1

    return articles


def import_articles(date_str=None):
    """导入文章到数据库"""
    # 确定日期
    if date_str is None:
        date_str = datetime.now().strftime("%Y年%m月%d日")

    # 查找总结文件
    summary_file = OUTPUT_BASE_DIR / date_str / f"{date_str}.md"

    if not summary_file.exists():
        print(f"总结文件不存在: {summary_file}")
        return

    print(f"导入日期: {date_str}")
    print(f"总结文件: {summary_file}")

    # 解析总结文件
    articles = parse_summary_file(summary_file)
    print(f"解析到 {len(articles)} 篇文章")

    # 连接数据库
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    imported = 0
    skipped = 0
    failed = 0

    for article in articles:
        try:
            # 检查是否已存在
            cursor.execute(
                "SELECT id FROM articles WHERE date = ? AND article_filename = ?",
                (article['date'], article['article_filename'])
            )
            existing = cursor.fetchone()

            if existing:
                # 更新已存在的文章
                article_id = existing[0]
                cursor.execute("""
                    UPDATE articles
                    SET summary_file = ?, title = ?, source = ?, summary = ?, obsidian_link = ?
                    WHERE id = ?
                """, (
                    article['summary_file'],
                    article['title'],
                    article['source'],
                    article['summary'],
                    article['obsidian_link'],
                    article_id
                ))

                # 删除旧的关键词关联
                cursor.execute("DELETE FROM article_keywords WHERE article_id = ?", (article_id,))

                skipped += 1
            else:
                # 插入新文章
                cursor.execute("""
                    INSERT INTO articles (date, summary_file, article_filename, title, source, summary, obsidian_link)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    article['date'],
                    article['summary_file'],
                    article['article_filename'],
                    article['title'],
                    article['source'],
                    article['summary'],
                    article['obsidian_link']
                ))
                article_id = cursor.lastrowid
                imported += 1

            # 插入关键词（无论新增还是更新）
            for keyword in article['keywords']:
                # 插入关键词（如果不存在）
                cursor.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (keyword,))
                cursor.execute("SELECT id FROM keywords WHERE keyword = ?", (keyword,))
                keyword_id = cursor.fetchone()[0]

                # 插入关联
                cursor.execute("""
                    INSERT OR IGNORE INTO article_keywords (article_id, keyword_id)
                    VALUES (?, ?)
                """, (article_id, keyword_id))

        except Exception as e:
            print(f"  导入失败: {article['title'][:30]}... - {e}")
            failed += 1

    conn.commit()
    conn.close()

    print(f"导入完成: 新增 {imported}, 更新 {skipped}, 失败 {failed}")


def main():
    date_str = None
    if len(sys.argv) > 1:
        date_str = sys.argv[1]

    import_articles(date_str)


if __name__ == "__main__":
    from utils import fix_encoding
    fix_encoding()
    main()
