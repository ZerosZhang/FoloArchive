# archive_gui.py — 归档图形界面（PySide6）

## 职责

PySide6 桌面 GUI，以可视化方式执行与 `archive.py` 相同的 5 步归档流程，核心逻辑复用 `archive_core.py`。

## 启动方式

```bash
# 双击项目根目录 启动GUI.bat（推荐，无控制台窗口）

# 或手动启动
.venv/Scripts/pythonw.exe src/archive_gui.py
```

> `pythonw.exe`（而非 `python.exe`）启动不会弹出控制台窗口；`pythonw` 下 stdout/stderr 为 `None`，`utils.fix_encoding()` 会跳过包装。

## 界面功能

| 区域 | 说明 |
|------|------|
| 任务设置 | 步骤复选框（全选/反选/今天按钮）+ 日期输入，与耗时统计并排 |
| 控制按钮 | 开始执行 / 停止 / 清除日志 |
| 进度条 | 按步骤推进（10 → 20-50 → 55 → 75 → 90 → 100） |
| 执行日志 | 实时日志；失败行红色、警告行黄色（判定规则见下） |
| 耗时统计 | 各步骤耗时 + 总耗时 + 当前运行时间，每秒刷新 |

## 日志着色规则

| 条件 | 颜色 |
|------|------|
| 含 `✗` / `❌`，或 `下载失败` / `转换失败` / `总结失败` / `失败原因` | 红色 |
| 含 `⚠️` | 黄色 |
| 其他 | 默认色 |

## 关键实现

- **输出重定向**：`LogStream`（线程安全）替换 `sys.stdout`/`sys.stderr`，子模块的 `print` 统一进日志框；`flush()` 不发射无换行残留，保证 `print(..., end="")` 前缀与后续内容合并为单行
- **日志去重**：与上一条重复的行不显示
- **富文本格式**：`append_log` 用 `QTextCursor` + `QTextCharFormat` 显式指定颜色，避免格式继承污染
- **停止机制**：`should_stop` 标志经回调传入核心流程，步骤间检查
- **步骤耗时**：`run_archive` 完成后把 `step_times` 写入统计面板
