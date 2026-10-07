# trayicon.py — Windows 系统托盘图标

## 职责

用纯 `ctypes` 调 Win32 的 `Shell_NotifyIcon`，给桌面小窗口（`src/gui.py`）提供
「关闭窗口 → 收进托盘 → 右键退出」的能力。**零第三方依赖**——项目约定只依赖标准库 + `openai`，
所以不引入 `pystray` 之类的库。

| 接口 | 说明 |
|------|------|
| `TrayIcon(tooltip, menu, on_command, on_activate=None, icon_path=None)` | 构造（此时还没有图标） |
| `start(timeout=5)` | 起托盘线程、建窗口、`NIM_ADD` 注册图标；失败抛 `RuntimeError` |
| `stop(timeout=3)` | `NIM_DELETE` 移除图标 + 结束托盘线程；可重复调用 |
| `notify(title, text)` | 弹一次气泡提示；**任意线程可调** |
| `AVAILABLE` | 当前平台是否支持（非 Windows 为 `False`） |

`menu` 是 `[(command_id, label) | None, ...]`，`None` 表示一条分隔线。
菜单项被选中时回调 `on_command(command_id)`。

```python
tray = TrayIcon(
    tooltip="Folo 归档",
    menu=[("show", "打开主窗口"), ("archive", "今日归档"), None, ("quit", "退出")],
    on_command=queue.put,                       # 回调在托盘线程里，别直接碰界面
    on_activate=lambda: queue.put("show"),      # 双击图标
).start()
...
tray.stop()
```

## 关键实现

- **独立的托盘线程 + 自己的消息循环**（`GetMessageW` / `DispatchMessageW`）。
  不借用 tkinter 的消息泵，因此不依赖 Tk 是否把消息派发给我们的窗口；
  也正因为自成一体，本模块不 import tkinter，可以单独自测。
- **回调在托盘线程里发生**，调用方必须自己把结果搬回界面线程。`gui.py` 的
  做法是 `on_command=self._tray_commands.put`（`queue.Queue`），再由
  `root.after(120, self._poll_tray)` 在界面线程里消费。
- **窗口过程必须被实例长期持有**（`self._wndproc = WNDPROC(...)`）。WNDPROC 被 GC
  回收后 Windows 会调用到野指针，程序直接崩——这是 ctypes 写 WNDPROC 最常见的坑。
- **`NOTIFYICONDATAW` 字段顺序必须与 `winshellapi.h` 完全一致**，错了 Shell 会静默
  失败（`Shell_NotifyIcon` 返回 False 但没有其他提示）。x64 上该结构 976 字节。
- **`TrackPopupMenu` 前必须 `SetForegroundWindow`**，否则点菜单外面菜单不会消失；
  调用后用 `TPM_RETURNCMD` 拿回被选中的菜单项 id，再 `DestroyMenu`。
- **类名重复注册要容错**：同进程内创建第二个实例时 `RegisterClassW` 返回 0 且
  `GetLastError() == 1410`（`ERROR_CLASS_ALREADY_EXISTS`），这不算失败。
- 气泡提示走 `notify()` → `PostMessage(WM_APP+2)` → 托盘线程里 `NIM_MODIFY | NIF_INFO`，
  避免跨线程直接调 Shell API。
- 托盘线程里的一切回调都包在 `_safe_call()` 里，异常不允许把线程搞死。

## 自测

```bash
.venv/Scripts/python.exe src/trayicon.py
```

会真实添加一个托盘图标、发一条气泡提示，3 秒后自动移除并退出。
想手工验证右键菜单，就趁这 3 秒内右键图标。

## 已知边界

- 只在 Windows 有效；其他平台 `AVAILABLE=False`，`start()` 抛 `RuntimeError`，
  `gui.py` 会退化回「关闭窗口即退出」并给出提示。
- 未处理资源管理器的「任务栏重建」广播（`TaskbarCreated`）。真出现图标丢失时，
  重启窗口即可；对这个低频场景不值得增加复杂度。
- 不考虑 DPI 缩放下的图标尺寸，用的是系统默认尺寸的应用图标
  （`IDI_APPLICATION`）；需要自定义图标时给 `icon_path` 传 `.ico`。
