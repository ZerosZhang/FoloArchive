#!/usr/bin/env python3
"""
失败邮件通知模块

归档流程（archive_core）结束后，若失败列表非空，可调用本模块把失败详情
通过 SMTP 邮件发出去。

设计要点：
- 邮件配置是「可选项」。未配置 / 未开启 / 字段缺失时一律返回 None，
  绝不 sys.exit、绝不抛异常，避免影响归档主流程。
- 发信过程中的任何异常都被吞掉，只返回 (False, 错误信息)。
- 只依赖标准库 + 项目内的 utils（取 config.json 路径与编码修复）。
"""

import json
import smtplib
from email.message import EmailMessage
from pathlib import Path

from utils import CONFIG_PATH, fix_encoding

# 归档步骤编号 -> 中文名称（用于整理耗时正文；与 archive_core.STEPS 对齐）
STEP_NAMES = {
    1: "获取未读文章列表",
    2: "下载网页",
    3: "渲染 HTML",
    4: "AI 生成摘要",
}

# 网络超时（秒），避免网络异常时长时间挂起归档流程
SMTP_TIMEOUT = 30


def _first(mail, *keys):
    """按顺序返回第一个非空字段值（用于兼容多种字段命名）"""
    for key in keys:
        value = mail.get(key)
        if value not in (None, "", []):
            return value
    return None


def load_mail_config():
    """读取 config.json 的 mail 段

    容错策略：只要出现以下任一情况就返回 None（调用方据此跳过发信）：
    - 配置文件不存在 / 解析失败
    - 没有 mail 段
    - mail.enabled 显式为假
    - 关键字段缺失或为空

    字段命名兼容两种写法（本项目模板用前者，也接受常见简写）：
    - smtp_host   ← smtp_server / host
    - username    ← sender / from
    - password    ← auth_code / pass
    - from        ← sender / username
    - to          缺省时默认发给发件人自己
    - use_ssl     缺省时按端口推断（465 走 SSL，其余走 STARTTLS）

    注意：这里不能调用 utils.load_config()，它对 api_key/base_url/model
    缺失会 sys.exit(1)，会把「没配邮件」变成「归档退出」。

    返回：规范化后的配置 dict，或 None。
    """
    try:
        config_path = Path(CONFIG_PATH)
        if not config_path.exists():
            return None
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError):
        # 读不到 / 不是合法 JSON，都当作「没配邮件」
        return None

    if not isinstance(config, dict):
        return None

    mail = config.get("mail")
    if not isinstance(mail, dict):
        return None

    # enabled 缺省视为开启（既然写了 mail 段就是想发信），显式 false 才关闭
    if not mail.get("enabled", True):
        return None

    host = _first(mail, "smtp_host", "smtp_server", "host")
    sender = _first(mail, "from", "sender", "username")
    username = _first(mail, "username", "sender", "from")
    password = _first(mail, "password", "auth_code", "pass")
    raw_port = _first(mail, "smtp_port", "port")
    if not (host and sender and username and password and raw_port is not None):
        return None

    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        return None

    # 收件人：to 支持字符串或列表；不填则默认发给自己
    to = mail.get("to")
    if to in (None, "", []):
        to = [sender]
    elif isinstance(to, str):
        to = [to]
    if not isinstance(to, list):
        return None
    recipients = [addr.strip() for addr in to if isinstance(addr, str) and addr.strip()]
    if not recipients:
        return None

    # use_ssl 缺省时按端口推断（465 走 SSL，其余走 STARTTLS）
    use_ssl = mail.get("use_ssl")
    if use_ssl is None:
        use_ssl = port == 465

    return {
        "smtp_host": str(host),
        "smtp_port": port,
        "use_ssl": bool(use_ssl),
        "username": str(username),
        "password": str(password),
        "from": str(sender),
        "to": recipients,
    }


def send_failure_mail(mail_config, subject, body):
    """发送失败通知邮件

    参数：
        mail_config: load_mail_config() 的返回值（或 None）
        subject: 邮件主题
        body: 纯文本正文

    返回：(success: bool, message: str)
        - 成功 -> (True, "")
        - 失败 -> (False, "错误信息")，绝不抛出异常
    """
    try:
        if not mail_config:
            return False, "未配置邮件（mail 段缺失或未启用）"

        recipients = mail_config.get("to") or []
        if isinstance(recipients, str):
            recipients = [recipients]
        recipients = [a for a in recipients if a]
        if not recipients:
            return False, "收件人列表为空"

        host = mail_config["smtp_host"]
        port = int(mail_config["smtp_port"])
        use_ssl = bool(mail_config.get("use_ssl"))
        username = mail_config["username"]
        password = mail_config["password"]
        sender = mail_config["from"]

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)
        msg.set_content(body)

        if use_ssl:
            # 465 隐式 SSL
            with smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT) as server:
                server.login(username, password)
                server.send_message(msg)
        else:
            # 587 明文连接 + STARTTLS 升级
            with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(username, password)
                server.send_message(msg)

        return True, ""
    except Exception as exc:  # noqa: BLE001 - 邮件失败绝不能影响主流程
        return False, f"{type(exc).__name__}: {exc}"


def _format_duration(seconds):
    """把秒数格式化成易读文本（避免依赖 utils.format_duration，保持模块独立）"""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)} 分 {secs:.1f} 秒"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours} 时 {minutes} 分 {secs:.1f} 秒"


def format_failure_report(today, failures, step_times):
    """把失败列表整理成纯文本邮件正文

    参数：
        today: 归档日期字符串，如 "2026年10月01日"
        failures: 失败描述列表（archive_core 返回值的 failures 字段）
        step_times: {步骤号: 秒数}，如 {1: 3.2, 2: 12.5}

    返回：正文 str。
    """
    failures = list(failures or [])
    step_times = step_times or {}

    lines = [
        "Folo 文章归档失败通知",
        "=" * 32,
        f"日期：{today}",
        f"失败总数：{len(failures)} 条",
        "",
        "【失败详情】",
    ]
    if failures:
        for idx, item in enumerate(failures, 1):
            lines.append(f"{idx}. {item}")
    else:
        lines.append("（无）")

    lines.append("")
    lines.append("【各步骤耗时】")
    if step_times:
        # 按步骤号排序，保证邮件里顺序稳定
        for num in sorted(step_times, key=lambda k: (str(k))):
            name = STEP_NAMES.get(num, f"步骤 {num}")
            lines.append(f"步骤 {num} {name}：{_format_duration(step_times[num])}")
        total = sum(v for v in step_times.values() if isinstance(v, (int, float)))
        lines.append(f"合计：{_format_duration(total)}")
    else:
        lines.append("（无耗时记录）")

    lines.append("")
    lines.append("请检查 Folo 认证与网络，并确认 config.json 中的 API 配置是否有效。")

    return "\n".join(lines)


def main():
    """CLI 自测入口：查看当前邮件配置与正文样例（不真的发信）"""
    fix_encoding()
    cfg = load_mail_config()
    if cfg is None:
        print("[i] 未启用或未配置邮件（mail 段缺失 / enabled=false / 字段不全）")
    else:
        masked = dict(cfg)
        masked["password"] = "***"
        print("[i] 邮件配置：", masked)

    demo = format_failure_report(
        "2026年10月01日",
        ["[步骤2] 「36氪」xxx：下载失败（超时）"],
        {1: 3.2, 2: 12.5, 3: 0.8, 4: 41.0},
    )
    print("-" * 32)
    print(demo)


if __name__ == "__main__":
    main()
