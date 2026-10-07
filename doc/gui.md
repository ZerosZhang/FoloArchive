# gui.py — 桌面小窗口

## 职责

把网页版服务包一层极简的桌面窗口，替代「双击 bat → 弹出终端黑框」的启动方式。
窗口只有三个按钮，**不展示日志**（进度、实时日志、耗时、热力图都在网页版界面里看）。

| 按钮 | 行为 |
|------|------|
| 今日归档 | 触发当天 `[1, 2, 3, 4]` 完整归档 |
| 打开 web 页面 | 用默认浏览器打开 `http://127.0.0.1:8765/` |
| 退出服务 | 真正退出：停服务 + 撤托盘图标 + 关窗 |

## 托盘（关闭窗口 ≠ 退出）

**点窗口关闭按钮（X）或按 Esc 不会退出**，而是把窗口收进系统托盘继续后台运行
（`root.withdraw()`，任务栏图标也一并消失），并弹一条气泡提示。要真正退出，
右键托盘图标选「退出」，或点窗口里的「退出服务」按钮。

托盘图标右键菜单：

| 菜单项 | 行为 |
|--------|------|
| 打开主窗口 | 唤回窗口（双击托盘图标等效） |
| 今日归档 | 触发当天完整归档 |
| 退出 | 停服务 + 移除托盘图标 + 关窗 |

窗口收在托盘里时，任何操作的结果都会额外用气泡提示反馈（否则用户看不到状态行）。
托盘由 `src/trayicon.py` 提供（纯 `ctypes` 调 Shell_NotifyIcon，零第三方依赖，
详见 [trayicon.md](trayicon.md)）。

**托盘不可用时的兜底**：若托盘图标添加失败（或非 Windows），状态行会标红提示，
此时**关闭窗口会弹提示而不是把窗口藏起来**——否则用户会既看不到窗口又没有托盘入口。
真正退出仍可用「退出服务」按钮。

## 运行

```bash
.venv/Scripts/pythonw.exe src/gui.py            # 无控制台窗口（启动Folo.bat 走这条）
.venv/Scripts/python.exe  src/gui.py            # 带控制台，便于看启动异常
.venv/Scripts/python.exe  src/gui.py --port 9000 --no-schedule
```

| 参数 | 说明 |
|------|------|
| `--host` | 监听地址（默认 `127.0.0.1`，可被 `config.json` 的 `web.host` 覆盖默认值） |
| `--port` | 监听端口（默认 `8765`） |
| `--no-schedule` | 不启动内建每日定时（改由 `python src/scheduler.py` 独立调度） |

## 关键实现

- **服务跑在本进程内**：`webui.start_service()` 建好服务后，用 `Service.serve_in_background()`
  在后台 daemon 线程 `serve_forever`。因此按钮能直接调用 `webui.start_run()` /
  `webui.state_payload()`，**不需要子进程与进程间通信**。
- **界面**：`tkinter` 单窗口，`FoloWindow._build_widgets()` 里一个标题、一行状态、三个按钮；
  字体用「Microsoft YaHei UI」，窗口不可缩放（`resizable(False, False)`）。
- **状态行**：只有一行文字，不是日志区。归档点击后显示「已开始归档 <日期>，进度与日志请看 web 页面」；
  出错时文字变红并说明原因（`#b3261e`）。
- **归档不阻塞界面**：`webui.start_run()` 本身只做校验并起后台线程，窗口立刻回到可响应状态。
  归档进行中再点「今日归档」会如实提示「已有任务正在运行…」。
- **收托盘 vs 真退出是两条路**：`hide_to_tray()`（X / Esc）只 `withdraw()` + 气泡；
  `exit_service()`（「退出服务」按钮 / 托盘菜单「退出」）才停服务、撤图标、关窗。
- **退出**：`exit_service()` 先查 `webui.state_payload()["running"]`，归档进行中弹确认框，
  用户取消则不退出；确认后 `tray.stop()` → `Service.stop()`（`shutdown()` + `server_close()`）
  → `root.destroy()`。
- **托盘回调跨线程**：托盘线程只做 `self._tray_commands.put(command)`，
  界面线程用 `root.after(120, self._poll_tray)` 轮询消费，再分发到
  `show_window()` / `archive_today()` / `exit_service()`；未知命令忽略。
- **启动失败不崩**：端口被占用等 `OSError` 由 `_start_service()` 捕获，弹 `messagebox.showerror`
  并把状态行标红；此时服务为 `None`，「今日归档 / 打开 web 页面」只提示不动作，「退出服务」仍可用。
- **缺 tkinter 的提示**：`import tkinter` 失败时用 `ctypes` 调 `user32.MessageBoxW` 弹窗说明
  修复方法（`pythonw` 下没有控制台，`print` 看不到）。`启动Folo.bat` 也会先跑
  `python -c "import tkinter"` 做同样的检查，便于在控制台里看到文字。

## 与其他入口的关系

| 入口 | 形态 | 适用 |
|------|------|------|
| `src/gui.py`（`启动Folo.bat`） | 桌面小窗口（可收进托盘）+ 内嵌网页版服务 | 日常使用，无终端黑框 |
| `src/webui.py`（`启动Web界面.bat`） | 控制台进程 + 网页版界面 | 服务器 / 无 tkinter 的环境 |
| `src/archive.py` | 纯 CLI | 计划任务、无人值守 |

三者共用同一套核心（`archive_core.run_archive`），网页版与桌面窗口还共用同一份服务生命周期
（`webui.Service` / `webui.start_service`），因此内建定时的互斥量（`FoloArchiveScheduler`）
逻辑完全一致：桌面窗口开着时，它和 `scheduler.py` 依旧互斥。
