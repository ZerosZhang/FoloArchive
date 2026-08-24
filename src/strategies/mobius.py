#!/usr/bin/env python3
"""Mobius 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class MobiusStrategy(BaseStrategy):
    """莫比乌斯 (mobius.blog): WordPress 博客, entry-content wp-block-post-content → 单一大块"""

    name = "莫比乌斯"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "莫比乌斯" in filename_hint:
            return True
        return "mobius.blog" in html_text and "wp-block-post-content" in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<div[^>]*class="entry-content wp-block-post-content"[^>]*>', html_text)
        if not m:
            m = re.search(r'<div[^>]*class="[^"]*wp-block-post-content[^"]*"[^>]*>', html_text)
        if not m:
            return ""
        start = m.end()
        end = _find_matching_close(html_text, start)
        body = html_text[start:end - 6]
        # 过滤点赞/随机文章区域 (ono-simple-like 插件)
        like_m = re.search(r'<div[^>]*class="ono-simple-like-space"[^>]*>', body)
        if like_m:
            like_end = _find_matching_close(body, like_m.end())
            body = body[:like_m.start()] + body[like_end:]
        return body

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

