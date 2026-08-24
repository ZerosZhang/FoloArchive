#!/usr/bin/env python3
"""Iplaysoft 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class IplaysoftStrategy(BaseStrategy):
    """异次元软件世界: entry-content → 单一大块（过滤广告）"""

    name = "异次元软件世界"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "异次元" in filename_hint:
            return True
        if 'class="entry-content"' in html_text and "iplaysoft.com" in html_text:
            return True
        return False

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'<div[^>]*class="entry-content"[^>]*>', html_text)
        if not m:
            return ""
        start = m.end()
        end = _find_matching_close(html_text, start)
        body_html = html_text[start:end - 6]

        # 过滤广告：删除 <style>...</style> 和背景色广告 div
        body_html = re.sub(r'<style[^>]*>.*?</style>', '', body_html, count=1, flags=re.DOTALL)
        body_html = re.sub(r'<div[^>]*style="[^"]*background:[^"]*"[^>]*>.*?</div>', '', body_html, count=1, flags=re.DOTALL)

        # 找到正文开始位置（跳过空白和广告后的第一个内容标签）
        first_content = re.search(r'<(?:p|h[1-6]|figure|img|ul|ol|blockquote|pre)[\s>]', body_html)
        if first_content:
            body_html = body_html[first_content.start():]

        return body_html

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

