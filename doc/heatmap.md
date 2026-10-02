# src/heatmap.py — 归档进展热力图数据扫描

## 职责

纯标准库的数据扫描模块（**不含任何 Qt 控件**）：扫描 `result/` 下的日期目录，按天统计归档篇数，返回 `{日期字符串: 篇数}`。界面（`src/webui.py`）拿到这份数据后自行绘制热力图。

## 接口

| 函数 | 说明 |
|------|------|
| `scan_daily_counts(base_dir)` | 扫描 `base_dir`（通常为 `result/`）下各日期目录的文章篇数，返回 `{日期字符串: 篇数}`，例如 `{"2026年10月01日": 38}` |

调用方会扫描**两个根目录**并合并结果（同日期相加）：`result/` 与 `result/归档/`（更早的历史归档）。

## 统计口径

- 只认形如 `YYYY年MM月DD日` 的子目录，解析不出或非法日期一律跳过
- 统计每个日期目录内的**文章文件数**：`「来源」标题.html`（新格式）与 `「来源」标题.md`（旧归档）都认，让历史归档也能显示
- 同一篇文章同时存在 `.html` / `.md` 时按文件名（去扩展名）**去重**，不重复计数
- **排除**当天索引页 `YYYY年MM月DD日.html/.md`、`assets/` 及其它子目录、非 `「...」...` 命名的文件

## 单独测试扫描逻辑

模块内部用 `from utils import ...` 取 `OUTPUT_BASE_DIR`，直接 `python src/heatmap.py` 会找不到 `utils`，需把 `core/` 加进模块路径：

```bash
# Git Bash
PYTHONPATH="src/core;src" .venv/Scripts/python.exe src/heatmap.py
PYTHONPATH="src/core;src" .venv/Scripts/python.exe src/heatmap.py <目录>
```

## 接入状态

已被网页版界面 `src/webui.py` 接入：`GET /api/heatmap` 合并扫描 `result/` 与 `result/归档/`
得到各日篇数，用于判断「当天有无归档」并生成悬停提示文本；再叠加按天已读状态后，前端用
HTML/CSS 网格绘成三态「阅读状态日历」（灰=无归档、绿=未读、蓝=已读）并支持点击跳转。
