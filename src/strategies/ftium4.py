#!/usr/bin/env python3
"""Ftium4 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class Ftium4Strategy(BaseStrategy):
    """龙爪槐守望者 (ftium4.com): Hexo Icarus 主题
       article.card-content.article → div.content → 单一大块
    """

    name = "龙爪槐守望者"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "龙爪槐守望者" in filename_hint:
            return True
        if "ftium4.com" in html_text and 'class="card-content article"' in html_text:
            return True
        return False

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<article[^>]*class="card-content article"[^>]*>', html_text)
        if not m:
            return ""
        article_start = m.end()
        article_end = html_text.find("</article>", article_start)
        if article_end == -1:
            return ""
        article_html = html_text[article_start:article_end]

        cm = re.search(r'<div[^>]*class="content"[^>]*>', article_html)
        if not cm:
            return ""
        content_start = cm.end()
        content_end = _find_matching_close(article_html, content_start)
        if content_end <= content_start:
            return ""
        body_html = article_html[content_start:content_end - 6]
        return body_html

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

