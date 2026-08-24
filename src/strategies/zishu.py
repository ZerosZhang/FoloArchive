#!/usr/bin/env python3
"""Zishu 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class ZishuStrategy(BaseStrategy):
    """子舒的博客 (zishu.me): Astro 博客, post-content → 单一大块"""

    name = "子舒的博客"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "子舒的博客" in filename_hint:
            return True
        return "zishu.me" in html_text and 'class="post-content"' in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<div[^>]*class="post-content"[^>]*>', html_text)
        if not m:
            return ""
        start = m.end()
        end = _find_matching_close(html_text, start)
        return html_text[start:end - 6]

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

