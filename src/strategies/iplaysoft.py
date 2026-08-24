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
    def _clean_promotions(cls, body_html):
        """清理推广横幅、购买按钮、站内推广链接栏、正文内嵌广告与重复标题"""
        # 推广横幅：「新上架优惠」所在段落（仅删单个 p，不跨段落）
        body_html = re.sub(
            r'<p[^>]*>(?:(?!</p>).)*?新上架优惠(?:(?!</p>).)*?</p>',
            '', body_html, flags=re.DOTALL)
        # 购买按钮与提示
        body_html = re.sub(r'<a[^>]*class="[^"]*button[^"]*"[^>]*>.*?</a>', '', body_html, flags=re.DOTALL)
        body_html = re.sub(r'<span[^>]*class="[^"]*button-hint[^"]*"[^>]*>.*?</span>', '', body_html, flags=re.DOTALL)
        # 正文内嵌广告 div（margin:40px auto 50px auto 特征，含广告 script 与自带的 h3 标题）
        while True:
            ad_m = re.search(
                r'(<h3[^>]*>.*?</h3>\s*)?<div[^>]*style="[^"]*margin:40px auto 50px auto[^"]*"[^>]*>',
                body_html, re.DOTALL)
            if not ad_m:
                break
            ad_end = _find_matching_close(body_html, ad_m.end())
            body_html = body_html[:ad_m.start()] + body_html[ad_end:]
        # 空段落（如 <p class="aligncenter"><br></p>）
        body_html = re.sub(r'<p[^>]*>\s*<br\s*/?>\s*</p>', '', body_html, flags=re.DOTALL)
        # 站内推广链接栏：单个 p 内 ≥3 个 &nbsp;&nbsp;|&nbsp;&nbsp; 分隔的推广链接（QSpace 推荐栏等）
        promo = re.compile(
            r'<p[^>]*>'
            r'(?:(?!</p>).)*?'
            r'(?:&nbsp;&nbsp;\|&nbsp;&nbsp;(?:(?!</p>).)*?){3,}'
            r'</p>',
            re.DOTALL)
        body_html = promo.sub('', body_html)
        # 去重相邻相同标题的 h3（广告 div 删除后遗留的空标题）
        body_html = re.sub(r'(<h3[^>]*>)(.*?)(</h3>)\s*<h3[^>]*>\2</h3>', r'\1\2\3', body_html, flags=re.DOTALL)
        return body_html

    @classmethod
    def extract_blocks(cls, body_html):
        body_html = cls._clean_promotions(body_html)
        if body_html.strip():
            return [(None, body_html)]
        return []

