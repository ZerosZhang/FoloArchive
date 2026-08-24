#!/usr/bin/env python3
"""Tw93Weekly 解析策略"""

import re

from .base import BaseStrategy, register_strategy


@register_strategy
class Tw93WeeklyStrategy(BaseStrategy):
    """潮流周刊: weekly.tw93.fun + id="write" """

    name = "潮流周刊"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "潮流周刊" in filename_hint:
            return True
        return "weekly.tw93.fun" in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<div[^>]*id="write"[^>]*>(.*?)</div>\s*(?=<div|<script|</body)', html_text, re.DOTALL)
        if not m:
            return ""
        content = m.group(1)
        # 跳过 h1 标题（包含 logo、读者模式按钮和 JavaScript）
        h1_end = content.find("</h1>")
        if h1_end != -1:
            content = content[h1_end + 5:]
        # 移除残留的 script 标签
        content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
        return content.strip()

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

