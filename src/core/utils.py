#!/usr/bin/env python3
"""
公共工具模块
统一各脚本重复的：Windows 编码修复、config.json 加载、耗时格式化
"""

import io
import json
import sys
from pathlib import Path

# =============================================================================
# 目录布局（结构变更后路径的唯一事实来源）
#   Python/          工具包根
#   ├── result/      数据（articles.db、temp_data/）
#   ├── src/         源码（入口 + core/ + strategies/）
#   └── .venv/       虚拟环境
# =============================================================================
PYTHON_ROOT = Path(__file__).resolve().parent.parent.parent  # Python/
RESULT_DIR = PYTHON_ROOT / "result"
TEMP_DIR = RESULT_DIR / "temp_data"                          # 文章列表 JSON
DB_PATH = RESULT_DIR / "articles.db"                         # SQLite 索引
CONFIG_PATH = PYTHON_ROOT / "src" / "config.json"            # DeepSeek API 配置
OUTPUT_BASE_DIR = RESULT_DIR                                 # 文章下载/转换输出目录（result/）

# 字节码缓存统一重定向到 .venv/pycache，避免源码目录产生 __pycache__
# （外部已通过 PYTHONPYCACHEPREFIX 环境变量设置时，保持外部设置优先）
if not sys.pycache_prefix:
    sys.pycache_prefix = str(PYTHON_ROOT / ".venv" / "pycache")


def fix_encoding():
    """Windows 终端 UTF-8 编码修复 + 行缓冲
    pythonw.exe 下 stdout/stderr 为 None，跳过包装；
    已是 UTF-8 编码的流跳过，避免重复包装导致退出时异常"""
    if sys.platform == "win32":
        if sys.stdout is not None and getattr(sys.stdout, "encoding", "").lower() != "utf-8":
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
        if sys.stderr is not None and getattr(sys.stderr, "encoding", "").lower() != "utf-8":
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)
    else:
        if sys.stdout is not None:
            sys.stdout.reconfigure(line_buffering=True)
        if sys.stderr is not None:
            sys.stderr.reconfigure(line_buffering=True)


def load_config():
    """加载 config.json（缺失或字段无效时退出）"""
    config_path = CONFIG_PATH
    if not config_path.exists():
        print(f"[!] 配置文件不存在: {config_path}")
        print("    请复制 config.example.json 为 config.json 并填入你的 Token")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    required = ["api_key", "base_url", "model"]
    for key in required:
        if key not in config or not config[key]:
            print(f"[!] 配置文件缺少必要字段: {key}")
            sys.exit(1)

    return config


def format_duration(seconds):
    """格式化耗时显示"""
    if seconds < 60:
        return f"{seconds:.1f}秒"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}分{secs:.1f}秒"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours}时{minutes}分{secs:.1f}秒"
