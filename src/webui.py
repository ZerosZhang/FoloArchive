#!/usr/bin/env python3
"""
Folo 文章归档 —— 本地网页版界面（纯标准库实现）

用途：项目唯一的图形界面。用浏览器访问一个本地 HTTP 服务完成归档工作：
手动归档、实时日志、进度条、耗时统计、失败邮件、归档热力图、常驻调度器
（scheduler.py）的状态查看。

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
"""

import argparse
import ctypes
import io
import json
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
from urllib.parse import parse_qs, urlparse

# 脚本所在目录（src/）与功能模块目录（src/core/）
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR / "core"))

# Windows 终端编码修复（必须在导入其他脚本之前执行）
from utils import fix_encoding, CONFIG_PATH, OUTPUT_BASE_DIR

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
    for key, value in counts.items():
        day = _parse_date_key(key)
        if day is not None and cutoff <= day <= today:
            recent30 += value

    data = {"counts": counts, "total": sum(counts.values()), "recent30": recent30}
    with _HEAT_LOCK:
        _HEAT_CACHE["data"] = data
        _HEAT_CACHE["at"] = time.time()
    return data


# =============================================================================
# 常驻调度器状态（scheduler.py，命名互斥量检测）
# =============================================================================
_SCHED_LOCK = threading.Lock()
_SCHED_CACHE = {"at": 0.0, "data": None}

# scheduler.py 里的 LOG_PATH / MUTEX_NAME，保持一致
SCHEDULER_LOG_PATH = OUTPUT_BASE_DIR / "scheduler.log"
SCHEDULER_MUTEX = "FoloArchiveScheduler"
SCHEDULER_LOG_TAIL = 15
_SCHEDULE_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _scheduler_running():
    """用命名互斥量检测 scheduler.py 是否已在运行

    CreateMutexW 若发现互斥量已存在，会把 GetLastError 置为 183
    （ERROR_ALREADY_EXISTS），据此判断已有常驻实例。检测完立即 CloseHandle，
    避免持有句柄挡住真正的调度器启动。非 Windows 或调用失败时安全返回 False。
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


def _schedule_log_tail(max_lines=SCHEDULER_LOG_TAIL):
    """读 result/scheduler.log 的最后若干行；文件不存在或读取失败返回空列表"""
    try:
        lines = SCHEDULER_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-max_lines:]


def get_schedule(force=False):
    """检测常驻调度器状态，返回展示用 dict"""
    now = time.time()
    with _SCHED_LOCK:
        cached = _SCHED_CACHE.get("data")
        if not force and cached is not None and now - _SCHED_CACHE.get("at", 0.0) < SCHEDULE_TTL:
            return cached

    enabled, at = _scheduler_config()
    result = {
        "running": _scheduler_running(),
        "time": at,
        "enabled": enabled,
        "log_tail": _schedule_log_tail(),
        "note": "定时由常驻调度器负责（后台运行.bat / scheduler.py），不依赖本页面是否打开",
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

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            return self._send_bytes(PAGE.encode("utf-8"), "text/html; charset=utf-8")
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
