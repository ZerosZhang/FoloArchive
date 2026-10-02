# webui.py —— 本地网页版界面（纯标准库）

## 用途

`src/webui.py` 是项目唯一的图形界面：一个只用标准库的本地 HTTP 服务 + 单页界面，
用浏览器完成归档工作，**不引入任何第三方库**。

页面功能：

- 手动执行：4 个步骤复选框（按 `archive_core.STEPS` 动态生成）、日期输入、开始/停止、
  进度条、当前状态文字、各步骤耗时统计
- 实时日志：等宽字体、自动滚到底，失败行标红（含 `✗`/`❌`/「失败」）、警告标黄（`⚠️`）
- 归档进展热力图：HTML/CSS 网格绘制（无 JS 图表库），列=周、行=周一~周日，
  最近 53 周、5 档绿色系色阶，含星期/月份标签与「少→多」图例，另有一行总篇数概览
- 常驻定时：**内建调度线程**（默认开启），每天到设定时刻自动跑一次完整归档；
  也可用 `--no-schedule` 关闭，改由独立调度器 `src/scheduler.py` 负责（两者互斥）
- 失败邮件：运行结束后 `failures` 非空时按 `config.json` 的 `mail` 段发信

## 新增文件

| 文件 | 说明 |
|------|------|
| `src/webui.py` | HTTP 服务 + 全部后端逻辑（日志捕获、运行线程、热力图、内建定时调度、邮件） |
| `src/webui_page.py` | 单页 HTML/CSS/JS，作为字符串常量 `PAGE`，由 `GET /` 返回 |
| `启动Web界面.bat` | 双击启动（纯 ASCII，前台运行以便 Ctrl+C 停止） |

## 运行

```bash
# 默认 127.0.0.1:8765，启动后自动打开浏览器
.venv/Scripts/python.exe src/webui.py

# 指定地址/端口、不自动开浏览器
.venv/Scripts/python.exe src/webui.py --host 0.0.0.0 --port 9000 --no-browser

# 关闭内建定时（改由 后台运行.bat / scheduler.py 独立调度）
.venv/Scripts/python.exe src/webui.py --no-schedule
```

或双击项目根的 `启动Web界面.bat`（可带参数，如 `启动Web界面.bat --port 9000`）。

默认值可在 `config.json` 里通过可选的 `web` 段覆盖（读不到或格式不对会安全回退，
**不使用** `utils.load_config()`，因为它缺字段会 `sys.exit(1)`）：

```json
{ "web": { "host": "127.0.0.1", "port": 8765 } }
```

命令行参数优先级高于 `web` 段，`web` 段高于内置默认值。

## HTTP 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 单页界面 |
| GET | `/api/state` | `{running, progress, progress_text, steps, step_times, last_log_index, next_log_index, today, schedule}` |
| GET | `/api/logs?since=N` | `{lines:[...], next:N}`，增量拉取（`N` 为上次返回的 `next`） |
| GET | `/api/heatmap` | `{counts:{日期:篇数}, total, recent30}` |
| GET | `/api/schedule` | 定时状态 `{owner, running, enabled, time, note}`；`?refresh=1` 强制绕过缓存 |
| POST | `/api/run` | body `{"steps":[1,2,3,4], "date":"YYYY年MM月DD日"}` → `{ok, error}` |
| POST | `/api/stop` | 请求停止当前运行 → `{ok}` |

前端约每 1 秒轮询 `/api/state` 与 `/api/logs?since=N`；热力图按需刷新，常驻定时状态约每 30 秒刷新。

## 关键实现

### 日志捕获

`run_archive` 自己的消息走 `log=` 回调，但底层模块（`render_html`/`summarize`/
`save_webpages`）用 `print()` 直接输出。因此 `webui.py` 定义一个线程安全的
`LogBuffer`（`io.TextIOBase` 子类）：

- 启动后把 `sys.stdout` / `sys.stderr` 一起重定向到它，两路输出按行合并；
- `log=` 回调直接走 `LogBuffer.append()`，同样按行落库；
- 内部是 `deque(maxlen=2000)` 的环形缓冲，每条消息带自增序号，`lines_since(idx)`
  支持增量拉取，缓冲区满时旧行被丢弃、序号继续递增，前端不会重复；
- 可选镜像流：每行同时写回原始终端，命令行仍能看到输出；
- 重写了 `BaseHTTPRequestHandler.log_message` 使其静音，避免 HTTP 访问日志混入归档日志。

### 运行与并发

归档在后台 daemon 线程里跑，HTTP 服务用 `ThreadingHTTPServer` 保持响应。
`start_run()` 在锁内检查 `running`，同一时间只允许一次运行，重复点「开始」返回
`{ok:false, error:"已有任务正在运行…"}`。「停止」把 `should_stop` 置位，通过
`run_archive` 的 `should_stop` 回调生效（在步骤边界检查）。

运行结束（含异常）后在 `finally` 里清理状态、`flush_partial()` 残余行，再调用
`_notify_failures()`：`failures` 非空 → `notify.load_mail_config()`，配置存在则
`format_failure_report` + `send_failure_mail` 并写日志，未配置则只记一行提示，
全程 try/except，绝不抛异常。

### 热力图

数据来自 `heatmap.scan_daily_counts`（纯标准库，见 [doc/heatmap.md](heatmap.md)），
扫描 `OUTPUT_BASE_DIR` 与 `OUTPUT_BASE_DIR/"归档"` 后按日期相加，得到总数与
最近 30 天篇数，带 5 秒缓存。

前端把 `{日期:篇数}` 在 JS 里按 53 周 × 7 天渲染成 CSS Grid，色阶阈值
0 / 1–3 / 4–7 / 8–15 / 16+。

### 内建定时调度

默认开启：网页版进程自己每天跑一次完整归档，因此**只开一个进程**即可同时提供界面与定时。
用 `--no-schedule` 可关闭，退回「界面 + `scheduler.py` 独立调度」的旧样子。

- **归属判定**：启动时读 `config.json` 的 `schedule` 段并尝试用命名互斥量
  `CreateMutexW(None, False, "FoloArchiveScheduler")` 抢占调度权（名称与
  `scheduler.py` 的 `MUTEX_NAME` 一致）。取到就启动调度线程并**长期持有句柄**
  （进程存活期间不 `CloseHandle`）；取不到（`GetLastError` 为 `183`
  `ERROR_ALREADY_EXISTS`）说明外部调度器在跑，只记一行日志、不重复调度。
  非 Windows 或 `ctypes` 调用失败时退化为「不互斥」，不阻塞启动。
- **调度线程**（daemon）：每 20 秒检查一次，同时满足才触发归档——
  `schedule.enabled` 为真、当前时间 >= 设定时刻、进程内 `_SCHED_LAST_RUN_DATE`
  不是今天、且当天列表 JSON `result/temp_data/「今天」.json` 不存在。
- **触发方式**：直接调用现有的 `start_run([1,2,3,4], 今天)`，不另起子进程、不重写流程。
  只有 `start_run` 返回 `ok=True` 才记下当天已跑，失败（如已有任务在运行）保持未记，
  20 秒后自然重试。线程内任何异常都只记日志，绝不让线程退出。
- **设定时刻**：从 `config.json` 的 `schedule` 段读 `enabled` 与 `time`（`HH:MM`），
  读不到或字段非法时回退 `enabled=True`、`time="08:00"`；**不使用**
  `utils.load_config()`，因为它缺字段会 `sys.exit(1)`。调度线程每轮重新读取配置，
  改 `config.json` 后无需重启。
- **与 `scheduler.py` 互斥**：两者抢同一个互斥量，谁先起谁负责，另一个只会打印提示后
  不调度，因此不会重复执行。

`/api/schedule` 返回 `{owner, running, enabled, time, note}`：

| `owner` | 含义 | `running` |
|---------|------|-----------|
| `webui` | 本进程在调度 | `true` |
| `external` | 外部 `scheduler.py` 持有互斥量 | `true` |
| `disabled` | 内建调度被 `--no-schedule` 关闭，或 `schedule.enabled=false` | `false` |

`note` 是给页面显示的一句话说明；页面按 `owner` 显示「由本进程负责（运行中）」/
「由外部调度器负责」/「未启用」三种状态与对应说明。仅当调用方即本进程时才会是
`webui`——`_scheduler_running()` 无法区分「自己持有的互斥量」和「别人的」，
所以先判断 `_SCHED_OWNER == "webui"` 再探测。

## 取舍

- 内建定时默认开启，定时由本进程负责，**关闭网页版即停止定时**；不想要这个行为就加
  `--no-schedule`，改用 `后台运行.bat` 独立调度（两者互斥）。
- 停止是协作式的，在当前步骤边界生效，不会中断正在进行的下载/摘要。
- 服务进程退出会结束正在运行的归档线程（daemon 线程）。
- 日志缓冲区上限 2000 行，超出后最早的行从页面/接口消失（终端镜像不受影响）。

## 自测

```bash
# 语法检查
.venv/Scripts/python.exe -m compileall -q src

# 启动（后台）
PYTHONUTF8=1 .venv/Scripts/python.exe src/webui.py --no-browser

# 接口自测
PYTHONUTF8=1 .venv/Scripts/python.exe -c "
import urllib.request, json
for p in ['/api/state','/api/heatmap','/api/schedule']:
    d = json.load(urllib.request.urlopen('http://127.0.0.1:8765'+p, timeout=10))
    print(p, '->', str(d)[:200])
print('/ ->', len(urllib.request.urlopen('http://127.0.0.1:8765/', timeout=10).read()), 'bytes')
"
```

> 自测运行时**只跑步骤 3**（`{"steps":[3]}`），避免触发步骤 1 把 Folo 未读标记为已读。
