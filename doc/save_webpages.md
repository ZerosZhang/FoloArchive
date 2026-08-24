# core/save_webpages.py — 下载网页

## 职责

根据文章列表 JSON 下载网页到本地输出目录（`result/YYYY年MM月DD日/`），并优化文件名。

## 用法

```bash
.venv/Scripts/python.exe src/core/save_webpages.py                                    # 下载当天文章
.venv/Scripts/python.exe src/core/save_webpages.py <列表文件> <输出目录>                # 指定输入输出
.venv/Scripts/python.exe src/core/save_webpages.py --overwrite <列表文件> <输出目录>    # 覆盖已存在文件
```

默认列表路径：`result/temp_data/「今天」.json`；默认输出：`result/今天/`。

## 不同来源的处理方式

| 来源 | 输出格式 | 内容获取方式 |
|------|----------|-------------|
| 虎嗅 | Markdown | Folo CLI 获取正文，失败重试 3 次；WAF 拦截时返回提示 |
| Product Hunt | Markdown | 直接使用 JSON 中的 summary 字段 |
| 其他网站 | HTML | `urllib.request` 直接下载，多编码尝试 |

## 行为要点

- 下载失败记录到 `failed_urls`，最后统一打印，不中断流程
- 默认不覆盖已存在文件，加 `--overwrite` 强制覆盖
- 文件名格式 `「来源」标题.html`，非法字符自动去除，同名自动追加 `_1`
- 失败原因回调为干净文本（`下载失败: HTTP 404: Not Found`），无 HTML 注释与换行

## 图片防盗链

`_get_referer_for_url()` 为 sspai.com、微信图片、hellogithub.com 等域名附加 Referer 头绕过反盗链。
