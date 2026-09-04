#!/usr/bin/env python3
"""Ezindie 解析策略"""

import re

from .base import BaseStrategy, register_strategy


@register_strategy
class EzindieStrategy(BaseStrategy):
    """ezindie.com（独立开发变现周刊）: <article> → 单一大块"""

    name = "独立开发变现周刊"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "ezindie" in filename_hint:
            return True
        return "ezindie.com" in html_text and "markdown-body-box" in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r"<article>.*?</article>", html_text, re.DOTALL)
        if not m:
            return ""
        content = m.group(0)
        # 去掉 h1 大标题（与文件名重复）
        content = re.sub(r"<h1[^>]*>.*?</h1>", "", content, count=1, flags=re.DOTALL)
        # 拆掉标题中指向自身的锚点链接 <h2><a href="#...">文字</a></h2> → <h2>文字</h2>
        content = re.sub(
            r"<h([1-6])[^>]*>\s*<a[^>]*>(.*?)</a>\s*</h\1>",
            r"<h\1>\2</h\1>",
            content,
            flags=re.DOTALL,
        )
        return content.strip()

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []
