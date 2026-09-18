# core/folo_export.py — 获取未读文章列表

## 职责

从 Folo CLI 获取未读文章列表（阶段 1），提取标题、来源、原文链接、发布时间等元信息，保存为 JSON 到 `result/temp_data/「当天日期」.json`。

## 用法

```bash
.venv/Scripts/python.exe src/core/folo_export.py
```

## 行为要点

- 直接 `subprocess.run([find_npx(), "--yes", "folocli@latest", ...])`，不经过任何 shell
- `find_npx()` 定位 `npx.cmd`（PATH 优先，兜底 `C:\Program Files\nodejs\npx.cmd`）
- 子进程环境用 `build_node_env()` 注入 `HTTP(S)_PROXY` + `NODE_USE_ENV_PROXY=1`（Node 的 fetch 不读系统代理）
- Windows 下加 `CREATE_NO_WINDOW`，避免 GUI 模式闪黑窗
- `run_folo()` 从 stdout/stderr 中提取 JSON，兼容 npx 提示等杂讯
- 只获取"长文"类型（`view=0`），不包括短视频
- 保存完成后自动标记所有文章为已读
- 需要先登录 Folo CLI（`npx folocli@latest login`）

## 输出

`result/temp_data/「2026年08月24日」.json`（列表为数组，每项含 `title`/`url`/`feed_title`/`published`/`summary` 等字段）。
