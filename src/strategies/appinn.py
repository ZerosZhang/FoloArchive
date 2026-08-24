#!/usr/bin/env python3
"""Appinn 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class AppinnStrategy(BaseStrategy):
    """小众软件: entry-content → 单一大块"""

    name = "小众软件"
    skip_titles = ["上期回顾"]

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "小众软件" in filename_hint:
            return True
        return 'class="post-single-content box mark-links entry-content"' in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<div[^>]*class="post-single-content box mark-links entry-content"[^>]*>', html_text)
        if not m:
            return ""
        start = m.end()
        end = html_text.find('<!--.post-content box mark-links-->', start)
        if end != -1:
            body_html = html_text[start:end]
        else:
            end = _find_matching_close(html_text, start)
            body_html = html_text[start:end - 6]
        return body_html

    @classmethod
    def extract_blocks(cls, body_html):
        # 删除开头的广告（<style> + 广告div）
        body_html = re.sub(r'<style[^>]*>.*?</style>', '', body_html, count=1, flags=re.DOTALL)
        # 删除以 background:#fcf0ef 开头的广告 div
        body_html = re.sub(r'<div[^>]*style="[^"]*background:#fcf0ef[^"]*"[^>]*>.*?</div>', '', body_html, count=1, flags=re.DOTALL)

        # 过滤 skip_titles
        for skip in cls.skip_titles:
            body_html = re.sub(
                r'<h[12][^>]*>\s*' + re.escape(skip) + r'\s*</h[12]>.*',
                '', body_html, count=1, flags=re.DOTALL | re.IGNORECASE
            )

        if body_html.strip():
            return [(None, body_html)]
        return []

