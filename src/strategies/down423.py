#!/usr/bin/env python3
"""Down423 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class Down423Strategy(BaseStrategy):
    """423Down: entry → 单一大块（登录页返回提示）"""

    name = "423Down"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "423Down" in filename_hint:
            return True
        return "423down.com" in html_text

    @classmethod
    def extract_body(cls, html_text):
        # 优先提取 entry
        m = re.search(r'<div[^>]*class="entry"[^>]*>', html_text)
        if m:
            start = m.end()
            end = _find_matching_close(html_text, start)
            return html_text[start:end - 6]

        # 登录提示页
        if "内容仅限登录后可见" in html_text:
            return "<p>⚠️ 内容仅限登录后可见。</p>"

        # 兜底：尝试 content 区域
        m = re.search(r'<div[^>]*class="content"[^>]*>', html_text)
        if m:
            start = m.end()
            end = _find_matching_close(html_text, start)
            return html_text[start:end - 6]

        return ""

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

