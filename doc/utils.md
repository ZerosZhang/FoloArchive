# core/utils.py — 公共工具与路径常量

## 职责

各模块共用的基础工具：Windows 编码修复、config 加载、耗时格式化，以及**目录布局的唯一事实来源**。

## 路径常量

| 常量 | 值 | 说明 |
|------|----|------|
| `PYTHON_ROOT` | `Python/` | 工具包根 |
| `RESULT_DIR` | `Python/result/` | 数据目录 |
| `TEMP_DIR` | `result/temp_data/` | 文章列表 JSON（按日期命名） |
| `RAW_DIR` | `result/temp_data/raw/` | 原始下载 HTML（步骤 2 → 步骤 3 的中间产物） |
| `CONFIG_PATH` | `src/config.json` | API / 定时 / 邮件配置 |
| `OUTPUT_BASE_DIR` | `result/` | 成品 HTML 输出目录 |

> 调整目录结构时只需修改本模块，其余脚本通过 `from utils import ...` 引用。

## 函数

### fix_encoding()
Windows 终端 UTF-8 编码修复 + 行缓冲：
- stdout/stderr 为 `None`（无控制台环境）时跳过包装
- 已是 UTF-8 编码的流跳过（避免重复包装导致退出时异常）
- 非 Windows 平台使用 `reconfigure(line_buffering=True)`

### get_system_proxy()
获取系统代理地址：优先环境变量 `HTTP(S)_PROXY`，取不到则读 Windows 注册表系统代理，无则返回 `None`。用于给 Node 子进程注入代理（Node 的 fetch 不读系统代理）。

### find_npx()
定位 `npx.cmd`：PATH 优先，兜底 `C:\Program Files\nodejs\npx.cmd`。

### build_node_env()
在 `os.environ` 基础上注入 `HTTP(S)_PROXY` + `NODE_USE_ENV_PROXY=1`。

### load_config()
加载 `src/config.json`（API 配置），缺失或缺少 `api_key`/`base_url`/`model` 字段时 `sys.exit(1)`。

### format_duration(seconds)
秒 → 中文可读时长（`65.5` → `1分5.5秒`，超过 1 小时显示 `X时X分X.X秒`）。

## 字节码缓存

模块加载时若未设置 `PYTHONPYCACHEPREFIX`，自动将 `sys.pycache_prefix` 指向 `Python/.venv/pycache`，避免源码目录产生 `__pycache__`。
