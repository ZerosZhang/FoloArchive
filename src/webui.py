#!/usr/bin/env python3
"""
Folo 文章归档 —— 本地网页版界面（纯标准库实现）

用途：项目唯一的图形界面。用浏览器访问一个本地 HTTP 服务完成归档工作：
手动归档、实时日志、进度条、耗时统计、失败邮件、归档热力图，以及每日定时
归档（内建调度线程，与独立调度器 scheduler.py 通过命名互斥量互斥）。

设计要点：
- 只依赖标准库：http.server / socketserver / json / threading / ctypes /
  webbrowser / urllib.parse。
- 复用现成接口、不重写流程：archive_core.STEPS / run_archive、
  heatmap.scan_daily_counts、notify.*。
- 归档跑在后台线程，HTTP 服务保持响应；同一时间只允许一次运行。
- 底层模块（render_html / summarize / save_webpages）用 print() 直接输出，
  因此把 sys.stdout / sys.stderr 重定向到线程安全的环形缓冲区，并按行增量
  提供给前端；可控台同步镜像一份，方便命令行观察。

运行：
    python src/webui.py
    python src/webui.py --host 0.0.0.0 --port 9000 --no-browser
    python src/webui.py --no-schedule          # 关闭内建定时（改由 python src/scheduler.py 独立调度）
"""

import argparse
import ctypes
import io
import json
import os
import re
import sys
import threading
import time
import traceback
import webbrowser
from collections import deque
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

# 脚本所在目录（src/）与功能模块目录（src/core/）
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR / "core"))

# Windows 终端编码修复（必须在导入其他脚本之前执行）
from utils import fix_encoding, CONFIG_PATH, OUTPUT_BASE_DIR, TEMP_DIR

fix_encoding()

from archive_core import STEPS      # noqa: E402
import heatmap                      # noqa: E402
import notify                       # noqa: E402
from webui_page import PAGE         # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
LOG_MAX_LINES = 2000
HEATMAP_TTL = 5.0
SCHEDULE_TTL = 10.0


# =============================================================================
# 日志缓冲区（线程安全、有上限）
# =============================================================================
class LogBuffer(io.TextIOBase):
    """把 stdout/stderr 与控制台日志合并成一个有上限的行缓冲区

    - write()：按行切分（保留无换行的残片，交由后续写入拼接）
    - append()：走 log 回调的完整消息，直接作为一行
    - lines_since(idx)：增量拉取，返回 (lines, next_index)
    - 可选的镜像流：把每行同时写回原始终端，命令行仍能看到输出
    """

    def __init__(self, max_lines=LOG_MAX_LINES):
        super().__init__()
        self._lock = threading.Lock()
        self._lines = deque(maxlen=max_lines)
        self._partial = ""
        self._next = 0
        self._mirror = None

    def set_mirror(self, stream):
        """设置镜像输出流（原始终端），为 None 则不镜像"""
        self._mirror = stream

    def _mirror_line(self, line):
        if self._mirror is None:
            return
        try:
            self._mirror.write(line + "\n")
            self._mirror.flush()
        except Exception:  # noqa: BLE001 - 镜像失败不影响日志本身
            pass

    def write(self, text):
        if not isinstance(text, str):
            text = str(text)
        out = []
        with self._lock:
            self._partial += text
            while "\n" in self._partial:
                line, self._partial = self._partial.split("\n", 1)
                self._lines.append((self._next, line))
                self._next += 1
                out.append(line)
        for line in out:
            self._mirror_line(line)
        return len(text)

    def append(self, message):
        """日志回调入口：直接追加一整行（不经过 stdout 的残片缓冲）"""
        if message is None:
            return
        line = str(message)
        with self._lock:
            self._lines.append((self._next, line))
            self._next += 1
        self._mirror_line(line)

    def flush(self):
        # 有意不发射残片：print(..., end="") 的前缀应与后续输出合并成一行
        pass

    def flush_partial(self):
        """把缓冲区里最后一段无换行的残余作为一行发出（运行结束时调用）"""
        with self._lock:
            if not self._partial:
                return
            line, self._partial = self._partial, ""
            self._lines.append((self._next, line))
            self._next += 1
        self._mirror_line(line)

    def lines_since(self, since):
        with self._lock:
            lines = [text for idx, text in self._lines if idx >= since]
            return lines, self._next

    def next_index(self):
        with self._lock:
            return self._next


LOG = LogBuffer()


# =============================================================================
# 运行状态
# =============================================================================
_STATE_LOCK = threading.Lock()
_STATE = {
    "running": False,
    "progress": 0,
    "progress_text": "就绪",
    "step_times": {},
    "should_stop": False,
}


def _on_progress(value, text):
    with _STATE_LOCK:
        try:
            _STATE["progress"] = int(value)
        except (TypeError, ValueError):
            pass
        if text:
            _STATE["progress_text"] = str(text)


def _should_stop():
    with _STATE_LOCK:
        return bool(_STATE["should_stop"])


def _run_worker(steps, date_str):
    """后台线程：执行归档 → 刷新状态 → 失败通知"""
    result = None
    try:
        from archive_core import run_archive

        result = run_archive(
            steps,
            date_str,
            log=LOG.append,
            on_progress=_on_progress,
            should_stop=_should_stop,
        )
    except Exception as exc:  # noqa: BLE001 - 任何异常都只记日志，线程必须干净退出
        LOG.append(f"❌ 发生错误: {exc}")
        LOG.append(traceback.format_exc())
    finally:
        LOG.flush_partial()
        with _STATE_LOCK:
            if result and result.get("step_times"):
                _STATE["step_times"] = dict(result["step_times"])
            _STATE["progress"] = 100
            _STATE["progress_text"] = "完成"
            _STATE["running"] = False
        LOG.append("[WebUI] 归档线程结束")

    if result is not None:
        _notify_failures(date_str, result.get("failures") or [], result.get("step_times") or {})


def _notify_failures(today, failures, step_times):
    """失败邮件通知：未配置只记一行，绝不抛异常"""
    failures = list(failures or [])
    if not failures:
        return
    try:
        cfg = notify.load_mail_config()
    except Exception as exc:  # noqa: BLE001
        LOG.append(f"⚠️ 无法加载邮件模块: {exc}")
        return
    if cfg is None:
        LOG.append("⚠️ 本次存在失败，但未配置邮件通知（config.json 的 mail 段缺失或未启用）")
        return
    try:
        subject = f"[Folo 归档失败] {today} 共 {len(failures)} 项"
        body = notify.format_failure_report(today, failures, step_times)
        ok, err = notify.send_failure_mail(cfg, subject, body)
        if ok:
            LOG.append(f"✉️ 失败通知邮件已发送: {', '.join(cfg.get('to', []))}")
        else:
            LOG.append(f"✗ 失败通知邮件发送失败: {err}")
    except Exception as exc:  # noqa: BLE001
        LOG.append(f"⚠️ 发送失败通知邮件时出错: {exc}")


_DATE_RE = re.compile(r"^\d{4}年\d{2}月\d{2}日$")


def _valid_date(text):
    if not isinstance(text, str) or not _DATE_RE.match(text):
        return False
    try:
        datetime.strptime(text, "%Y年%m月%d日")
        return True
    except ValueError:
        return False


# =============================================================================
# 已读状态（按天，持久化 result/read_state.json）
# =============================================================================
READ_STATE_PATH = OUTPUT_BASE_DIR / "read_state.json"
_READ_LOCK = threading.Lock()


def load_read_state():
    """读取按天的已读状态，返回 {日期: True}

    文件不存在 / 损坏 / 结构不对时一律视为「全部未读」，绝不抛异常。
    """
    try:
        with open(READ_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: True for key, value in data.items() if isinstance(key, str) and value}


def _save_read_state(state):
    """原子写入：先写临时文件再 replace，避免出现半截 JSON"""
    READ_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = READ_STATE_PATH.with_name(READ_STATE_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, READ_STATE_PATH)


def set_read_state(date_key, read):
    """把某天标记为已读（read=True）或未读（read=False），返回 (ok, error)"""
    if not _valid_date(date_key):
        return False, "日期格式无效，应为 YYYY年MM月DD日"
    with _READ_LOCK:
        state = load_read_state()
        if read:
            state[date_key] = True
        else:
            state.pop(date_key, None)
        try:
            _save_read_state(state)
        except OSError as exc:
            return False, f"写入已读状态失败: {exc}"
    # 已读状态变了，热力图缓存立即失效，下一次拉取就能看到新状态
    with _HEAT_LOCK:
        _HEAT_CACHE["data"] = None
        _HEAT_CACHE["at"] = 0.0
    return True, None


def _normalize_steps(raw):
    valid = {num for num, _, _ in STEPS}
    out = []
    for item in raw or []:
        try:
            num = int(item)
        except (TypeError, ValueError):
            continue
        if num in valid and num not in out:
            out.append(num)
    return sorted(out)


def start_run(steps, date_str):
    """校验并启动后台归档线程，返回 (ok, error)"""
    steps = _normalize_steps(steps)
    if not steps:
        return False, "请至少选择一个步骤"
    if not _valid_date(date_str):
        return False, "日期格式无效，应为 YYYY年MM月DD日"

    with _STATE_LOCK:
        if _STATE["running"]:
            return False, "已有任务正在运行，请等待完成或先停止"
        _STATE["running"] = True
        _STATE["should_stop"] = False
        _STATE["progress"] = 0
        _STATE["progress_text"] = "准备中…"
        _STATE["step_times"] = {}

    LOG.append(f"[WebUI] 开始执行：步骤 {steps}，日期 {date_str}")
    threading.Thread(target=_run_worker, args=(steps, date_str), daemon=True).start()
    return True, None


def request_stop():
    with _STATE_LOCK:
        if not _STATE["running"]:
            return False
        _STATE["should_stop"] = True
    LOG.append("⚠️ 已请求停止，将在当前步骤结束后退出")
    return True


def state_payload():
    with _STATE_LOCK:
        running = bool(_STATE["running"])
        progress = int(_STATE["progress"])
        progress_text = str(_STATE["progress_text"])
        step_times = dict(_STATE["step_times"])
    with _SCHED_LOCK:
        schedule = _SCHED_CACHE.get("data")
    return {
        "running": running,
        "progress": progress,
        "progress_text": progress_text,
        "steps": [{"num": num, "name": name, "desc": desc} for num, name, desc in STEPS],
        "step_times": step_times,
        "last_log_index": LOG.next_index() - 1,
        "next_log_index": LOG.next_index(),
        "today": datetime.now().strftime("%Y年%m月%d日"),
        "schedule": schedule,
    }


# =============================================================================
# 热力图（合并 result/ 与 result/归档/）
# =============================================================================
_HEAT_LOCK = threading.Lock()
_HEAT_CACHE = {"at": 0.0, "data": None}

_DATE_DIR_RE = re.compile(r"^(\d{4})年(\d{2})月(\d{2})日$")


def _parse_date_key(key):
    m = _DATE_DIR_RE.match(key) if isinstance(key, str) else None
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def get_heatmap(force=False):
    """扫描两个根目录并合并篇数，返回 {counts, total, recent30}"""
    now = time.time()
    with _HEAT_LOCK:
        cached = _HEAT_CACHE.get("data")
        if not force and cached is not None and now - _HEAT_CACHE.get("at", 0.0) < HEATMAP_TTL:
            return cached

    counts = {}
    for base in (OUTPUT_BASE_DIR, OUTPUT_BASE_DIR / "归档"):
        try:
            part = heatmap.scan_daily_counts(base) or {}
        except Exception:  # noqa: BLE001 - 单个目录扫描失败不影响另一个
            continue
        for key, value in part.items():
            try:
                counts[key] = counts.get(key, 0) + int(value)
            except (TypeError, ValueError):
                continue

    today = date.today()
    cutoff = today - timedelta(days=29)
    recent30 = 0
    days = {}
    read_state = load_read_state()
    for key, value in counts.items():
        day = _parse_date_key(key)
        if day is not None and cutoff <= day <= today:
            recent30 += value
        days[key] = {"count": value, "read": key in read_state}

    read_days = sum(1 for key in counts if key in read_state)
    data = {
        "days": days,
        "total": sum(counts.values()),
        "recent30": recent30,
        "read_days": read_days,
    }
    with _HEAT_LOCK:
        _HEAT_CACHE["data"] = data
        _HEAT_CACHE["at"] = time.time()
    return data


# =============================================================================
# 归档产物静态托管（GET /archive/<日期>/<相对路径>）
# =============================================================================
# 归档产物位于 result/ 与更早的历史目录 result/归档/，serve 时依次尝试
_ARCHIVE_ROOTS = (OUTPUT_BASE_DIR, OUTPUT_BASE_DIR / "归档")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _content_type_for(suffix):
    return _CONTENT_TYPES.get(suffix.lower(), "application/octet-stream")


def _resolve_archive_file(rel_path):
    """把 /archive/ 后的相对路径解析成归档根目录内的真实文件，越界返回 None

    防路径穿越：拒绝 NUL、绝对路径（前导 / 或盘符）、任何 `..` 片段；
    再对解析后的绝对路径做 resolve()，确认仍位于某个允许的根目录之下
    （is_relative_to 同时挡住符号链接逃逸）。只接受普通文件，不列目录。
    """
    if not isinstance(rel_path, str) or not rel_path or "\x00" in rel_path:
        return None

    cleaned = rel_path.replace("\\", "/")
    if cleaned.startswith("/") or re.match(r"^[A-Za-z]:", cleaned):
        return None
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    if not parts:
        return None

    for root in _ARCHIVE_ROOTS:
        try:
            root_resolved = root.resolve()
            candidate = (root_resolved / Path(*parts)).resolve()
        except (OSError, ValueError):
            continue
        if not candidate.is_relative_to(root_resolved):
            continue
        if candidate.is_file():
            return candidate
    return None


# =============================================================================
# 每日定时（内建调度，与独立调度器 scheduler.py 通过命名互斥量互斥）
# =============================================================================
_SCHED_LOCK = threading.Lock()
_SCHED_CACHE = {"at": 0.0, "data": None}

# 与 scheduler.py 的 MUTEX_NAME 一致，两者靠它互斥
SCHEDULER_MUTEX = "FoloArchiveScheduler"
SCHEDULE_CHECK_INTERVAL = 20               # 秒，调度线程轮询间隔
_SCHEDULE_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")

# 调度归属：None（未初始化）/ "webui"（本进程）/ "external"（外部调度器）/ "disabled"
_SCHED_OWNER = None
_SCHED_LAST_RUN_DATE = None                # 进程内已触发的日期（与 scheduler.py 的兜底同理）
_SCHED_MUTEX_HANDLE = None                 # 本进程持有的互斥量句柄，存活期间不关闭


def _acquire_schedule_mutex():
    """尝试获取调度互斥量：本进程拿到返回句柄，已被占用返回 None

    句柄需由调用方长期持有（不要 CloseHandle），外部 scheduler.py 启动时才会
    检测到「已有实例」而退出。非 Windows 或调用失败时返回 True 占位，
    退化为「不互斥」，不阻塞启动。
    """
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, SCHEDULER_MUTEX)
        if not handle:
            return True
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            return None
        return handle
    except Exception:  # noqa: BLE001 - 非 Windows 或调用失败时不阻塞
        return True


def _scheduler_running():
    """探测互斥量是否已被外部 scheduler.py 持有

    本进程若已持有该互斥量，这里同样会返回 True，所以调用方要先判断
    _SCHED_OWNER == "webui"。CreateMutexW 只做探测，用完立即 CloseHandle，
    不持有句柄。非 Windows 或调用失败时安全返回 False。
    """
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, SCHEDULER_MUTEX)
        if not handle:
            return False
        try:
            return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - 非 Windows 或调用失败时按未运行处理
        return False


def _scheduler_config():
    """读取 config.json 的 schedule 段，返回 (enabled, time)

    任何异常或字段非法都回退默认值（enabled=True、time="08:00"）。
    不能用 utils.load_config()：它缺 api_key 等字段会 sys.exit(1)。
    """
    enabled = True
    at = "08:00"
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            sched = data.get("schedule") if isinstance(data, dict) else None
            if isinstance(sched, dict):
                if isinstance(sched.get("enabled"), bool):
                    enabled = sched["enabled"]
                value = sched.get("time")
                if isinstance(value, str) and _SCHEDULE_TIME_RE.match(value.strip()):
                    at = value.strip()
    except (OSError, ValueError):
        pass
    return enabled, at


def _target_today(at):
    """今天的设定时刻（datetime）；解析失败时回退到当天 23:59（即当天不再触发）"""
    try:
        hour, minute = (int(x) for x in at.split(":"))
        return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    except (ValueError, AttributeError):
        return datetime.now().replace(hour=23, minute=59, second=0, microsecond=0)


def _list_json_path(today):
    """当天文章列表 JSON 的路径（步骤 1 的产物，与 scheduler.py 的判据一致）"""
    return TEMP_DIR / f"「{today}」.json"


def _schedule_worker():
    """内建调度线程：每 20 秒检查一次，到点且当天未跑则触发一次完整归档

    触发直接复用 start_run()（不另起子进程、不重复实现流程）；只有它返回 ok
    才记下当天已跑，返回失败（如已有任务在跑）保持未记，下个周期自然重试。
    任何异常都只记日志，绝不让线程退出。
    """
    global _SCHED_LAST_RUN_DATE
    while True:
        try:
            enabled, at = _scheduler_config()
            today = datetime.now().strftime("%Y年%m月%d日")
            if enabled and _SCHED_LAST_RUN_DATE != today and datetime.now() >= _target_today(at):
                if _list_json_path(today).exists():
                    _SCHED_LAST_RUN_DATE = today
                    LOG.append(f"[WebUI] 定时：当天已归档过（存在 {_list_json_path(today).name}），跳过")
                else:
                    ok, error = start_run([1, 2, 3, 4], today)
                    if ok:
                        _SCHED_LAST_RUN_DATE = today
                        LOG.append(f"[WebUI] 定时（{at}）触发：开始执行当日归档 {today}")
                    else:
                        LOG.append(f"[WebUI] 定时触发未启动：{error}")
        except Exception as exc:  # noqa: BLE001 - 循环内异常不允许终止调度
            LOG.append(f"[WebUI] 定时循环异常: {type(exc).__name__}: {exc}")
        time.sleep(SCHEDULE_CHECK_INTERVAL)


def start_scheduler():
    """启动内建定时（默认行为）：取到互斥量则起调度线程，被占用则只记日志

    返回最终的调度归属字符串，便于日志/展示。
    """
    global _SCHED_OWNER, _SCHED_MUTEX_HANDLE
    enabled, at = _scheduler_config()
    if not enabled:
        _SCHED_OWNER = "disabled"
        LOG.append("[WebUI] config.json 的 schedule.enabled=false，内建定时未启用")
        return _SCHED_OWNER

    handle = _acquire_schedule_mutex()
    if handle is None:
        _SCHED_OWNER = "external"
        LOG.append("[WebUI] 检测到外部调度器（scheduler.py），本进程不再重复调度")
        return _SCHED_OWNER

    _SCHED_MUTEX_HANDLE = handle       # 进程存活期间一直持有，保持与外部调度器互斥
    _SCHED_OWNER = "webui"
    LOG.append(f"[WebUI] 内建定时已启用：每天 {at} 自动执行一次完整归档（关闭网页版即停止定时）")
    threading.Thread(target=_schedule_worker, daemon=True).start()
    return _SCHED_OWNER


def disable_scheduler():
    """用 --no-schedule 显式关闭内建定时"""
    global _SCHED_OWNER
    _SCHED_OWNER = "disabled"
    LOG.append("[WebUI] 已用 --no-schedule 关闭内建定时调度")


def _schedule_owner():
    """当前调度归属：webui（本进程）/ external（外部调度器）/ disabled"""
    if _SCHED_OWNER == "webui":
        return "webui"
    if _scheduler_running():
        return "external"
    return "disabled"


_SCHEDULE_NOTES = {
    "webui": "定时由本网页版进程负责；关闭网页版即停止定时。"
             "也可改用 python src/scheduler.py 独立运行（两者互斥，不会重复）。",
    "external": "已检测到外部调度器（scheduler.py），本进程不再重复调度。",
    "disabled": "内建定时已关闭（--no-schedule 或 config.json 里 schedule.enabled=false）。",
}


def get_schedule(force=False):
    """返回调度状态：owner / running / enabled / time / note"""
    now = time.time()
    with _SCHED_LOCK:
        cached = _SCHED_CACHE.get("data")
        if not force and cached is not None and now - _SCHED_CACHE.get("at", 0.0) < SCHEDULE_TTL:
            return cached

    enabled, at = _scheduler_config()
    owner = _schedule_owner()
    result = {
        "owner": owner,
        "running": owner in ("webui", "external"),
        "enabled": enabled,
        "time": at,
        "note": _SCHEDULE_NOTES.get(owner, _SCHEDULE_NOTES["disabled"]),
    }

    with _SCHED_LOCK:
        _SCHED_CACHE["data"] = result
        _SCHED_CACHE["at"] = time.time()
    return result


# =============================================================================
# HTTP 服务
# =============================================================================
class Handler(BaseHTTPRequestHandler):
    server_version = "FoloWebUI/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # 静音：避免 HTTP 访问日志混入归档日志缓冲区
        pass

    # ---------------------------------------------------------- 响应工具
    def _send_bytes(self, data, content_type, status=200):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, UnicodeDecodeError, OSError):
            return {}

    # -------------------------------------------------------- 归档静态文件
    def _serve_archive(self, raw_path):
        """GET /archive/<日期>/<相对路径>：只读返回归档产物，越界一律 404"""
        rel_encoded = raw_path[len("/archive/"):]
        try:
            rel_path = unquote(rel_encoded)
        except Exception:  # noqa: BLE001 - 解码失败按原样处理，后续仍会被拒绝
            rel_path = rel_encoded
        target = _resolve_archive_file(rel_path)
        if target is None:
            return self._send_json({"ok": False, "error": "未找到归档文件"}, status=404)
        try:
            data = target.read_bytes()
        except OSError:
            return self._send_json({"ok": False, "error": "读取归档文件失败"}, status=404)
        return self._send_bytes(data, _content_type_for(target.suffix))

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            return self._send_bytes(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if path.startswith("/archive/"):
            return self._serve_archive(path)
        if path == "/api/state":
            return self._send_json(state_payload())
        if path == "/api/logs":
            query = parse_qs(parsed.query)
            try:
                since = int(query.get("since", ["0"])[0])
            except (TypeError, ValueError):
                since = 0
            lines, next_index = LOG.lines_since(max(since, 0))
            return self._send_json({"lines": lines, "next": next_index})
        if path == "/api/heatmap":
            return self._send_json(get_heatmap())
        if path == "/api/schedule":
            force = parse_qs(parsed.query).get("refresh", ["0"])[0] in ("1", "true", "yes")
            return self._send_json(get_schedule(force=force))
        return self._send_json({"ok": False, "error": "未知路径"}, status=404)

    # --------------------------------------------------------------- POST
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json()

        if path == "/api/run":
            ok, error = start_run(body.get("steps"), body.get("date"))
            return self._send_json({"ok": ok, "error": error})
        if path == "/api/stop":
            return self._send_json({"ok": request_stop()})
        if path == "/api/read":
            read = body.get("read")
            if not isinstance(read, bool):
                return self._send_json({"ok": False, "error": "read 字段应为布尔值"})
            ok, error = set_read_state(body.get("date"), read)
            return self._send_json({"ok": ok, "error": error})
        return self._send_json({"ok": False, "error": "未知路径"}, status=404)


# =============================================================================
# 配置与启动
# =============================================================================
def _load_web_config():
    """读取 config.json 的 web 段（可选），任何异常都安全回退到空 dict

    不能用 utils.load_config()：它缺 api_key 等字段会 sys.exit(1)。
    """
    try:
        if not CONFIG_PATH.exists():
            return {}
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    web = data.get("web")
    if not isinstance(web, dict):
        return {}

    result = {}
    host = web.get("host")
    if isinstance(host, str) and host.strip():
        result["host"] = host.strip()
    try:
        port = int(web.get("port"))
        if 1 <= port <= 65535:
            result["port"] = port
    except (TypeError, ValueError):
        pass
    return result


def parse_args():
    parser = argparse.ArgumentParser(
        description="Folo 文章归档 —— 本地网页版界面（纯标准库）",
    )
    parser.add_argument("--host", default=None, help="监听地址（默认 127.0.0.1，可被 config.json 的 web.host 覆盖默认值）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认 8765）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--no-schedule", action="store_true",
                        help="不启动内建定时调度（默认启动；关闭后可改用 python src/scheduler.py 独立调度）")
    return parser.parse_args()


def main():
    args = parse_args()
    web_cfg = _load_web_config()

    host = args.host or web_cfg.get("host") or DEFAULT_HOST
    port = args.port or web_cfg.get("port") or DEFAULT_PORT
    if not (1 <= port <= 65535):
        print(f"[!] 端口无效: {port}，回退到 {DEFAULT_PORT}")
        port = DEFAULT_PORT

    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"[!] 无法监听 {host}:{port} —— {exc}")
        print("    端口可能已被占用，可换一个：python src/webui.py --port 8766")
        return 1
    httpd.daemon_threads = True

    # 重定向 stdout/stderr 到日志缓冲区，并镜像一份到原始终端
    origin_stdout = sys.stdout
    LOG.set_mirror(origin_stdout)
    sys.stdout = LOG
    sys.stderr = LOG

    display_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    url = f"http://{display_host}:{port}/"
    LOG.append("=" * 60)
    LOG.append(f"Folo 网页版界面已启动：{url}")
    LOG.append("归档请用页面上的按钮；按 Ctrl+C 退出服务")
    LOG.append("=" * 60)

    # 内建定时：默认开启；取不到互斥量说明外部调度器在跑，本进程不重复调度
    if args.no_schedule:
        disable_scheduler()
    else:
        start_scheduler()

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - 打不开浏览器不影响服务
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOG.append("")
        LOG.append("[WebUI] 收到中断，正在退出…")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
