#!/usr/bin/env python3
"""小众软件官方论坛 (meta.appinn.net, Discourse) 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class AppinnForumStrategy(BaseStrategy):
    """小众软件论坛: Discourse 帖子 → 首楼正文单一大块"""

    name = "小众软件论坛"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        # 仅论坛域名，且必须是 Discourse 帖子结构
        # （www.appinn.com 正文页也会外链 meta.appinn.net，须靠结构标记区分）
        if "meta.appinn.net" not in html_text:
            return False
        return "crawler-post" in html_text or "topic-body" in html_text

    @classmethod
    def extract_body(cls, html_text):
        # Discourse 爬虫视图: <div class='post' itemprop="text">
        m = re.search(
            r'<div[^>]*itemprop=["\']text["\'][^>]*>', html_text)
        if not m:
            # 常规（带 JS）视图: <div class="cooked">
            m = re.search(r'<div[^>]*class=["\'][^"\']*\bcooked\b[^"\']*["\'][^>]*>', html_text)
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
