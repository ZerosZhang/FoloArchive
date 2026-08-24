#!/usr/bin/env python3
"""HexoBlog 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class HexoBlogStrategy(BaseStrategy):
    """通用 Hexo 博客: post-body (itemprop="articleBody") → 单一大块"""

    name = "Hexo博客"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "Hexo" in html_text and 'class="post-body"' in html_text:
            return True
        return 'itemprop="articleBody"' in html_text and 'class="post-body"' in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<div[^>]*class="post-body"[^>]*>', html_text)
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

