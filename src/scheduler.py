#!/usr/bin/env python3
"""
常驻定时运行（无界面）

在后台一直运行，每天到设定时刻自动执行一次完整归档。
真正的归档动作交给 `src/archive.py`（子进程），因此日志输出、失败邮件、
退出码全部复用既有逻辑，本模块只负责「什么时候跑」。

「当天是否已跑过」的判据：
- 优先看步骤 1 产出的列表 JSON `result/temp_data/「日期」.json` 是否存在 ——
  它是持久化产物，进程重启后依然有效，可避免重复执行会「把 Folo 未读标记
  为已读」的步骤 1；
- 会话内再用 `last_run_date` 兜底：当天若没有未读文章则不会生成 JSON，
  仅靠文件判断会导致到点后反复触发。

设定时刻来自 `config.json` 的 `schedule` 段（`{"enabled": bool, "time": "HH:MM"}`），
也可用命令行 `--time` 覆盖。同一时间只允许一个实例运行（Windows 命名互斥量）。

用法:
    python src/scheduler.py              # 用 config.json 里的设定
    python src/scheduler.py --time 08:00 # 覆盖设定时刻
"""

import argparse
import ctypes
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# 脚本所在目录（src/）
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
# 功能模块目录 src/core/
sys.path.insert(0, str(SCRIPT_DIR / "core"))

from utils import fix_encoding, CONFIG_PATH, TEMP_DIR, PYTHON_ROOT

ARCHIVE_PY = SCRIPT_DIR / "archive.py"
LOG_PATH = PYTHON_ROOT / "result" / "scheduler.log"

CHECK_INTERVAL = 20          # 秒，轮询检测间隔
DEFAULT_TIME = "08:00"
MUTEX_NAME = "FoloArchiveScheduler"
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _log(message):
    """同时写日志文件与控制台（pythonw 下无控制台则只写文件）"""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001 - 无控制台时忽略
        pass


def _load_schedule():
    """读取 config.json 的 schedule 段；缺失或非法时回退默认值"""
    enabled = True
    at = DEFAULT_TIME
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError):
        return enabled, at

    schedule = config.get("schedule") if isinstance(config, dict) else None
    if not isinstance(schedule, dict):
        return enabled, at
    if "enabled" in schedule:
        enabled = bool(schedule.get("enabled"))
    value = schedule.get("time")
    if isinstance(value, str) and TIME_RE.match(value.strip()):
        at = value.strip()
    return enabled, at


def _target_today(at):
    """今天的设定时刻（datetime）"""
    hour, minute = (int(x) for x in at.split(":"))
    return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)


def _list_json_path(today):
    """当天文章列表 JSON 的路径（步骤 1 的产物）"""
    return TEMP_DIR / f"「{today}」.json"


def _acquire_single_instance():
    """用 Windows 命名互斥量保证只有一个常驻实例；成功返回句柄，已存在返回 None"""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True  # 拿不到句柄就不阻塞启动，退化为不检测
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            return None
        return handle
    except Exception:  # noqa: BLE001 - 非 Windows 或调用失败时不阻塞
        return True


def _run_archive():
    """调用 archive.py 跑一次完整归档，输出实时写入日志"""
    _log("开始执行归档: python src/archive.py")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(ARCHIVE_PY)],
            cwd=str(PYTHON_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in proc.stdout:
            _log("  " + line.rstrip("\n"))
        code = proc.wait()
        _log(f"归档结束，退出码={code}")
    except Exception as exc:  # noqa: BLE001 - 单次失败不能让常驻进程退出
        _log(f"启动归档失败: {type(exc).__name__}: {exc}")


def _log_tail(n=12):
    """把日志末尾若干行回显到控制台，便于调试"""
    try:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
        for line in lines[-n:]:
            print(line, flush=True)
    except (OSError, UnicodeError):
        pass


def main():
    parser = argparse.ArgumentParser(
        description="Folo 归档常驻定时运行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python scheduler.py                 # 用 config.json 的 schedule 段
  python scheduler.py --time 08:00    # 覆盖设定时刻
  python scheduler.py --tail          # 只回显最近日志后退出""",
    )
    parser.add_argument("--time", metavar="HH:MM", help="覆盖设定时刻，如 08:00")
    parser.add_argument("--tail", action="store_true", help="只回显最近日志后退出")
    args = parser.parse_args()

    fix_encoding()

    if args.tail:
        _log_tail(30)
        return

    if args.time:
        if not TIME_RE.match(args.time.strip()):
            print(f"[!] 时刻格式无效: {args.time}（应为 HH:MM，如 08:00）")
            sys.exit(1)
        at = args.time.strip()
        enabled = True
    else:
        enabled, at = _load_schedule()

    guard = _acquire_single_instance()
    if guard is None:
        _log("已有常驻实例在运行，本进程退出")
        print("已有常驻实例在运行，本进程退出。", flush=True)
        return

    _log("=" * 60)
    _log(f"常驻定时已启动：每天 {at} 执行一次完整归档")
    _log(f"配置来源: {CONFIG_PATH}（enabled={enabled}）")
    _log(f"日志文件: {LOG_PATH}")
    _log("按 Ctrl+C 退出")
    _log("=" * 60)

    last_run_date = None
    while True:
        try:
            today = datetime.now().strftime("%Y年%m月%d日")
            if enabled and last_run_date != today and datetime.now() >= _target_today(at):
                if _list_json_path(today).exists():
                    last_run_date = today
                    _log(f"当天已归档过（存在 {_list_json_path(today).name}），跳过")
                else:
                    last_run_date = today
                    _run_archive()
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            _log("收到中断信号，常驻进程退出")
            break
        except Exception as exc:  # noqa: BLE001 - 循环内异常不允许终止常驻
            _log(f"循环异常: {type(exc).__name__}: {exc}")
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
