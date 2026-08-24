#!/usr/bin/env python3
"""来源解析策略包：导入全部策略以触发注册
注意：导入顺序即注册顺序，第一个匹配的策略被使用，请保持原有顺序"""

from .base import BaseStrategy, register_strategy, resolve_strategy, _STRATEGIES
from . import suiyan
from . import sspai
from . import appinn
from . import iplaysoft
from . import ruanyifeng
from . import sean
from . import endler
from . import echosoar
from . import ftium4
from . import juya
from . import baoyu
from . import tumeng
from . import kr36
from . import oschina
from . import down423
from . import product_hunt
from . import tw93_weekly
from . import hexo_blog
from . import hello_github
from . import coldwind
from . import huxiu
from . import zishu
from . import mobius
from . import wang_zhiyong
