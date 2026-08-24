#!/usr/bin/env python3
"""Huxiu 解析策略"""

import re

from .base import BaseStrategy, register_strategy, _find_matching_close


@register_strategy
class HuxiuStrategy(BaseStrategy):
    """虎嗅: 检测 WAF 验证页面或正常文章页面"""

    name = "虎嗅"

    @classmethod
    def detect(cls, html_text, filename_hint=""):
        if "虎嗅" in filename_hint:
            return True
        return "huxiu.com" in html_text or "aliyun_waf" in html_text

    @classmethod
    def extract_body(cls, html_text):
        # WAF 验证页面
        if "aliyun_waf" in html_text or "Access Verification" in html_text:
            return "<p>⚠️ 该文章被虎嗅 WAF 拦截，需要浏览器验证才能查看。请在浏览器中打开原文链接后重新保存。</p>"

        # 正常文章页面：提取 article__content 区域（正文内容）
        m = re.search(r'<div[^>]*class="article__content"[^>]*>', html_text)
        if m:
            start = m.end()
            end = _find_matching_close(html_text, start)
            if end > start:
                body_html = html_text[start:end - 6]
                # 过滤掉末尾的相关文章推荐、评论区等
                body_html = cls._filter_tail_content(body_html)
                return body_html

        # 备用：尝试 article-detail 容器
        m = re.search(r'<div[^>]*class="article-detail"[^>]*>', html_text)
        if m:
            start = m.end()
            end = _find_matching_close(html_text, start)
            if end > start:
                body_html = html_text[start:end - 6]
                body_html = cls._filter_tail_content(body_html)
                return body_html

        # 备用：尝试 article-content-wrap
        m = re.search(r'<div[^>]*class="article-content-wrap"[^>]*>', html_text)
        if m:
            start = m.end()
            end = _find_matching_close(html_text, start)
            return html_text[start:end - 6]

        return "<p>⚠️ 无法提取虎嗅文章内容。</p>"

    @classmethod
    def _filter_tail_content(cls, body_html):
        """过滤掉文章末尾的无关内容（相关推荐、评论、点赞等）"""
        # 过滤掉 ai-summary 区域
        body_html = re.sub(r'<div[^>]*id="ai-summary"[^>]*>.*?</div>', '', body_html, flags=re.DOTALL)

        # 过滤掉 h1 标题（避免与脚本生成的标题重复）
        body_html = re.sub(r'<h1[^>]*>.*?</h1>', '', body_html, flags=re.DOTALL)

        # 过滤掉相关文章推荐区域
        body_html = re.sub(r'<div[^>]*class="article__related-article-wrap"[^>]*>.*', '', body_html, flags=re.DOTALL)
        body_html = re.sub(r'<div[^>]*class="related-article"[^>]*>.*', '', body_html, flags=re.DOTALL)

        # 过滤掉评论区域
        body_html = re.sub(r'<div[^>]*class="comment[^"]*"[^>]*>.*', '', body_html, flags=re.DOTALL)

        # 过滤掉打赏区域
        body_html = re.sub(r'<div[^>]*class="article__reward-wrap"[^>]*>.*?</div>', '', body_html, flags=re.DOTALL)

        # 过滤掉"问虎嗅嗅"区域
        body_html = re.sub(r'<div[^>]*class="article__xiuxiu-question-wrap"[^>]*>.*?</div>', '', body_html, flags=re.DOTALL)

        # 过滤掉文章末尾的作者信息和版权声明
        body_html = re.sub(r'<div[^>]*class="last-editor-username"[^>]*>.*?</div>', '', body_html, flags=re.DOTALL)
        body_html = re.sub(r'<div[^>]*class="article__reprinted-explain"[^>]*>.*?</div>', '', body_html, flags=re.DOTALL)

        # 过滤掉"读过本文，Ta们还读了"区域
        body_html = re.sub(r'<div[^>]*class="hot-article"[^>]*>.*', '', body_html, flags=re.DOTALL)

        # 过滤掉底部的点赞、收藏、分享按钮
        body_html = re.sub(r'<div[^>]*class="article-detail-bottom"[^>]*>.*', '', body_html, flags=re.DOTALL)

        return body_html

    @classmethod
    def extract_blocks(cls, body_html):
        if body_html.strip():
            return [(None, body_html)]
        return []

