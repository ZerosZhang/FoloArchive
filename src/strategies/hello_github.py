#!/usr/bin/env python3
"""HelloGithub 解析策略"""

import re
import json

from .base import BaseStrategy, register_strategy


@register_strategy
class HelloGithubStrategy(BaseStrategy):
    """HelloGitHub 精选开源项目: Next.js SPA，从 __NEXT_DATA__ JSON 提取"""

    name = "HelloGithub - 精选开源项目"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "HelloGithub" in filename_hint or "HelloGitHub" in filename_hint:
            return True
        return "hellogithub.com" in html_text and "__NEXT_DATA__" in html_text

    @staticmethod
    def _extract_json(html_text):
        """从 HTML 中提取 __NEXT_DATA__ JSON"""
        start_marker = '"__NEXT_DATA__" type="application/json">'
        idx = html_text.find(start_marker)
        if idx == -1:
            return None
        json_start = idx + len(start_marker)
        json_end = html_text.find("</script>", json_start)
        if json_end == -1:
            return None
        try:
            return json.loads(html_text[json_start:json_end])
        except json.JSONDecodeError:
            return None

    @classmethod
    def extract_body(cls, html_text):
        data = cls._extract_json(html_text)
        if not data:
            return ""

        volume = data.get("props", {}).get("pageProps", {}).get("volume", {})
        if not volume:
            return ""

        categories = volume.get("data", [])
        if not categories:
            return ""

        lines = []
        for cat in categories:
            cat_name = cat.get("category_name", "")
            items = cat.get("items", [])
            if not items:
                continue
            lines.append(f'<h2>{cat_name}</h2>')
            for item in items:
                name = item.get("name", "")
                full_name = item.get("full_name", "")
                desc = item.get("description", "") or item.get("description_en", "")
                github_url = item.get("github_url", "")
                stars = item.get("stars", 0)
                image_url = item.get("image_url", "")

                lines.append(f'<h3>{name}（{full_name}）</h3>')
                if image_url:
                    lines.append(f'<img src="{image_url}" alt="{name}"/>')
                lines.append(f'<p>{desc}</p>')
                lines.append(f'<p>GitHub: <a href="{github_url}">{github_url}</a> | Star: {stars}</p>')
                lines.append("<hr/>")

        return "\n".join(lines)

    @classmethod
    def extract_blocks(cls, body_html):
        # 按 <h2> 切分成块，每个分类一个块
        parts = re.split(r'(<h2>.*?</h2>)', body_html, flags=re.DOTALL)
        blocks = []
        current_title = None
        for part in parts:
            if part.startswith("<h2>"):
                current_title = re.sub(r"<[^>]+>", "", part).strip()
            elif part.strip():
                blocks.append((current_title, part.strip()))
                current_title = None
        if current_title and parts and parts[-1].startswith("<h2>"):
            parts_rest = body_html.split(parts[-1], 1)
            if len(parts_rest) > 1:
                blocks.append((current_title, parts_rest[1].strip()))
        if not blocks and body_html.strip():
            blocks = [(None, body_html)]
        return blocks

