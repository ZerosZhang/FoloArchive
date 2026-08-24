#!/usr/bin/env python3
"""Juya 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class JuyaStrategy(BaseStrategy):
    """橘鸦AI早报 (imjuya.github.io): post-content → 单一大块"""

    name = "橘鸦AI早报"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "橘鸦AI早报" in filename_hint:
            return True
        return "imjuya.github.io" in html_text

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

