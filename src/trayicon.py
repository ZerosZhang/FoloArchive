#!/usr/bin/env python3
"""
Windows 系统托盘图标（纯 ctypes 调 Shell_NotifyIcon，零第三方依赖）

给桌面小窗口（src/gui.py）用：点窗口关闭按钮时把窗口收进托盘继续后台跑，
右键托盘图标弹出菜单，选「退出」才真正结束。

设计要点：
- **独立的托盘线程 + 自己的消息循环**。不借用 tkinter 的消息泵，因此不依赖
  Tk 是否把消息派发到我们这个窗口；托盘的一切都不在主线程之外乱跑。
- 菜单项被点击时在**托盘线程**里回调 `on_command(command_id)`，所以调用方
  要把结果搬回界面线程（gui.py 用 queue + root.after 轮询）。
- `notify()` 可在任意线程调用：它只是往托盘窗口 Post 一条自定义消息，
  真正的气泡提示在托盘线程里发。
- 非 Windows 上导入不报错，`TrayIcon.start()` 会抛 RuntimeError。
"""

import ctypes
import threading
from ctypes import wintypes

# =============================================================================
# Win32 常量与结构
# =============================================================================

WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_APP = 0x8000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_INFO = 0x00000001

IDI_APPLICATION = 32512
IDC_ARROW = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800

TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080

ERROR_CLASS_ALREADY_EXISTS = 1410

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    """Shell_NotifyIcon 的载荷结构（Vista+ 的完整版本）

    字段顺序必须与 winshellapi.h 完全一致——顺序错了 Shell 会静默失败。
    """

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", wintypes.BYTE * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


def _bind_functions():
    """声明用到的 Win32 函数签名；失败（非 Windows）返回 None"""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (OSError, AttributeError):
        return None

    user32.RegisterClassW.restype = ctypes.c_ushort
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]

    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
    ]

    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]

    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]

    user32.GetMessageW.restype = ctypes.c_int
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]

    user32.LoadIconW.restype = wintypes.HICON
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user32.LoadImageW.restype = wintypes.HICON
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                  wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  wintypes.UINT]
    user32.LoadCursorW.restype = wintypes.HANDLE
    user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]

    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT,
                                   ctypes.c_size_t, wintypes.LPCWSTR]
    user32.TrackPopupMenu.restype = wintypes.UINT
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      wintypes.HWND, ctypes.c_void_p]
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]

    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                          ctypes.POINTER(NOTIFYICONDATAW)]

    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

    return user32, shell32, kernel32


_WIN = _bind_functions()
if _WIN:
    _user32, _shell32, _kernel32 = _WIN

# 供自检/调试：托盘图标是否可用
AVAILABLE = _WIN is not None


class TrayIcon:
    """系统托盘图标

    参数：
        tooltip:     鼠标悬停提示
        menu:        [(command_id, label) | None, ...]，None 表示一条分隔线
        on_command:  菜单项被选中时回调 on_command(command_id)（在托盘线程里）
        on_activate: 双击图标时回调（在托盘线程里），可为 None
        icon_path:   可选 .ico 路径；不传则用系统默认应用图标
    """

    _CLASS_NAME = "FoloArchiveTrayWnd"
    _WM_TRAY = WM_APP + 1
    _WM_NOTIFY = WM_APP + 2
    _ICON_ID = 1
    _MENU_ID_BASE = 1000

    def __init__(self, tooltip, menu, on_command, on_activate=None, icon_path=None):
        self.tooltip = tooltip
        self.menu = list(menu)
        self.on_command = on_command
        self.on_activate = on_activate
        self.icon_path = icon_path

        self._hwnd = None
        self._thread = None
        self._ready = threading.Event()
        self._error = None
        self._pending_notice = None
        self._icon_added = False
        # 回调函数必须由实例长期持有：被 GC 回收后 Windows 会调用到野指针
        self._wndproc = WNDPROC(self._handle_message)

    # ------------------------------------------------------------ 生命周期
    def start(self, timeout=5):
        """起托盘线程并注册图标；失败抛 RuntimeError"""
        if not AVAILABLE:
            raise RuntimeError("系统托盘目前只支持 Windows")
        self._thread = threading.Thread(target=self._run, name="FoloTray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        if self._error is not None:
            raise self._error
        if self._hwnd is None:
            raise RuntimeError("托盘窗口创建超时")
        return self

    def stop(self, timeout=3):
        """移除托盘图标并结束托盘线程；可重复调用"""
        if self._hwnd is not None and AVAILABLE:
            _user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        self._hwnd = None

    def notify(self, title, text):
        """弹一次气泡提示；任意线程可调，窗口已在时无副作用"""
        if self._hwnd is None or not AVAILABLE:
            return
        self._pending_notice = (title, text)
        _user32.PostMessageW(self._hwnd, self._WM_NOTIFY, 0, 0)

    # ------------------------------------------------------------ 托盘线程
    def _run(self):
        try:
            hinst = _kernel32.GetModuleHandleW(None)
            self._register_class(hinst)
            self._hwnd = _user32.CreateWindowExW(
                0, self._CLASS_NAME, self.tooltip, 0, 0, 0, 0, 0,
                None, None, hinst, None,
            )
            if not self._hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            if not self._shell_notify(NIM_ADD, NIF_MESSAGE | NIF_ICON | NIF_TIP):
                raise RuntimeError("Shell_NotifyIcon(NIM_ADD) 失败，托盘图标未能添加")
            self._icon_added = True
            self._ready.set()

            msg = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:  # noqa: BLE001 - 统一交给 start() 抛出
            self._error = exc
        finally:
            self._ready.set()

    def _register_class(self, hinst):
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.hCursor = _user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
        wc.lpszClassName = self._CLASS_NAME
        if not _user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            # 同进程内第二次创建（类名已注册）不算错误
            if err != ERROR_CLASS_ALREADY_EXISTS:
                raise ctypes.WinError(err)

    def _icon_handle(self):
        if self.icon_path:
            hicon = _user32.LoadImageW(None, str(self.icon_path), IMAGE_ICON, 0, 0,
                                       LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if hicon:
                return hicon
        return _user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))

    def _shell_notify(self, action, flags, title="", text=""):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = self._ICON_ID
        nid.uFlags = flags
        nid.uCallbackMessage = self._WM_TRAY
        nid.hIcon = self._icon_handle()
        nid.szTip = self.tooltip[:127]
        if title:
            nid.szInfoTitle = title[:63]
            nid.dwInfoFlags = NIIF_INFO
        if text:
            nid.szInfo = text[:255]
        return bool(_shell32.Shell_NotifyIconW(action, ctypes.byref(nid)))

    # --------------------------------------------------------- 消息处理
    def _handle_message(self, hwnd, msg, wparam, lparam):
        if msg == self._WM_TRAY:
            self._on_tray_message(lparam)
            return 0
        if msg == self._WM_NOTIFY:
            notice, self._pending_notice = self._pending_notice, None
            if notice:
                self._shell_notify(NIM_MODIFY, NIF_INFO | NIF_ICON | NIF_TIP, *notice)
            return 0
        if msg == WM_CLOSE:
            _user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            if self._icon_added:
                self._shell_notify(NIM_DELETE, 0)
                self._icon_added = False
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_tray_message(self, event):
        if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
            self._show_menu()
            return
        if event == WM_LBUTTONDBLCLK and self.on_activate:
            self._safe_call(self.on_activate)

    def _show_menu(self):
        hmenu, commands = self._build_menu()
        if not hmenu:
            return
        try:
            point = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(point))
            # 不置前台的话，点菜单外面菜单不会消失（Win32 经典坑）
            _user32.SetForegroundWindow(self._hwnd)
            picked = _user32.TrackPopupMenu(
                hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
                point.x, point.y, 0, self._hwnd, None,
            )
            _user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        finally:
            _user32.DestroyMenu(hmenu)
        command = commands.get(picked)
        if command is not None:
            self._safe_call(self.on_command, command)

    def _build_menu(self):
        """建好弹出菜单，返回 (hmenu, {菜单项 id: 业务 command_id})"""
        hmenu = _user32.CreatePopupMenu()
        if not hmenu:
            return None, {}
        commands = {}
        for index, item in enumerate(self.menu):
            if item is None:
                _user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                continue
            command_id, label = item
            menu_id = self._MENU_ID_BASE + index
            commands[menu_id] = command_id
            _user32.AppendMenuW(hmenu, MF_STRING, menu_id, label)
        return hmenu, commands

    @staticmethod
    def _safe_call(func, *args):
        """托盘线程里的回调绝不允许把线程搞死"""
        try:
            func(*args)
        except Exception:  # noqa: BLE001
            pass


def main():
    """自测：弹 3 秒托盘图标（右键有菜单），然后自动移除"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent / "core"))
    from utils import fix_encoding

    fix_encoding()
    if not AVAILABLE:
        print("[!] 当前平台不支持系统托盘")
        return 1

    print("[i] 正在添加托盘图标……图标会显示 3 秒后自动移除")
    print(f"[i] NOTIFYICONDATAW 大小 = {ctypes.sizeof(NOTIFYICONDATAW)} 字节")

    tray = TrayIcon(
        tooltip="Folo 归档（自测）",
        menu=[("show", "打开主窗口"), ("archive", "今日归档"), None, ("quit", "退出")],
        on_command=lambda cmd: print(f"[i] 选中菜单项: {cmd}"),
        on_activate=lambda: print("[i] 双击了图标"),
    ).start()
    print("[i] 图标已添加，双击图标或右键看菜单；3 秒后自动移除")
    tray.notify("Folo 归档", "托盘自测：右键图标可退出")
    threading.Event().wait(3.0)
    tray.stop()
    print("[i] 图标已移除，托盘线程已结束")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
