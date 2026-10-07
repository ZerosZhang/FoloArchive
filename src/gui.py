#!/usr/bin/env python3
"""
Folo 文章归档 —— 桌面小窗口（GUI）

一个极简的「服务管家」窗口，只有三个按钮：

    今日归档        触发当天完整归档（四步全跑）
    打开 web 页面    用浏览器打开网页版界面
    退出服务        停止本地服务并关闭窗口

**关闭窗口（X 或 Esc）不会退出**，而是把窗口收进系统托盘继续后台运行；
要真正退出，右键托盘图标选「退出」，或点窗口里的「退出服务」按钮。
托盘图标用 src/trayicon.py（纯 ctypes 调 Shell_NotifyIcon），双击图标可唤回窗口。

本窗口**不展示日志**：归档进度、实时日志、耗时统计、热力图都在网页版界面里看。

服务跑在**本进程内**（webui.start_service + serve_in_background），因此按钮能
直接调用 webui.start_run() / state_payload()，不需要子进程与进程间通信。

运行：
    pythonw src/gui.py        # 无控制台窗口（双击 启动Folo.bat 走这条）
    python  src/gui.py        # 带控制台，便于观察启动异常
"""

import argparse
import ctypes
import queue
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

# 脚本所在目录（src/）与功能模块目录（src/core/）
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR / "core"))

TITLE = "Folo 归档"


def _fatal(message):
    """启动期致命错误：pythonw 下没有控制台，用系统弹窗把话讲清楚"""
    try:
        sys.stderr.write(message + "\n")
    except Exception:  # noqa: BLE001 - pythonw 下 stderr 可能不可用
        pass
    try:
        ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)
    except Exception:  # noqa: BLE001 - 非 Windows 平台弹窗不可用
        pass


try:
    import tkinter as tk
    from tkinter import messagebox
except ImportError:
    _fatal(
        "当前 Python 缺少 tkinter，无法显示桌面窗口。\n\n"
        "修复方法：重新运行 Python 安装程序 → Modify → 勾选\n"
        "「tcl/tk and IDLE」→ 安装完成后重建虚拟环境：\n\n"
        "    python -m venv .venv\n"
        "    .venv\\Scripts\\pip install -r requirements.txt\n\n"
        "也可以改用纯网页版：双击 启动Web界面.bat"
    )
    raise SystemExit(1)

from utils import fix_encoding  # noqa: E402

fix_encoding()

import webui  # noqa: E402

try:  # 托盘不可用时窗口仍应能用，只是关窗直接退出
    from trayicon import TrayIcon  # noqa: E402
except Exception:  # noqa: BLE001
    TrayIcon = None


class FoloWindow:
    """三个按钮的极简窗口：只管住「归档 / 打开页面 / 退出」三件事

    点窗口关闭按钮（或按 Esc）**不退出**，而是把窗口收进系统托盘继续后台跑；
    真正退出要走托盘图标右键菜单的「退出」，或窗口里的「退出服务」按钮。
    """

    def __init__(self, host=None, port=None, schedule=True):
        self.host = host
        self.port = port
        self.schedule = schedule
        self.service = None

        # 托盘回调发生在托盘线程，用队列搬回界面线程（root.after 轮询）
        self.tray = None
        self._tray_commands = queue.Queue()
        self.hidden = False

        self.root = tk.Tk()
        self.root.title(TITLE)
        self.root.resizable(False, False)
        self._build_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.root.bind("<Escape>", lambda _event: self.hide_to_tray())
        self._start_service()
        self._start_tray()
        self.root.after(120, self._poll_tray)

    # ------------------------------------------------------------ 界面搭建
    def _build_widgets(self):
        frame = tk.Frame(self.root, padx=18, pady=16)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame, text="Folo 归档",
            font=("Microsoft YaHei UI", 13, "bold"), anchor="w",
        ).pack(anchor="w")

        self.status = tk.Label(
            frame, text="正在启动服务…", fg="#444", justify="left",
            anchor="w", wraplength=280, font=("Microsoft YaHei UI", 9),
        )
        self.status.pack(fill="x", pady=(6, 12))

        button_font = ("Microsoft YaHei UI", 10)
        self.archive_button = tk.Button(
            frame, text="今日归档", width=26, height=2, font=button_font,
            command=self.archive_today,
        )
        self.archive_button.pack(pady=3)

        self.page_button = tk.Button(
            frame, text="打开 web 页面", width=26, height=2, font=button_font,
            command=self.open_page,
        )
        self.page_button.pack(pady=3)

        tk.Button(
            frame, text="退出服务", width=26, height=2, font=button_font,
            command=self.exit_service,
        ).pack(pady=3)

    def _set_status(self, text, error=False):
        self.status.configure(text=text, fg="#b3261e" if error else "#444")

    def _report(self, text, error=False):
        """更新状态行；窗口收在托盘里时同时弹一条气泡，免得看不到反馈"""
        self._set_status(text, error=error)
        if self.hidden and self.tray is not None:
            self.tray.notify(TITLE, text)
        return text

    # -------------------------------------------------------------- 服务
    def _start_service(self):
        try:
            self.service = webui.start_service(self.host, self.port, schedule=self.schedule)
        except OSError as exc:
            self.service = None
            self._set_status("服务未启动", error=True)
            messagebox.showerror(
                TITLE,
                f"无法启动本地服务：\n{exc}\n\n"
                "端口可能已被占用。可用 --port 8766 换一个端口，"
                "或先关掉已在运行的实例。",
            )
            return

        self.service.serve_in_background()
        self._set_status(f"服务已启动：\n{self.service.url}")

    # -------------------------------------------------------------- 托盘
    def _start_tray(self):
        """加托盘图标；失败只记状态行，不能让窗口起不来"""
        if TrayIcon is None:
            self._set_status("托盘不可用：关闭窗口将直接退出服务", error=True)
            return
        try:
            self.tray = TrayIcon(
                tooltip=TITLE,
                menu=[("show", "打开主窗口"), ("archive", "今日归档"),
                      None, ("quit", "退出")],
                on_command=self._tray_commands.put,
                on_activate=lambda: self._tray_commands.put("show"),
            ).start()
        except Exception as exc:  # noqa: BLE001 - 托盘失败不影响主流程
            self.tray = None
            self._set_status(f"托盘图标添加失败（关闭窗口将直接退出）：{exc}", error=True)

    def _poll_tray(self):
        """界面线程轮询托盘回调队列（托盘线程只负责投递）"""
        try:
            while True:
                command = self._tray_commands.get_nowait()
                self._handle_tray_command(command)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_tray)

    def _handle_tray_command(self, command):
        if command == "show":
            self.show_window()
        elif command == "archive":
            self.archive_today()
        elif command == "quit":
            self.exit_service()

    def hide_to_tray(self):
        """点关闭按钮 / 按 Esc：收进托盘继续后台运行，而不是退出"""
        if self.tray is None:
            messagebox.showinfo(
                TITLE,
                "托盘图标不可用，窗口无法收进托盘。\n"
                "请用窗口里的「退出服务」按钮退出。",
            )
            return
        self.root.withdraw()
        self.hidden = True
        self.tray.notify(TITLE, "已最小化到系统托盘。右键托盘图标可退出。")

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.hidden = False

    # -------------------------------------------------------------- 按钮
    def archive_today(self):
        """触发当天完整归档；真正的执行与日志都在网页版里"""
        if self.service is None:
            return self._report("服务未启动，无法归档", error=True)

        today = datetime.now().strftime("%Y年%m月%d日")
        ok, error = webui.start_run([1, 2, 3, 4], today)
        if ok:
            return self._report(f"已开始归档 {today}，进度与日志请看 web 页面")
        return self._report(f"未能开始归档：{error}", error=True)

    def open_page(self):
        if self.service is None:
            return self._report("服务未启动，无法打开页面", error=True)
        try:
            webbrowser.open(self.service.url)
        except Exception as exc:  # noqa: BLE001 - 打不开浏览器不影响服务
            return self._report(f"无法打开浏览器：{exc}", error=True)
        return self._report(f"已在浏览器打开 {self.service.url}")

    def exit_service(self):
        """真正退出：停服务、撤托盘图标、关窗；归档进行中时先确认"""
        if self.service is not None and webui.state_payload().get("running"):
            if not messagebox.askokcancel(
                TITLE, "归档正在运行，确定要退出吗？\n退出会中断本次归档。"
            ):
                return
        if self.tray is not None:
            self.tray.stop()
            self.tray = None
        if self.service is not None:
            self.service.stop()
            self.service = None
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Folo 文章归档 —— 桌面小窗口",
    )
    parser.add_argument("--host", default=None,
                        help="监听地址（默认 127.0.0.1，可被 config.json 的 web.host 覆盖默认值）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认 8765）")
    parser.add_argument("--no-schedule", action="store_true",
                        help="不启动内建每日定时（改由 python src/scheduler.py 独立调度）")
    return parser.parse_args()


def main():
    args = parse_args()
    FoloWindow(args.host, args.port, schedule=not args.no_schedule).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
