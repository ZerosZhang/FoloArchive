#!/usr/bin/env python3
"""Oschina 解析策略"""

import re

from .base import BaseStrategy, register_strategy


@register_strategy
class OschinaStrategy(BaseStrategy):
    """开源中国-软件资讯: SPA，返回提示"""

    name = "开源中国-软件资讯"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "开源中国" in filename_hint:
            return True
        return "oschina.net" in html_text and '<div id="app"' in html_text

    @classmethod
    def extract_body(cls, html_text):
        return "<p>⚠️ 该页面为动态加载，原始 HTML 中无可提取内容。</p>"

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

