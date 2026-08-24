#!/usr/bin/env python3
"""WangZhiyong 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class WangZhiyongStrategy(BaseStrategy):
    """王志勇-和平海底 (auiou.com): 老式博客, div#t → 单一大块"""

    name = "王志勇-和平海底"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "王志勇" in filename_hint:
            return True
        return "auiou.com" in html_text and '<div id=t>' in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<div id=t>', html_text)
        if not m:
            return ""
        start = m.end()
        # 正文在评论区 (<div id=cr>) 之前结束，截取到评论区开始
        end = html_text.find('<div id=cr>', start)
        if end != -1:
            return html_text[start:end]
        end = _find_matching_close(html_text, start)
        return html_text[start:end - 6]

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []


# =============================================================================
