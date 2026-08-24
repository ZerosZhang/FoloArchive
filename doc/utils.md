# core/utils.py — 公共工具与路径常量

## 职责

各模块共用的基础工具：Windows 编码修复、config 加载、耗时格式化，以及**目录布局的唯一事实来源**。

## 路径常量

| 常量 | 值 | 说明 |
|------|----|------|
| `PYTHON_ROOT` | `Python/` | 工具包根 |
| `RESULT_DIR` | `Python/result/` | 数据目录 |
| `TEMP_DIR` | `result/temp_data/` | 文章列表 JSON（按日期命名） |
| `DB_PATH` | `result/articles.db` | SQLite 文章索引 |
| `CONFIG_PATH` | `src/config.json` | DeepSeek API 配置 |
| `OUTPUT_BASE_DIR` | `result/` | 文章下载/转换输出目录 |

> 调整目录结构时只需修改本模块，其余脚本通过 `from utils import ...` 引用。

## 函数

### fix_encoding()
Windows 终端 UTF-8 编码修复 + 行缓冲：
- `pythonw.exe` 下 stdout/stderr 为 `None`，跳过包装
- 已是 UTF-8 编码的流跳过（避免重复包装导致退出时异常）
- 非 Windows 平台使用 `reconfigure(line_buffering=True)`

### load_config()
加载 `src/config.json`（API 配置），缺失或缺少 `api_key`/`base_url`/`model` 字段时 `sys.exit(1)`。

### format_duration(seconds)
秒 → 中文可读时长（`65.5` → `1分5.5秒`，超过 1 小时显示 `X时X分X.X秒`）。

## 字节码缓存

模块加载时若未设置 `PYTHONPYCACHEPREFIX`，自动将 `sys.pycache_prefix` 指向 `Python/.venv/pycache`，避免源码目录产生 `__pycache__`。
