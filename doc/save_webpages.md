# core/save_webpages.py — 下载网页

## 职责

根据文章列表 JSON 下载网页的原始 HTML 到中间目录（`result/temp_data/raw/YYYY年MM月DD日/`），并优化文件名。所有来源统一处理，渲染交给 `render_html.py`。

## 用法

```bash
.venv/Scripts/python.exe src/core/save_webpages.py                                    # 下载当天文章
.venv/Scripts/python.exe src/core/save_webpages.py <列表文件> <输出目录>                # 指定输入输出
.venv/Scripts/python.exe src/core/save_webpages.py --overwrite <列表文件> <输出目录>    # 覆盖已存在文件
```

默认列表路径：`result/temp_data/「今天」.json`。

## 处理方式

所有来源统一用 `urllib.request` 下载原始 HTML（多编码尝试），文件名格式 `「来源」标题.html`。**不再有任何按来源特判的分支**。

## 行为要点

- 下载失败记录到 `failed_urls`，最后统一打印，不中断流程
- `download_articles(..., overwrite=False)` 默认不覆盖已存在文件，CLI 加 `--overwrite` 强制覆盖；**归档管线（`archive_core._download_step`）固定以 `overwrite=True` 调用**，以便同一天重跑时重新下载并覆盖旧文件
- 非法字符自动去除（Windows 文件名兼容、统一中文标点、限制长度）
- 失败原因回调为干净文本（`下载失败: HTTP 404: Not Found`），无 HTML 注释与换行
- `_NO_PROXY_SITES` 中的域名（如 `meta.appinn.net`）绕过系统代理直连——部分代理节点与其 TLS 握手会报 `SSL: UNEXPECTED_EOF_WHILE_READING`
- `_PLAIN_UA_SITES` 中的域名（如 `mobius.blog`）改用普通客户端 UA，避免被 WAF 误判为伪装浏览器
- `optimize_titles()` 规范化目录内文件名（统一 `「来源」标题` 格式），跳过当天索引页

## 图片防盗链

图片本地化在 `render_html.py` 中进行，其 `_get_referer_for_url()` 为 sspai.com、微信图片、hellogithub.com 等域名附加 Referer 头绕过反盗链。
