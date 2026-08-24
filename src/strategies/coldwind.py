#!/usr/bin/env python3
"""Coldwind 解析策略"""

import re

from .base import BaseStrategy, register_strategy


@register_strategy
class ColdwindStrategy(BaseStrategy):
    """寒流の编程笔记: Hugo Stack 主题博客"""

    name = "寒流の编程笔记"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "寒流" in filename_hint or "编程笔记" in filename_hint:
            return True
        return "blog.coldwind.top" in html_text or "寒流の编程笔记" in html_text

    @classmethod
    def extract_body(cls, html_text):
        # 正文在 <section class="article-content"> 中（可能有引号也可能没有）
        m = re.search(r'<section[^>]*class="?article-content"?[^>]*>', html_text)
        if m:
            start = m.end()
            # 直接查找 </section> 结束标签
            end = html_text.find('</section>', start)
            if end > start:
                return html_text[start:end]
        return ""

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

