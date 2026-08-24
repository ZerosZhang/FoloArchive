#!/usr/bin/env python3
"""Endler 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class EndlerStrategy(BaseStrategy):
    """Matthias Endler (endler.dev): article-content → 单一大块"""

    name = "Matthias Endler"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "Matthias Endler" in filename_hint:
            return True
        return "endler.dev" in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<div[^>]*class="article-content"[^>]*>', html_text)
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

