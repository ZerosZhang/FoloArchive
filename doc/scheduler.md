# scheduler.py — 常驻定时运行（无界面）

## 用途

在后台一直运行，每天到设定时刻自动执行一次完整归档。**不使用 Windows 计划任务**，
适合「不想用计划任务、就让 CLI 挂着跑」的场景。

真正的归档交给子进程 `src/archive.py`，因此日志输出、失败邮件、退出码全部复用既有逻辑，
本模块只负责「什么时候跑」。

## 运行

```bash
# 用 config.json 的 schedule 段（默认 08:00）
.venv/Scripts/python.exe src/scheduler.py

# 覆盖设定时刻
.venv/Scripts/python.exe src/scheduler.py --time 08:00

# 只回显最近日志后退出
.venv/Scripts/python.exe src/scheduler.py --tail
```

或双击项目根的 `后台运行.bat`（最小化窗口启动）：

```
后台运行.bat                  启动（最小化窗口）
后台运行.bat stop             停止
后台运行.bat status           查看进程状态 + 日志尾部
```

## 设定时刻

来自 `config.json` 的 `schedule` 段：

```json
{ "schedule": { "enabled": true, "time": "08:00" } }
```

命令行 `--time` 优先级最高；`enabled` 为 `false` 时进程仍常驻，但不会触发归档。

## 「当天是否已跑过」的判据

1. **优先**：步骤 1 产出的列表 JSON `result/temp_data/「日期」.json` 是否存在 —— 它是持久化
   产物，进程重启后依然有效，可避免重复执行会「把 Folo 未读标记为已读」的步骤 1；
2. **兜底**：进程内的 `last_run_date` —— 当天若没有未读文章则不会生成 JSON，仅靠文件
   判断会导致到点后每 20 秒反复触发。

因此：**当天已有 JSON 就跳过**，同一天最多触发一次。想手动补跑用网页版界面或直接跑 `archive.py`。

## 单实例保护

用 Windows 命名互斥量（`CreateMutexW` + `ERROR_ALREADY_EXISTS`，名称 `FoloArchiveScheduler`）
保证同一时间只有一个常驻实例；重复启动会打印提示并立即退出。非 Windows 环境或调用失败时不阻塞启动。

网页版 `webui.py` 默认会内建定时并抢占同一个互斥量，因此**若网页版正在运行并持有互斥量，
本脚本会拒绝启动**（打印「已有常驻实例在运行，本进程退出」）；反之若本脚本已在运行，网页版
启动时检测到互斥量被占用，就只做状态展示、不再重复调度。想同时开网页版又用本脚本，请用
`webui.py --no-schedule` 关闭网页版内建定时。

## 日志

`result/scheduler.log`（UTF-8，追加写），每行带时间戳；子进程输出**按行实时**写入，
不是等跑完才落盘。文件会持续追加，需要时手动清理。

## 与「计划任务」方案的取舍

| | 常驻进程（本模块） | Windows 计划任务 |
|---|---|---|
| 依赖 | 进程必须一直活着 | 系统负责，无需常驻 |
| 机器重启后 | **不会自动恢复**，需手动重新启动 | 自动恢复 |
| 会中断的场景 | 注销登录 / 关机 / 手动结束 | — |
| 排查 | 日志集中在 `scheduler.log` | 要翻任务计划程序的历史记录 |

若需要「关机重启后也照跑」，仍建议用 `定时归档.bat install` 注册计划任务。
