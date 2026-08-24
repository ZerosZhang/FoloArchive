#!/usr/bin/env python3
"""策略基类与注册表"""

# 2. 策略基类与注册
# =============================================================================

class BaseStrategy:
    """转换策略基类。子类需重写 detect / extract_body / extract_blocks。"""

    name = "未知来源"
    skip_titles = []

    @classmethod
    def detect(cls, html_text: str, filename_hint: str = "") -> bool:
        """检测是否适用此策略。filename_hint 为文件名中的来源提示。"""
        return False

    @classmethod
    def extract_body(cls, html_text: str) -> str:
        """提取正文区域 HTML"""
        return ""

    @classmethod
    def extract_blocks(cls, body_html: str) -> list:
        """
        从正文区域提取内容块。
        返回 [(h2_title_or_none, block_html), ...]
        """
        return []


# ---- 策略注册表 ----
_STRATEGIES = []


def register_strategy(cls):
    """注册策略类（装饰器）"""
    _STRATEGIES.append(cls)
    return cls


def resolve_strategy(html_text: str, filename_hint: str = ""):
    """根据 HTML 内容和文件名提示匹配最佳策略"""
    for strategy in _STRATEGIES:
        if strategy.detect(html_text, filename_hint):
            return strategy
    return None


# =============================================================================


def _find_matching_close(html_text, start_pos):
    """从 start_pos（刚过一个 <div> 的开始标签）开始，找到匹配的 </div>"""
    depth = 1
    pos = start_pos
    while depth > 0 and pos < len(html_text):
        next_open = html_text.find("<div", pos)
        next_close = html_text.find("</div>", pos)
        if next_close == -1:
            break
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 4
        else:
            depth -= 1
            pos = next_close + 6
    return pos
