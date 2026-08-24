# core/folo_export.py — 获取未读文章列表

## 职责

从 Folo CLI 获取未读文章列表（阶段 1），提取标题、来源、原文链接、发布时间等元信息，保存为 JSON 到 `result/temp_data/「当天日期」.json`。

## 用法

```bash
.venv/Scripts/python.exe src/core/folo_export.py
```

## 行为要点

- 通过 `subprocess.run(["bash", "-c", ...])` 执行 `npx folocli@latest`
- `NODE_PATH` 硬编码为 `/c/Program Files/nodejs`（Windows Git Bash 环境）
- 自动查找 Git Bash（`shutil.which("bash")`）
- `run_folo()` 从 stdout/stderr 中提取 JSON，兼容 WSL 警告等杂讯
- 只获取"长文"类型（`view=0`），不包括短视频
- 保存完成后自动标记所有文章为已读
- 需要先登录 Folo CLI（`npx folocli@latest login`）

## 输出

`result/temp_data/「2026年08月24日」.json`（列表为数组，每项含 `title`/`url`/`feed_title`/`published`/`summary` 等字段）。
