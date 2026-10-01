# Docker 化 + 飞牛 NAS 定时部署方案

> 目标：把项目打包成一个无人值守的 Docker 容器，在飞牛 NAS 上每天定时跑一次完整归档（fetch → download → convert → summarize）。
>
> 状态：**方案待确认**，确认后再落地代码。

---

## 1. 可行性结论

**中等偏易。** 项目代码几乎全部已经用 `sys.platform` 做了跨平台保护，Linux 容器下可直接运行，不需要重写核心逻辑。

| 风险点 | 位置 | 容器（Linux）下的行为 |
|--------|------|----------------------|
| 终端编码修复 | `src/core/utils.py:37` | 仅 `win32` 下包装，否则走 `reconfigure(line_buffering=True)` |
| 读注册表取代理 | `src/core/utils.py:55-79` | 优先读环境变量，注册表仅 `win32` |
| 找 npx | `src/core/utils.py:93` | `which("npx.cmd") or which("npx")`，Linux 能找到 |
| 隐藏子进程窗口 | `folo_export.py:39` | 包在 `if sys.platform == "win32"` 内 |
| 路径 | 全部 | `pathlib` + `utils.py` 常量（相对项目根，可整体搬到 `/app`） |

依赖极简：只用**标准库 + openai**。

真正需要处理的只有 2 件事：**Folo 认证**、**Node 运行时缓存**。

---

## 2. 目标架构

```
飞牛 NAS
└── Docker 容器（常驻，内置 cron）
    ├── 每天 HH:MM 触发
    │     └── python /app/src/archive.py
    ├── Node 24 + 预热好的 folocli 缓存
    ├── openai（DeepSeek 客户端）
    └── 挂载卷
          ├── /app/result        ← 宿主机共享目录，归档产物（HTML + assets）
          ├── /app/src/config.json（只读）← DeepSeek API 密钥
          └── 环境变量 FOLO_TOKEN ← Folo 登录态
          └── 环境变量 HTTP(S)_PROXY ← 可选代理
```

数据流不变，只是执行位置从 Windows 桌面挪到 NAS 容器。

---

## 3. 新增文件清单

全部放在 git 仓库根（即 `Python/`）：

```
Python/
├── Dockerfile                      # 镜像定义
├── docker-compose.yml              # 飞牛上直接用这个部署
├── .dockerignore                   # 排除 .venv/result/__pycache__
├── .env.example                    # FOLO_TOKEN / 代理 的模板（.env 本身不提交）
├── docker/
│   ├── entrypoint.sh               # 写 crontab + 启动 cron
│   └── crontab                     # 定时规则模板
└── doc/docker部署方案.md            # 本文档
```

**不需要新增/修改**：`src/` 下的核心代码在 Linux 下可原样运行（核心流程零改动，见第 8 节）。

---

## 4. Dockerfile 设计

```dockerfile
# ---------- 阶段 1：取 Node 24 运行时 ----------
FROM node:24-bookworm-slim AS node

# ---------- 阶段 2：运行时镜像 ----------
FROM python:3.12-slim-bookworm

# 时区 + cron（容器内定时必需）
RUN apt-get update && apt-get install -y --no-install-recommends \
        cron tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 从阶段 1 复制 node / npm / npx
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Python 依赖：只装 openai
RUN pip install --no-cache-dir openai

# 预热 folocli，避免每次运行都冷启动下载
ENV NPM_CONFIG_CACHE=/app/.npm \
    NPM_CONFIG_PREFER_OFFLINE=true
RUN npx --yes folocli@latest --help >/dev/null 2>&1 || true

WORKDIR /app
COPY . /app/

RUN chmod +x /app/docker/entrypoint.sh
ENTRYPOINT ["/app/docker/entrypoint.sh"]
```

要点说明：

- **Node 用多阶段复制**：`python:3.12-slim-bookworm` 与 `node:24-bookworm-slim` 同为 Debian bookworm，二进制可直接复用，避免在 Python 镜像里再装一遍 Node。
- **预热 folocli**：`folocli@latest` 每次运行都会尝试联网解析最新版；预热 + `NPM_CONFIG_PREFER_OFFLINE` 让缓存命中时不必等网络。
- **时区**：不设 `TZ` 的话容器默认 UTC，cron 会在你睡觉的时间点跑（差 8 小时），这是最常见的坑。

`.dockerignore`：

```
.venv/
result/
__pycache__/
*.pyc
.git/
doc/
```

---

## 5. 容器入口与定时

`docker/entrypoint.sh`：

```sh
#!/bin/sh
set -e

# 关键：cron 不继承容器环境变量，把当前 env 落盘给它用
printenv | grep -E '^(FOLO_TOKEN|HTTP_PROXY|HTTPS_PROXY|http_proxy|https_proxy|TZ)=' > /etc/environment

# 安装 crontab
crontab /app/docker/crontab

# 可选：容器启动立刻先跑一次，方便验证
if [ "${RUN_ON_START:-0}" = "1" ]; then
    /usr/local/bin/python /app/src/archive.py || true
fi

# 前台运行 cron 作为 PID 1
exec cron -f
```

`docker/crontab`（每天 08:00 执行，可改）：

```
0 8 * * * cd /app && /usr/local/bin/python src/archive.py >> /var/log/archive.log 2>&1
```

要点说明：

- **cron 环境变量坑**：容器 cron 默认拿不到 `FOLO_TOKEN` / 代理变量，必须通过 `/etc/environment`（或写死在 crontab 顶部）注入，否则会报 `UNAUTHORIZED`。
- `cd /app` 保证相对调用路径正确；`src/archive.py` 自身用 `__file__` 计算路径，理论上不依赖 cwd，但显式 cd 更稳。
- 日志走 `>> /var/log/archive.log`，用 `docker exec ... tail -f` 查看；也可在 compose 里改成输出到 stdout 交给 `docker logs`。

---

## 6. docker-compose.yml

```yaml
services:
  folo-archive:
    build: .
    container_name: folo-archive
    restart: unless-stopped
    environment:
      - TZ=Asia/Shanghai
      - FOLO_TOKEN=${FOLO_TOKEN}          # 来自 .env
      - HTTP_PROXY=${HTTP_PROXY:-}        # 可空，空则不注入代理
      - HTTPS_PROXY=${HTTPS_PROXY:-}
      - RUN_ON_START=0                    # 置 1 可启动即跑一次
    volumes:
      - ./result:/app/result              # 归档产物（HTML + assets）
      - ./config/config.json:/app/src/config.json:ro   # DeepSeek 密钥
```

要点说明：

- **代理做成可配置**：`${HTTP_PROXY:-}` 为空时不注入，`utils.get_system_proxy()` 会走环境变量分支，Node 子进程也由 `build_node_env()` 自动带代理。
- **config.json 只读挂载**：避免容器内意外改写；文件本身已在 `.gitignore` 中。
- **result 用绑定挂载**：飞牛上直接指向共享文件夹，你可以在文件管理器里看到每天的 `YYYY年MM月DD日/` 目录。

`.env.example`（复制为 `.env` 后填写，`.env` 不提交）：

```env
# 从 ~/.folo/config.json 的 token 字段复制，注意整体不加多余空格
FOLO_TOKEN=

# 需要代理时才填，不需要就留空
# HTTP_PROXY=http://192.168.1.2:7890
# HTTPS_PROXY=http://192.168.1.2:7890
```

---

## 7. Folo Token 获取与注入

**原理**：folocli 的登录态存在 `~/.folo/config.json`，格式为：

```json
{ "token": "<session-token>", "apiUrl": "https://api.folo.is" }
```

Token 优先级：`--token` 参数 > `FOLO_TOKEN` 环境变量 > 配置文件。所以容器里用 `FOLO_TOKEN` 最干净。

**获取步骤**（在能开浏览器的电脑上，比如你现在的 Windows）：

1. 运行登录（会打开浏览器授权）：
   ```bash
   npx --yes folocli@latest login
   ```
2. 打开 `C:\Users\<你的用户名>\.folo\config.json`。
3. 复制其中 `token` 字段的值，就是需要的 Folo Token。

**补充**：你本机已经登录过，`C:\Users\zeros\.folo\config.json` 里已存在有效 token，可以直接取用，不必重新登录。token 失效时，重新执行一次第 1 步即可。

**验证 token 是否有效**：

```bash
npx --yes folocli@latest whoami        # 返回用户名即正常
```

**安全提醒**：

1. token 是登录凭证，只写进 `.env` 或 NAS 上的 compose 覆盖文件，**不要提交进 git**。
2. token 含 `+ / =` 等字符，写进 `.env` 时不要手工加多余空格。
3. 该 token 泄漏等于账号被接管，建议仅在 NAS 本地保存。

---

## 8. 需要改的代码（最小改动）

**结论：核心流程零改动。** 原先唯一的平台差异是一个专为已废弃来源编写、仅查找 Windows `msedge.exe` 的浏览器兜底抓取路径，Linux 下不可用。本轮清理已将该来源支持整体移除，浏览器兜底函数连同相关来源分支一并删除，`save_webpages.py` 不再包含任何平台专属的抓取逻辑。

**建议但不必须的优化**（可放到落地阶段再决定）：

1. `utils.py` 的 `NPX_PATH` 兜底路径是 Windows 专用，可加一个 POSIX 兜底，纯属稳健性。
2. `load_config()` 目前只读文件，可增加从环境变量读取 DeepSeek 密钥，这样连 `config.json` 都不用挂载。

---

## 9. 飞牛 NAS 部署步骤

1. 在飞牛上准备好存放目录（共享文件夹），例如 `/vol1/1000/folo/`，内含：
   - `result/`（归档输出）
   - `config/config.json`（DeepSeek 密钥）
   - `.env`（Folo Token，可从第 7 节获取）
2. 把 `Dockerfile`、`docker-compose.yml`、`docker/`、`src/`、`doc/` 等同步到 NAS（git clone 或直接拷贝 `Python/` 目录）。
3. 飞牛「Docker」应用里选择 **Compose**，粘贴 `docker-compose.yml` 内容（或指向项目目录），构建并启动。
4. 首次启动建议先设 `RUN_ON_START=1` 跑一次，观察日志确认全流程通过；之后改回 `0`，交给 cron。
5. 确认容器处于运行状态（`restart: unless-stopped` 会随 NAS 重启自动拉起）。

**架构说明**：飞牛主流机型为 x86_64，`python:3.12-slim` 与 `node:24-bookworm-slim` 都是多架构镜像；无论 NAS 是 x86_64 还是 ARM64，在 NAS 上直接构建都会自动匹配，无需额外处理。

---

## 10. 验证清单

1. `docker compose build` 成功，镜像内 `node -v`、`npx --version`、`python -c "import openai"` 均正常。
2. 手动跑一次：`docker exec -it folo-archive python /app/src/archive.py`，4 步全绿。
3. 检查宿主机 `result/YYYY年MM月DD日/` 已生成 `「来源」标题.html`、索引页 `YYYY年MM月DD日.html` 与 `assets/` 图片。
4. 确认 cron 生效：临时把 crontab 改成「每分钟」验证定时链路，再改回每天一次。
5. 断网/代理场景验证：分别在有代理、无代理环境下各跑一次。
6. 重启 NAS 后确认容器自动恢复运行。

---

## 11. 风险与备选

| 风险 | 影响 | 应对 |
|------|------|------|
| Folo Token 过期 | 步骤 1 失败，整体中断 | 日志会打 `UNAUTHORIZED`；重新 login 取 token 更新 `.env` |
| `folocli@latest` 运行期需联网 | 网络异常时 npx 解析失败 | 已预热缓存 + `NPM_CONFIG_PREFER_OFFLINE`；必要时改为固定版本 |
| 定时点 NAS 恰好关机 | 当天漏跑 | NAS 常开则无碍；也可改成 `RUN_ON_START=1` 补跑 |
| 代理不可用 | 下载/API 失败 | 代理变量留空即直连，按实际网络配置 |

**备选方案**：若不想要容器内 cron，可改为「不常驻 + 飞牛计划任务触发」——镜像更轻，由飞牛的定时任务 `docker exec folo-archive python /app/src/archive.py` 触发。当前方案已选择容器内常驻 cron，此备选仅作记录。

---

*文档版本：v1（待确认）*
