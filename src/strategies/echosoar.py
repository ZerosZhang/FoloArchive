#!/usr/bin/env python3
"""Echosoar 解析策略"""

import re

from .base import BaseStrategy, register_strategy


@register_strategy
class EchosoarStrategy(BaseStrategy):
    """偷懒爱好者周刊 (echosoar.github.io): <article> 标签内为正文"""

    name = "偷懒爱好者周刊"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "偷懒爱好者周刊" in filename_hint:
            return True
        if "echosoar.github.io" in html_text:
            return True
        return False

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r"<article>(.*?)</article>", html_text, re.DOTALL)
        if m:
            return m.group(1)
        return ""

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

