#!/usr/bin/env python3
"""ProductHunt 解析策略"""

import re

from .base import BaseStrategy, register_strategy


@register_strategy
class ProductHuntStrategy(BaseStrategy):
    """Product Hunt 热门: 只提取产品简介"""

    name = "Product Hunt 热门"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "Product Hunt" in filename_hint:
            return True
        return "producthunt.com" in html_text

    @classmethod
    def extract_body(cls, html_text, json_url=None, json_summary=None):
        # 优先使用 JSON 中的 summary
        desc = json_summary or ""

        # 如果没有 summary，从 HTML 中提取
        if not desc:
            # 提取 og:description（产品简介）
            m = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html_text, re.IGNORECASE)
            if m:
                desc = m.group(1).strip()

        # 提取 meta description 作为备用
        if not desc:
            m = re.search(r'<meta[^>]*name="description"[^>]*content="([^"]*)"', html_text, re.IGNORECASE)
            if m:
                desc = m.group(1).strip()

        # 如果仍然没有描述，尝试从 <title> 提取
        if not desc:
            m = re.search(r'<title>([^<]+)</title>', html_text, re.IGNORECASE)
            if m:
                title_text = m.group(1).strip()
                # 去掉网站名称部分（如 "Qik Office - AI Office - Deploy AI Project Managers - Agentic Rooms"）
                # 尝试取前两部分作为简介（如果第一部分太短）
                parts = title_text.split(' - ')
                if len(parts) >= 3 and len(parts[0]) < 15:
                    # 第一部分太短，取前两部分
                    desc = ' - '.join(parts[:2])
                elif len(parts) >= 2:
                    desc = parts[0]
                else:
                    desc = title_text

        # 生成简洁的 HTML
        parts = []
        if desc:
            parts.append(f'<p>{desc}</p>')
        else:
            parts.append('<p>⚠️ 无法提取产品简介。</p>')

        return "\n".join(parts)

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

