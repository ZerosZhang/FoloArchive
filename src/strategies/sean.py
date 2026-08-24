#!/usr/bin/env python3
"""Sean 解析策略"""

import re

from .base import BaseStrategy, register_strategy


@register_strategy
class SeanStrategy(BaseStrategy):
    """seangoedecke.com: main > article > section → 单一大块"""

    name = "seangoedecke.com"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "seangoedecke" in filename_hint:
            return True
        return '<main><article>' in html_text and 'seangoedecke.com' in html_text

    @classmethod
    def extract_body(cls, html_text):
        # 提取 <main><article><header>...</header><section> 到 </section></article></main>
        main_start = html_text.find('<main>')
        if main_start == -1:
            return ""

        article_start = html_text.find('<article>', main_start)
        if article_start == -1:
            return ""

        section_start = html_text.find('<section>', article_start)
        if section_start == -1:
            return ""

        section_end = html_text.rfind('</section>', section_start)
        if section_end == -1:
            return ""

        body_html = html_text[section_start + len('<section>'):section_end]
        return body_html

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

