#!/usr/bin/env python3
"""
查询相关文章
用法: python query_related.py "AI硬件" 7
      python query_related.py "关键词" [天数]
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from utils import DB_PATH


def query_related_articles(keyword, days=7):
    """查询过去N天内包含关键词的相关文章"""
    if not DB_PATH.exists():
        print("数据库不存在，请先运行 init_db.py")
        return []

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 计算日期范围
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    # 格式化日期
    start_date_str = start_date.strftime("%Y年%m月%d日")
    end_date_str = end_date.strftime("%Y年%m月%d日")

    # 查询包含关键词的文章
    cursor.execute("""
        SELECT DISTINCT a.id, a.date, a.title, a.source, a.summary, a.obsidian_link
        FROM articles a
        JOIN article_keywords ak ON a.id = ak.article_id
        JOIN keywords k ON ak.keyword_id = k.id
        WHERE k.keyword LIKE ?
        AND a.date >= ? AND a.date <= ?
        ORDER BY a.date DESC
    """, (f"%{keyword}%", start_date_str, end_date_str))

    results = cursor.fetchall()
    conn.close()

    return results


def query_related_by_title(title, days=7):
    """根据标题查询相关文章（模糊匹配）"""
    if not DB_PATH.exists():
        print("数据库不存在，请先运行 init_db.py")
        return []

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 计算日期范围
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    # 格式化日期
    start_date_str = start_date.strftime("%Y年%m月%d日")
    end_date_str = end_date.strftime("%Y年%m月%d日")

    # 查询标题相似的文章
    cursor.execute("""
        SELECT DISTINCT a.id, a.date, a.title, a.source, a.summary, a.obsidian_link
        FROM articles a
        WHERE a.title LIKE ?
        AND a.date >= ? AND a.date <= ?
        ORDER BY a.date DESC
    """, (f"%{title}%", start_date_str, end_date_str))

    results = cursor.fetchall()
    conn.close()

    return results


def format_related_articles(articles, max_count=5, keywords=None):
    """格式化相关文章输出"""
    if not articles:
        return ""

    lines = []

    # 如果有关键词，先显示关键词
    if keywords:
        lines.append(f"关键词：{', '.join(keywords)}")

    lines.append("相关文章：")
    for i, (id, date, title, source, summary, obsidian_link) in enumerate(articles[:max_count]):
        lines.append(f"- {obsidian_link}")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("用法: python query_related.py '关键词' [天数]")
        print("示例: python query_related.py 'AI硬件' 7")
        return

    keyword = sys.argv[1]
    days = 7
    if len(sys.argv) > 2:
        days = int(sys.argv[2])

    print(f"查询关键词: {keyword}")
    print(f"查询范围: 过去 {days} 天")
    print()

    results = query_related_articles(keyword, days)

    if results:
        print(f"找到 {len(results)} 篇相关文章:")
        for id, date, title, source, summary, obsidian_link in results:
            print(f"  [{date}] {title}")
            print(f"    来源: {source}")
            print(f"    链接: {obsidian_link}")
            print()
    else:
        print("没有找到相关文章")


if __name__ == "__main__":
    from utils import fix_encoding
    fix_encoding()
    main()
