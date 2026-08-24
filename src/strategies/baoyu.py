#!/usr/bin/env python3
"""Baoyu 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class BaoyuStrategy(BaseStrategy):
    """宝玉的博客 (baoyu.io): prose → 单一大块"""

    name = "宝玉的博客"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "宝玉的博客" in filename_hint:
            return True
        return "baoyu.io" in html_text and 'class="prose prose-lg' in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<div[^>]*class="prose prose-lg[^"]*"[^>]*>', html_text)
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

