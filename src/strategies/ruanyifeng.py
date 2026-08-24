#!/usr/bin/env python3
"""Ruanyifeng 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class RuanyifengStrategy(BaseStrategy):
    """阮一峰的网络日志: main-content → 单一大块"""

    name = "阮一峰的网络日志"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "阮一峰" in filename_hint:
            return True
        return 'id="main-content"' in html_text and "ruanyifeng.com" in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<div[^>]*class="asset-content entry-content"[^>]*id="main-content"[^>]*>', html_text)
        if not m:
            m = re.search(r'<div[^>]*id="main-content"[^>]*>', html_text)
        if not m:
            return ""
        start = m.end()
        end = _find_matching_close(html_text, start)
        body_html = html_text[start:end - 6]
        return body_html

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

