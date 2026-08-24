# core/cluster_daily.py — 跨日主题聚类

## 职责

读取当日汇总 + 过去 7 天汇总，AI 分析跨日关联（时间线）和当日主题聚类，将当日汇总文件从"按来源分组"重组为"按主题聚类"。

## 用法

```bash
.venv/Scripts/python.exe src/core/cluster_daily.py
```

## 行为要点

- 需要 `src/config.json`（DeepSeek API 配置）
- 数据库路径来自 `utils.DB_PATH`，输出目录来自 `utils.OUTPUT_BASE_DIR`
- 调试文件（`ai_response_debug.txt`、`clustering_debug.json`）写入 `result/`
- 替换当日汇总文件内容（`result/YYYY年MM月DD日/YYYY年MM月DD日.md`）
