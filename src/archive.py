#!/usr/bin/env python3
"""
Folo 文章一键归档脚本（CLI）
连续执行：获取未读文章列表 → 下载网页到本地 → 优化文件名 → HTML 转 Markdown
核心流程见 archive_core.py

用法:
    python archive.py                # 完整执行所有步骤
    python archive.py --start-step 4 # 从第 4 步开始（跳过 1-3）
    python archive.py --only-step 5  # 只执行第 5 步
    python archive.py --list-steps   # 列出所有步骤
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# 源码根（本文件在 src/ 下）
sys.path.insert(0, str(Path(__file__).parent))
# 功能模块目录 src/core/（utils、folo_export 等）
sys.path.insert(0, str(Path(__file__).parent / "core"))

# Windows 终端编码修复（必须在导入其他模块之前执行）
from utils import fix_encoding
fix_encoding()

from archive_core import STEPS, run_archive


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="Folo 文章一键归档脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python archive.py                # 完整执行所有步骤
  python archive.py --start-step 4 # 从第 4 步开始（跳过 1-3）
  python archive.py --only-step 5  # 只执行第 5 步
  python archive.py --list-steps   # 列出所有步骤"""
    )
    parser.add_argument("--start-step", type=int, metavar="N",
                        help="从第 N 步开始执行（跳过前面的步骤）")
    parser.add_argument("--only-step", type=int, metavar="N",
                        help="只执行第 N 步")
    parser.add_argument("--list-steps", action="store_true",
                        help="列出所有步骤及编号")
    parser.add_argument("--date", type=str, metavar="YYYY年MM月DD日",
                        help="指定日期（默认为今天）")
    return parser.parse_args()


def main():
    args = parse_args()

    # 列出所有步骤
    if args.list_steps:
        print("Folo 文章归档步骤：")
        print()
        for num, name, desc in STEPS:
            print(f"  {num}. {desc}")
        print()
        print("用法:")
        print("  python archive.py --start-step 4  # 从第 4 步开始")
        print("  python archive.py --only-step 5   # 只执行第 5 步")
        return

    # 确定日期
    today = args.date or datetime.now().strftime("%Y年%m月%d日")

    # 确定要执行的步骤范围
    if args.only_step is not None and args.start_step is not None:
        print("❌ 不能同时使用 --start-step 和 --only-step")
        sys.exit(1)

    if args.only_step is not None:
        if args.only_step < 1 or args.only_step > len(STEPS):
            print(f"❌ 无效的步骤编号: {args.only_step}（有效范围: 1-{len(STEPS)}）")
            sys.exit(1)
        start_step = args.only_step
        end_step = args.only_step
    elif args.start_step is not None:
        if args.start_step < 1 or args.start_step > len(STEPS):
            print(f"❌ 无效的步骤编号: {args.start_step}（有效范围: 1-{len(STEPS)}）")
            sys.exit(1)
        start_step = args.start_step
        end_step = len(STEPS)
    else:
        start_step = 1
        end_step = len(STEPS)

    if start_step > 1:
        print(f"⏭️  从第 {start_step} 步开始执行（跳过步骤 1-{start_step - 1}）")
        print()

    selected_steps = list(range(start_step, end_step + 1))
    result = run_archive(
        selected_steps,
        today,
        log=lambda message: print(message, flush=True),
    )
    if result["error"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
