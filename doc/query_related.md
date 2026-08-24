# core/query_related.py — 关键词查询

## 职责

按关键词查询过去 N 天内包含该关键词的相关文章（基于 `result/articles.db` 数据库）。

## 用法

```bash
.venv/Scripts/python.exe src/core/query_related.py "AI硬件" 7    # 过去 7 天含"AI硬件"的文章
.venv/Scripts/python.exe src/core/query_related.py "关键词"       # 默认 7 天
```

## 行为要点

- 数据库不存在时提示先运行导入步骤
- 匹配 `keywords` 表（文章摘要中提取的关键词）
- 数据库路径来自 `utils.DB_PATH`
