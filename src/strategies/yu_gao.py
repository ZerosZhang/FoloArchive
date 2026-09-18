#!/usr/bin/env python3
"""Blog Yu Gao 解析策略"""

import re

from .base import BaseStrategy, register_strategy


@register_strategy
class YuGaoStrategy(BaseStrategy):
    """gaoyu.me (Blog Yu Gao): <article class="prose"> → 单一大块"""

    name = "Blog Yu Gao"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "Yu Gao" in filename_hint:
            return True
        return "gaoyu.me" in html_text and '<article class="prose">' in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<article class="prose">(.*?)</article>', html_text, re.DOTALL)
        if not m:
            return ""
        return m.group(1).strip()

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []
