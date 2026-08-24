# core/import_to_db.py — 导入数据库

## 职责

从每日总结文件导入文章数据到 SQLite 数据库（`result/articles.db`）。

## 用法

```bash
.venv/Scripts/python.exe src/core/import_to_db.py
.venv/Scripts/python.exe src/core/import_to_db.py 2026年07月07日
```

## 输入

`result/YYYY年MM月DD日/YYYY年MM月DD日.md`（总结文件，含 `[[文件名|标题]]` Wiki 链接与摘要）。

## 数据库表

| 表 | 说明 |
|----|------|
| `articles` | 文章基本信息（日期、文件名、标题、来源、摘要、Obsidian 链接） |
| `keywords` | 关键词 |
| `article_keywords` | 文章-关键词关联表 |

## 行为要点

- 关键词从标题和摘要中提取（按标点分割，取 2-10 字片段）
- 已存在记录执行 UPDATE（更新标题/摘要/关键词），否则 INSERT
- 输出目录路径来自 `utils.OUTPUT_BASE_DIR`
