#!/usr/bin/env python3
"""Suiyan 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class SuiyanStrategy(BaseStrategy):
    """碎言 (suiyan.cc): Astro 博客, article-body → 单一大块
    注意：必须在少数派策略之前注册，因少数派也检测 class="article-body"
    """

    name = "碎言"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "碎言" in filename_hint:
            return True
        return "suiyan.cc" in html_text and 'class="article-body"' in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<div[^>]*class="article-body"[^>]*>', html_text)
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

