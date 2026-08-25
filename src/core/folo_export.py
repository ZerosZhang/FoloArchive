#!/usr/bin/env python3
"""
Folo 未读文章列表获取脚本（阶段 1）
流程：
1. 从 Folo CLI 获取所有未读文章（view=0 的长文）
2. 提取每篇文章的标题、来源、原文链接、发布时间、Folo summary 等元信息
3. 保存为 JSON 列表到 TempData/「当天日期.json」
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from utils import TEMP_DIR

# 配置
NODE_PATH = "/c/Program Files/nodejs"
SCRIPT_DIR = Path(__file__).parent


def find_bash():
    """在 Windows 上自动查找 Git Bash 路径"""
    from shutil import which
    # 优先检查 PATH 中是否有 bash
    found = which("bash")
    if found:
        return found
    # 常见 Git 安装路径
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    import os
    for p in candidates:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError("找不到 bash，请安装 Git for Windows 或将 bash 加入 PATH")


def run_bash(cmd):
    """运行 bash 命令"""
    bash_path = find_bash()
    env_cmd = f'export PATH="{NODE_PATH}:$PATH" && {cmd}'
    kwargs = {}
    # Windows 下禁止子进程弹出控制台窗口（GUI 模式避免黑窗口闪烁）
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(
        [bash_path, "-c", env_cmd],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        **kwargs
    )
    return result


def extract_json(text):
    """从可能包含 WSL 警告、npx 提示等杂讯的文本中提取 JSON 对象"""
    if not text:
        return None
    # 找到第一个 '{' 和最后一个 '}'
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text.strip()


def run_folo(args):
    """运行 Folo CLI 命令，返回解析后的 JSON 字典"""
    result = run_bash(f"npx --yes folocli@latest {' '.join(args)}")
    stdout_text = result.stdout or ""
    stderr_text = result.stderr or ""

    # 优先从 stdout 提取 JSON（folocli 正常输出到 stdout）
    json_text = extract_json(stdout_text)
    try:
        return json.loads(json_text)
    except (json.JSONDecodeError, TypeError):
        pass

    # 若 stdout 失败，尝试 stderr（某些错误场景）
    json_text = extract_json(stderr_text)
    try:
        return json.loads(json_text)
    except (json.JSONDecodeError, TypeError):
        pass

    # 完全无法解析，打印调试信息
    print(f"[!] Folo CLI 输出无法解析为 JSON")
    print(f"    stdout: {stdout_text[:300].replace(chr(10), ' ')}")
    print(f"    stderr: {stderr_text[:300].replace(chr(10), ' ')}")
    return None


def check_auth():
    """检查 Folo CLI 是否已登录。返回 True/False"""
    result = run_folo(["whoami"])
    if result and result.get("ok"):
        user = result.get("data", {}).get("user", {})
        name = user.get("name") or user.get("email") or "未知用户"
        print(f"  已登录: {name}")
        return True

    error = result.get("error", {}) if result else {}
    code = error.get("code", "UNKNOWN")
    msg = error.get("message", "未知错误")

    print(f"[!] 认证检查失败 ({code}): {msg}")
    print()
    print("请使用以下方式之一登录 Folo CLI：")
    print("  1. 交互式登录（推荐）：")
    print(r"       bash -c 'export PATH=\"/c/Program Files/nodejs:$PATH\" && npx --yes folocli@latest login'")
    print("  2. 使用 Token 登录：")
    print(r"       bash -c 'export PATH=\"/c/Program Files/nodejs:$PATH\" && npx --yes folocli@latest login --token <your-token>'")
    print("  3. 设置环境变量 FOLO_TOKEN（当前终端有效）：")
    print("       set FOLO_TOKEN=<your-token>    (cmd)")
    print("       $env:FOLO_TOKEN='<your-token>' (PowerShell)")
    print()
    return False


def fetch_unread_articles():
    """分页获取所有未读文章（view=0）"""
    all_entries = []
    cursor = None
    page = 1

    print("正在从 Folo 获取未读文章列表...")

    while True:
        args = ["timeline", "--unread-only", "--limit", "100", "--format", "json"]
        if cursor:
            args.extend(["--cursor", cursor])

        result = run_folo(args)
        if not result:
            print(f"  [!] 获取第 {page} 页失败（CLI 无响应或输出异常）")
            break

        if not result.get("ok"):
            error = result.get("error", {})
            code = error.get("code", "UNKNOWN")
            msg = error.get("message", "未知错误")
            print(f"  [!] 获取第 {page} 页失败: [{code}] {msg}")
            break

        entries = result["data"].get("entries", [])
        if not entries:
            break

        # 只保留 view=0 的文章
        articles = [e for e in entries if e.get("view") == 0]
        all_entries.extend(articles)

        has_next = result["data"].get("hasNext", False)
        cursor = result["data"].get("nextCursor")

        print(f"  第 {page} 页: {len(entries)} 条未读, {len(articles)} 篇文章")

        if not has_next or not cursor:
            break
        page += 1

    print(f"共获取 {len(all_entries)} 篇文章")
    return all_entries


def build_article_list(entries):
    """将原始 entry 数据整理为简洁的文章列表"""
    articles = []
    for item in entries:
        # Folo CLI timeline 返回的结构：每个 item 包含 entries 和 feeds
        entry = item.get("entries") or item
        feed = item.get("feeds") or {}

        # 兼容直接是 entry 对象的情况
        if "entries" in item:
            entry = item["entries"]
            feed = item.get("feeds", {})
        else:
            entry = item
            feed = {}

        # 提取 publishedAt，如果没有则尝试 published
        published = entry.get("publishedAt") or entry.get("published", "")

        articles.append({
            "entry_id": entry.get("id", ""),
            "title": entry.get("title", "无标题"),
            "url": entry.get("url", ""),
            "feed_title": feed.get("title", "未知来源"),
            "published": published,
            "summary": entry.get("summary") or "",
        })
    return articles


def sanitize_filename(name):
    """清理文件名中的非法字符"""
    name = re.sub(r'[<>"/\\|?*#]', '', name)
    name = name.strip()
    if len(name) > 80:
        name = name[:80]
    return name


def export_articles(skip_auth_check=False):
    """
    获取未读文章列表，保存 JSON，标记已读。返回 (today, article_list, output_path)
    
    Args:
        skip_auth_check: 若已在外部检查过认证，可设为 True 避免重复检查
    """
    today = datetime.now().strftime("%Y年%m月%d日")
    TEMP_DIR.mkdir(exist_ok=True)

    # 0. 前置认证检查（可选跳过）
    if not skip_auth_check:
        print("[1/3] 检查 Folo CLI 认证状态...")
        if not check_auth():
            return today, [], None
        print()

    # 1. 获取未读文章列表
    step_label = "[2/3]" if not skip_auth_check else "[1/2]"
    print(f"{step_label} 获取未读文章列表...")
    raw_entries = fetch_unread_articles()
    if not raw_entries:
        print("[!] 没有获取到文章，任务结束")
        return today, [], None
    print()

    # 2. 整理为简洁列表
    step_label = "[3/3]" if not skip_auth_check else "[2/2]"
    print(f"{step_label} 整理并保存文章列表...")
    article_list = build_article_list(raw_entries)

    # 3. 保存 JSON
    output_path = TEMP_DIR / f"「{today}」.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(article_list, f, ensure_ascii=False, indent=2)
    print(f"  已保存: {output_path}")

    # 4. 标记所有 view=0 的文章为已读
    print("  标记已读...")
    mark_result = run_folo(["entry", "mark-all-read", "--view", "articles"])
    if mark_result and mark_result.get("ok"):
        print("  标记已读完成")
    else:
        err = mark_result.get("error", {}).get("message", "未知错误") if mark_result else "无响应"
        print(f"  [!] 标记已读失败: {err}")

    return today, article_list, output_path


def main():
    today = datetime.now().strftime("%Y年%m月%d日")
    print(f"今天的日期: {today}")
    print()

    today, article_list, output_path = export_articles()
    if not article_list:
        print("\n没有未读文章或获取失败，任务结束")
        return

    print(f"\n{'='*50}")
    print(f"文章列表获取完成！")
    print(f"共 {len(article_list)} 篇文章")
    print(f"保存路径: {output_path}")
    print(f"{'='*50}")


if __name__ == "__main__":
    from utils import fix_encoding
    fix_encoding()
    main()
