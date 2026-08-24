#!/usr/bin/env python3
"""Tumeng 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class TumengStrategy(BaseStrategy):
    """土猛的员外 (luxiangdong.com): article-content → entry → 单一大块"""

    name = "土猛的员外"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "土猛的员外" in filename_hint:
            return True
        return "luxiangdong.com" in html_text and 'class="article-content"' in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<div[^>]*class="article-content"[^>]*>', html_text)
        if not m:
            return ""
        start = m.end()
        end = _find_matching_close(html_text, start)
        article_html = html_text[start:end - 6]

        em = re.search(r'<div[^>]*class="entry"[^>]*>', article_html)
        if not em:
            return article_html
        estart = em.end()
        eend = _find_matching_close(article_html, estart)
        return article_html[estart:eend - 6]

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

