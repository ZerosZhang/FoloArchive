#!/usr/bin/env python3
"""
归档进展热力图 —— 数据扫描模块（纯标准库）

扫描结果目录下的日期目录，按天统计归档篇数，供界面绘制热力图。本模块
**不依赖任何第三方库**（不含任何 Qt 控件），只做数据统计。

数据来源：直接扫描 result/ 下的日期目录（不依赖数据库）。
- 只认形如 `YYYY年MM月DD日` 的子目录，解析不出的一律跳过；
- 统计每个日期目录内的文章文件数（`「来源」标题.html` 新格式 与
  `「来源」标题.md` 旧格式都认，让历史归档也能显示）；
- 排除当天索引页 `YYYY年MM月DD日.html/.md`、`assets/` 及任何子目录；
- 同一篇文章同时存在 .html/.md 时按「篇」去重，不重复计数。

单独测试扫描逻辑：
    python src/heatmap.py            # 默认扫 result/
    python src/heatmap.py <目录>     # 指定目录
"""

import os
import re
import sys
from datetime import date
from pathlib import Path

# 日期目录名：2026年10月01日
DATE_DIR_RE = re.compile(r"^(\d{4})年(\d{2})月(\d{2})日$")
# 文章文件：文件名形如 `「来源」标题.html` / `「来源」标题.md`
# 注意收尾的 `」` 在「来源」之后，标题在其后，因此用 `」.*` 而不是 `」\.`
ARTICLE_FILE_RE = re.compile(r"^「[^」]+」.*\.(?:html|md)$", re.IGNORECASE)


def _parse_date(name):
    """把 `YYYY年MM月DD日` 目录名解析成 date，失败返回 None"""
    m = DATE_DIR_RE.match(name)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        # 如 2026年13月40日 这类非法日期
        return None


def scan_daily_counts(base_dir):
    """扫描 base_dir（通常为 result/）下各日期目录的文章篇数

    返回：{日期字符串: 篇数}，例如 {"2026年10月01日": 38}
    """
    base = Path(base_dir)
    result = {}
    if not base.is_dir():
        return result

    for entry in os.scandir(base):
        if not entry.is_dir():
            continue
        day = _parse_date(entry.name)
        if day is None:
            continue

        # 同一篇文章可能同时有 .html 与 .md，按文件名（去扩展名）去重
        stems = set()
        try:
            for f in os.scandir(entry.path):
                if not f.is_file():
                    continue  # 顺带排除 assets/ 等子目录
                if not ARTICLE_FILE_RE.match(f.name):
                    continue  # 排除索引页 `YYYY年MM月DD日.md/html` 及其它文件
                stems.add(os.path.splitext(f.name)[0])
        except OSError:
            # 目录不可读时跳过，不影响其它日期
            continue

        result[entry.name] = len(stems)

    return result


def main():
    """独立运行：打印扫描结果，便于核对统计口径"""
    from utils import fix_encoding

    fix_encoding()
    if len(sys.argv) > 1:
        base = sys.argv[1]
    else:
        from utils import OUTPUT_BASE_DIR

        base = str(OUTPUT_BASE_DIR)

    counts = scan_daily_counts(base)
    print(f"扫描目录：{base}")
    print(f"日期数：{len(counts)}")
    for key in sorted(counts):
        print(f"  {key}: {counts[key]} 篇")


if __name__ == "__main__":
    main()
