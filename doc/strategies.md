# src/strategies/ — 来源解析策略包

## 职责

HTML → Markdown 的来源识别与正文提取策略。每个来源一个独立模块，按注册顺序匹配（**第一个匹配生效**）。

## 结构

```
src/strategies/
├── __init__.py       # 导入全部策略触发注册（顺序即注册顺序）
├── base.py           # BaseStrategy 基类、注册表、resolve_strategy、_find_matching_close
├── suiyan.py         # 碎言
├── sspai.py          # 少数派
├── appinn.py         # 小众软件
├── iplaysoft.py      # 异次元软件世界
├── ruanyifeng.py     # 阮一峰的网络日志
├── sean.py           # seangoedecke.com
├── endler.py         # Matthias Endler
├── echosoar.py       # 偷懒爱好者周刊
├── ftium4.py         # 龙爪槐守望者
├── juya.py           # 橘鸦AI早报
├── baoyu.py          # 宝玉的博客
├── tumeng.py         # 土猛的员外
├── kr36.py           # 36氪
├── oschina.py        # 开源中国-软件资讯
├── down423.py        # 423Down
├── product_hunt.py   # Product Hunt 热门
├── tw93_weekly.py    # 潮流周刊
├── hexo_blog.py      # Hexo 博客（通用）
├── hello_github.py   # HelloGitHub 精选
├── coldwind.py       # 寒流の编程笔记
├── huxiu.py          # 虎嗅
├── zishu.py          # 子舒的博客
├── mobius.py         # 莫比乌斯
└── wang_zhiyong.py   # 王志勇-和平海底
```

## base.py 核心

```python
class BaseStrategy:
    name = "未知来源"          # 来源显示名
    skip_titles = []          # 需跳过的标题列表
    # 需实现：
    detect(html_text, filename_hint) -> bool       # 是否匹配
    extract_body(html_text) -> str                 # 提取正文 HTML
    extract_blocks(body_html) -> [(h2标题或None, 块HTML)]  # 拆分内容块

register_strategy(cls)        # 注册装饰器
resolve_strategy(html_text, filename_hint)         # 按注册顺序返回第一个匹配
_find_matching_close(html, pos)                    # 匹配 </div> 闭合位置
```

## 识别方式

- **优先文件名提示**：`「来源」标题.html` 中的"来源"
- **其次 HTML 内容**：域名 + 结构标记（如 `sspai.com` + `class="article-body"`）
- **顺序敏感**：碎言必须在少数派之前（少数派只查 `article-body` 结构，需域名 `sspai.com` 双重校验避免误判）

## 新增来源步骤

1. 在 `src/strategies/` 新建文件（参考现有策略），定义 `class XxxStrategy(BaseStrategy)`
2. 实现 `detect()`、`extract_body()`、`extract_blocks()`
3. 用 `@register_strategy` 装饰
4. 在 `__init__.py` 中**按正确顺序**导入（宽泛匹配的策略放后面）
