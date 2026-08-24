#!/usr/bin/env python3
"""Kr36 解析策略"""

import re
import json

from .base import BaseStrategy, register_strategy


@register_strategy
class Kr36Strategy(BaseStrategy):
    """36氪 (36kr.com): window.initialState JSON → widgetContent"""

    name = "36氪"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "36氪" in filename_hint:
            return True
        return "36kr.com" in html_text and "window.initialState" in html_text

    @classmethod
    def extract_body(cls, html_text):
        m = re.search(r'window\.initialState=(\{.*?\})</script>', html_text, re.DOTALL)
        if not m:
            return ""
        try:
            data = json.loads(m.group(1))
            article = data.get("articleDetail", {}).get("articleDetailData", {}).get("data", {})
            return article.get("widgetContent", "")
        except (json.JSONDecodeError, KeyError, AttributeError):
            return ""

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []


