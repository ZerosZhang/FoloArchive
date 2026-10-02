# archive.py — 一键归档 CLI 入口

## 职责

命令行入口，串行执行归档的 4 个步骤，核心逻辑委托给 `archive_core.py`。

| 编号 | 名称 | 说明 |
|------|------|------|
| 1 | fetch | 获取未读文章列表（含认证） |
| 2 | download | 下载原始 HTML 并优化文件名 |
| 3 | convert | 渲染为干净 HTML |
| 4 | summarize | AI 摘要与索引页生成 |

## 用法

```bash
# 完整执行（需 .venv 环境）
.venv/Scripts/python.exe src/archive.py

# 从第 4 步开始（断点续跑）
.venv/Scripts/python.exe src/archive.py --start-step 4

# 仅执行第 3 步
.venv/Scripts/python.exe src/archive.py --only-step 3

# 指定日期
.venv/Scripts/python.exe src/archive.py --date "2026年07月07日"

# 列出所有步骤
.venv/Scripts/python.exe src/archive.py --list-steps
```

## 参数

| 参数 | 说明 |
|------|------|
| `--start-step N` | 从第 N 步开始执行（跳过前面步骤），有效范围 1-4 |
| `--only-step N` | 只执行第 N 步，有效范围 1-4 |
| `--list-steps` | 列出所有步骤及编号 |
| `--date "YYYY年MM月DD日"` | 指定日期（默认今天） |

## 行为

- 当天列表 `result/temp_data/「日期」.json` 已存在时，步骤 1 自动**跳过抓取**并复用该 JSON（避免重复把 Folo 未读标记为已读）；要强制重抓需先删除该文件，或用 `--date` 指定另一个日期
- 跳过步骤 1-2 时，步骤 2 自动从 `result/temp_data/「日期」.json` 加载文章列表
- 步骤 2 无法加载列表时以退出码 1 结束
- **同一天重跑**（再次整轮执行）：复用列表 → 重下载并覆盖原始 HTML → 重渲染 → 重新生成摘要并覆盖（详见 `doc/archive_core.md`）
- 日志通过回调输出到 stdout（网页版界面 `src/webui.py` 复用同一核心）
- **失败邮件**：运行结束后若 `run_archive()` 返回的 `failures` 非空，调用 `core/notify.py` 发送通知邮件；未配置 `config.json` 的 `mail` 段时只打印一行提示，**不影响退出码**

## 关键实现

- `main()` 解析参数 → 计算步骤范围 → 调用 `archive_core.run_archive()`
- 步骤范围由 `len(STEPS)` 计算，步骤增删后范围自动跟随
- 日志回调：`lambda message: print(message, flush=True)`
- `notify_failures()`：从返回值取 `failures`，为空直接返回；否则读 `config.json` 的 `mail` 段发信（复用 `core/notify.py`）

## 服务器定时运行（无界面）

Windows Server 直接用 `定时归档.bat`：

```bat
定时归档.bat                 :: 立即执行一次
定时归档.bat install         :: 注册每天 08:00 的计划任务
定时归档.bat install 09:30   :: 指定时间
定时归档.bat status          :: 查看任务状态
定时归档.bat remove          :: 删除任务
```

- 日志写入 `result\archive.log`（超过约 2MB 自动轮转为 `archive.log.old`）
- 计划任务以「创建该任务时的当前账户」运行，因此能读到该用户的 `~/.folo/config.json` 登录态
- 服务器上**只需** `.venv\Scripts\pip install openai`，CLI 不需要任何界面库
