#!/usr/bin/env python3
"""Sspai 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class SspaiStrategy(BaseStrategy):
    """少数派: 支持三种结构
       - 派早报: article-body → 多个 post__body__extend__item 块
       - 普通文章: article-body → 单个 article__main__content wangEditor-txt
       - Prime 文章: prime__story__body__wrapper → wangEditor-txt prime__story__body
    """

    name = "少数派"
    skip_titles = ["少数派的近期动态", "你可能错过的好文章"]

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "少数派" in filename_hint:
            return True
        # 域名 + 结构标记双重匹配，避免其他站点的 article-body 被误判
        return ("sspai.com" in html_text and
                ('class="article-body"' in html_text or
                 'class="prime__story__body__wrapper"' in html_text))

    @classmethod
    def extract_body(cls, html_text):
        # 优先提取 article-body
        m = re.search(r'<div[^>]*class="article-body"[^>]*>(.*?)</article>', html_text, re.DOTALL)
        if m:
            return m.group(1)

        # Prime 文章：提取 prime__story__body__wrapper 内内容
        m = re.search(r'<div[^>]*class="prime__story__body__wrapper"[^>]*>', html_text)
        if m:
            start = m.end()
            end = _find_matching_close(html_text, start)
            return html_text[start:end - 6]

        return ""

    @classmethod
    def extract_blocks(cls, body_html):
        blocks = []

        # 模式 A: 派早报 / Matrix 文章 → 多个 post__body__extend__item 块
        pattern = re.compile(r'<div[^>]*class="post__body__extend__item comp__PostBodyExtendItem"[^>]*>')
        if pattern.search(body_html):
            for match in pattern.finditer(body_html):
                start = match.start()
                end = _find_matching_close(body_html, match.end())
                block_html = body_html[start:end]

                h2_match = re.search(r'<h2[^>]*class="post__body__extend__item__title"[^>]*>(.*?)</h2>', block_html, re.DOTALL)
                title = ""
                if h2_match:
                    title = re.sub(r'<[^>]+>', '', h2_match.group(1)).strip()

                we_start_match = re.search(r'<div[^>]*class="post__body__extend__item__content wangEditor-txt"[^>]*>', block_html)
                if we_start_match:
                    we_start = we_start_match.end()
                    we_end = _find_matching_close(block_html, we_start)
                    content_html = block_html[we_start:we_end - 6]
                else:
                    content_html = ""

                if title or content_html.strip():
                    blocks.append((title, content_html))
            return blocks

        # 模式 B: 普通长文 → 单个 article__main__content wangEditor-txt
        we_match = re.search(r'<div[^>]*class="article__main__content wangEditor-txt"[^>]*>', body_html)
        if we_match:
            we_start = we_match.end()
            we_end = _find_matching_close(body_html, we_start)
            content_html = body_html[we_start:we_end - 6]
            if content_html.strip():
                blocks.append((None, content_html))
            return blocks

        # 模式 C: Prime 文章 → wangEditor-txt prime__story__body
        we_match = re.search(r'<article[^>]*class="wangEditor-txt prime__story__body"[^>]*>', body_html)
        if we_match:
            we_start = we_match.end()
            we_end = _find_matching_close(body_html, we_start)
            content_html = body_html[we_start:we_end - 6]
            if content_html.strip():
                blocks.append((None, content_html))
            return blocks

        return blocks

