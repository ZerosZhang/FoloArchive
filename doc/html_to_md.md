# core/html_to_md.py — HTML → Markdown

## 职责

将下载的 HTML 批量转换为 Markdown（策略模式自动识别来源），提取正文并下载图片到 `assets/`。

## 用法

```bash
.venv/Scripts/python.exe src/core/html_to_md.py                                  # 批量转换当天文件夹
.venv/Scripts/python.exe src/core/html_to_md.py <html文件路径>                    # 转换指定文件
```

## 转换流程

1. **预处理**：chroma / shiki 代码块 → 占位符；notice 块 → Obsidian callout
2. **解析**：`TextExtractor`（HTMLParser）提取链接、图片、表格、标题、列表等
3. **图片下载**：并发下载远程图片到 `assets/`，重命名为 `日期_编号.ext`，替换为相对路径
4. **元信息**：从 `result/temp_data/「日期」.json` 匹配标题/来源/发布时间/原文链接，生成 `> [!summary]` 块

## Markdown 格式规则

- 块级元素（段落、标题、引用、代码块）之间有空行
- 列表项紧凑排列（无空行），`ul`/`ol` 结束补空行与后续块分隔
- 表格输出 GitHub 风格（`| --- |` 分隔行）
- 日志按显示宽度对齐（中文占 2 字符），状态列统一 `✓ [来源]` / `✗ [来源] 转换失败` / `✗ 未知来源`

## 输出

`result/YYYY年MM月DD日/「来源」标题.md` + `assets/` 图片目录。

## 新增来源

见 `doc/src/strategies.md`。
