# archive.py — 一键归档 CLI 入口

## 职责

命令行入口，串行执行归档的 5 个步骤，核心逻辑委托给 `archive_core.py`。

| 编号 | 名称 | 说明 |
|------|------|------|
| 1 | fetch | 获取未读文章列表（含认证） |
| 2 | download | 下载网页并优化文件名 |
| 3 | convert | HTML → Markdown 转换 |
| 4 | summarize | AI 生成文章总结 |
| 5 | import | 导入文章到数据库 |

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
| `--start-step N` | 从第 N 步开始执行（跳过前面步骤） |
| `--only-step N` | 只执行第 N 步 |
| `--list-steps` | 列出所有步骤及编号 |
| `--date "YYYY年MM月DD日"` | 指定日期（默认今天） |

## 行为

- 跳过步骤 1-2 时，步骤 2 自动从 `result/temp_data/「日期」.json` 加载文章列表
- 步骤 2 无法加载列表时以退出码 1 结束
- 日志通过回调输出到 stdout（GUI 版本复用同一核心，见 `archive_gui.py`）

## 关键实现

- `main()` 解析参数 → 计算步骤范围 → 调用 `archive_core.run_archive()`
- 日志回调：`lambda message: print(message, flush=True)`
