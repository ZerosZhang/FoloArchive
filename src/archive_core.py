#!/usr/bin/env python3
"""
归档核心流程模块
统一 CLI（archive.py）与网页版（webui.py）的 4 步执行逻辑：
fetch → download → convert（渲染为干净 HTML）→ summarize（注入摘要 + 生成索引页）

调用方通过回调注入日志、进度和停止检查，自身不依赖任何界面框架。
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape as html_escape
from pathlib import Path
from urllib.parse import quote

# 功能模块目录 src/core/（utils、folo_export 等）
sys.path.insert(0, str(Path(__file__).parent / "core"))

from utils import format_duration, TEMP_DIR, RAW_DIR, OUTPUT_BASE_DIR

# 步骤定义：(编号, 名称, 描述)
STEPS = [
    (1, "fetch", "获取 folo 的未读文章列表"),
    (2, "download", "下载网页并优化文件名"),
    (3, "convert", "渲染为 HTML"),
    (4, "summarize", "AI 摘要与索引页生成"),
]


def _load_article_list(today_str):
    """从 TempData 加载之前保存的文章列表"""
    json_path = TEMP_DIR / f"「{today_str}」.json"
    if not json_path.exists():
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _download_step(article_list, today, log, on_progress=None):
    """步骤 2：下载网页到原始 HTML 目录并优化文件名"""
    from save_webpages import download_articles, optimize_titles

    output_dir = RAW_DIR / today
    step_start = time.time()

    def on_download_progress(index, total, article, status, info):
        title = article.get("title", "无标题")[:50]
        if status == "success":
            log(f"[{index}/{total}] {title}... ✓ ({info})")
        elif status == "fail":
            log(f"[{index}/{total}] {title}... ✗ 失败 ({info})")
        elif status == "skip":
            log(f"[{index}/{total}] {title}... - 跳过 ({info})")
        if on_progress:
            on_progress(20 + (index / total) * 30, f"步骤 2: 下载网页 {index}/{total}...")

    # 同一天重跑时重新下载并覆盖旧文件
    success, fail, skipped, failed = download_articles(
        article_list, output_dir, on_progress=on_download_progress, overwrite=True
    )

    log("")
    log(f"✓ 下载完成: {success}/{len(article_list)}")
    log("正在优化文件名...")
    renamed = optimize_titles(output_dir)
    if renamed > 0:
        log(f"✓ 已优化 {renamed} 个文件名")
    else:
        log("✓ 文件名无需优化")

    step_time = time.time() - step_start
    log(f"  ⏱ 耗时: {format_duration(step_time)}")
    return success, fail, skipped, failed, step_time


def _write_index_page(today, index_path, results):
    """生成当日 HTML 汇总索引页：按来源分组，链接到各篇文章并附摘要"""
    from render_html import render_document

    chinese_nums = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十"]

    source_groups = {}
    for r in results:
        source_groups.setdefault(r["source"], []).append(r)

    parts = []
    parts.append('<header class="article-header">')
    parts.append(f'<h1 class="article-title">{html_escape(today)} 归档</h1>')
    parts.append(
        '<div class="article-meta">'
        f'<span class="index-intro">本日共收录 {len(results)} 篇文章，按来源分类整理。</span>'
        "</div>"
    )
    parts.append("</header>")

    sorted_sources = sorted(source_groups.items(), key=lambda x: len(x[1]), reverse=True)
    for i, (source, articles) in enumerate(sorted_sources):
        if not articles:
            continue
        num = chinese_nums[i] if i < len(chinese_nums) else str(i + 1)
        parts.append(
            f'<h2 class="index-source">{num}、{html_escape(source)}（{len(articles)} 篇）</h2>'
        )
        parts.append('<ul class="index-list">')
        for r in articles:
            href = quote(r["filename"])
            display = html_escape(r["display_title"])
            digest = html_escape(r["summary"]).replace("\n", " ")
            parts.append(
                f'<li class="index-item"><a href="./{href}" target="_blank" rel="noopener noreferrer">{display}</a>'
                f'<div class="digest">{digest}</div></li>'
            )
        parts.append("</ul>")

    page = render_document(f"{today} 归档", "\n".join(parts))
    index_path.write_text(page, encoding="utf-8")


def _summarize_step(today, log, should_stop=None):
    """步骤 4：AI 生成文章摘要并生成当日索引页（返回失败明细）"""
    from summarize import (load_config, create_client, scan_html_files,
                           extract_source, process_article, MAX_WORKERS)

    try:
        config = load_config()
    except SystemExit:
        log("⚠️  未找到 config.json 或配置无效，跳过总结步骤")
        return None

    client = create_client(config)
    model = config["model"]

    folder = OUTPUT_BASE_DIR / today
    if not folder.exists():
        log(f"⚠️  文件夹不存在: {folder}，跳过总结步骤")
        return None

    html_files = scan_html_files(folder, today)
    if not html_files:
        log("⚠️  没有找到需要总结的 .html 文件")
        return None

    source_files = {}
    for f in html_files:
        source = extract_source(f.name)
        source_files.setdefault(source, []).append(f)

    total_count = len(html_files)
    log(f"找到 {total_count} 篇文章待处理，来源分布:")
    for source, files in sorted(source_files.items(), key=lambda x: len(x[1]), reverse=True):
        log(f"  - {source}: {len(files)} 篇")
    log("")

    results = []
    processed = 0
    failures = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_file = {
            executor.submit(process_article, client, model, html_file, today): html_file
            for html_file in html_files
        }

        for future in as_completed(future_to_file):
            if should_stop and should_stop():
                executor.shutdown(wait=False)
                break

            result = future.result()
            if result["success"]:
                processed += 1
                log(f"  ✓ [{processed}/{total_count}] {result['filename']}")
                results.append(result)
            else:
                failures.append((result["filename"], result["error"]))
                log(f"  ✗ {result['filename']}: {result['error']}")

    log("\n正在生成索引页...")
    index_path = folder / f"{today}.html"
    _write_index_page(today, index_path, results)

    log(f"已保存: {index_path}")
    return {"processed": processed, "failed": len(failures), "failures": failures, "path": index_path}


def run_archive(selected_steps, today, log, on_progress=None, should_stop=None):
    """执行归档流程

    参数:
        selected_steps: 要执行的步骤编号列表，如 [1, 2, 3, 4]
        today: 日期，如 "2026年08月24日"
        log: 日志回调 log(message)
        on_progress: 进度回调 on_progress(value, text)，可为 None
        should_stop: 停止检查回调 should_stop() -> bool，可为 None

    返回:
        dict 包含 article_list / download / conversion / summary_result /
             step_times / error / failures
        failures 为字符串列表，汇总任一环节的失败原因（供邮件通知判定）
    """
    from folo_export import export_articles
    from render_html import scan_and_convert

    total_steps = len(STEPS)
    log(f"{'=' * 60}")
    log(f"Folo 文章归档 - {today}")
    log(f"{'=' * 60}")
    log("")

    article_list = None
    output_dir = OUTPUT_BASE_DIR / today
    success = fail = skipped = 0
    failed = []
    conversion = None
    summary_result = None
    step_times = {}
    error = None
    failures = []

    def should_run(step_num):
        return step_num in selected_steps

    def stopped():
        return bool(should_stop and should_stop())

    def snapshot():
        return {"article_list": article_list, "download": (success, fail, skipped, failed),
                "conversion": conversion, "summary_result": summary_result,
                "step_times": step_times, "error": error, "failures": failures}

    # ========== 步骤 1: 获取未读文章列表（含认证检查）==========
    if should_run(1):
        if on_progress:
            on_progress(10, "步骤 1: 获取文章列表...")

        # 当天列表已存在则直接复用，避免重复抓取（抓取会把 Folo 未读标记为已读）
        json_path = TEMP_DIR / f"「{today}」.json"
        if json_path.exists():
            log(f"[步骤 1/{total_steps}] 获取未读文章列表 - ⏭️ 跳过（已存在 「{today}」.json）")
            log("  提示：如需重新抓取，请先删除该文件，或用 --date 指定其他日期")
            article_list = _load_article_list(today)
            if not article_list:
                log(f"✗ 无法从已存在的列表文件加载文章: {json_path}")
                failures.append(f"步骤 1 无法加载已存在的列表文件: {json_path}")
                return snapshot()
            log(f"📂 从文件加载了 {len(article_list)} 篇文章")
            log(f"  列表文件: {json_path}")
            log("")
        else:
            log(f"[步骤 1/{total_steps}] 获取未读文章列表...")
            step_start = time.time()
            today_str, article_list, output_path, fetch_failure = export_articles(skip_auth_check=False)

            if fetch_failure:
                failures.append(f"步骤 1 获取文章列表失败: {fetch_failure}")

            if not article_list:
                if fetch_failure:
                    log(f"✗ 获取文章列表失败: {fetch_failure}")
                else:
                    log("✗ 没有未读文章")
                return snapshot()

            step_times[1] = time.time() - step_start
            log(f"✓ 共 {len(article_list)} 篇文章")
            log(f"  列表保存: {output_path}")
            log(f"  ⏱ 耗时: {format_duration(step_times[1])}")
            log("")
    else:
        log(f"[步骤 1/{total_steps}] 获取文章列表 - ⏭️ 跳过")

    if stopped():
        return snapshot()

    # ========== 步骤 2: 下载网页并优化文件名 ==========
    if should_run(2):
        if article_list is None:
            article_list = _load_article_list(today)
            if not article_list:
                log("❌ 无法加载文章列表，请先运行步骤 1")
                error = "无法加载文章列表，请先运行步骤 1"
                failures.append(f"流程错误: {error}")
                return snapshot()
            log(f"📂 从文件加载了 {len(article_list)} 篇文章")

        log(f"[步骤 2/{total_steps}] 下载网页并优化文件名...")
        if on_progress:
            on_progress(20, "步骤 2: 下载网页...")

        success, fail, skipped, failed, step_times[2] = _download_step(
            article_list, today, log, on_progress
        )

        for idx, title, url, reason in failed:
            failures.append(f"步骤 2 下载失败: {title} - {reason}")
    else:
        log(f"[步骤 2/{total_steps}] 下载网页 - ⏭️ 跳过")

    if stopped():
        return snapshot()

    # ========== 步骤 3: 渲染为 HTML ==========
    if should_run(3):
        log("")
        log(f"[步骤 3/{total_steps}] 渲染为 HTML...")
        if on_progress:
            on_progress(60, "步骤 3: 渲染 HTML...")

        step_start = time.time()
        conversion = scan_and_convert(today)
        step_times[3] = time.time() - step_start
        log(f"  ⏱ 耗时: {format_duration(step_times[3])}")

        for name, source in conversion.get("failed", []):
            failures.append(f"步骤 3 渲染失败: [{source}] {name}")
        for name, _ in conversion.get("unknown", []):
            failures.append(f"步骤 3 未识别来源: {name}")
    else:
        log(f"[步骤 3/{total_steps}] 渲染 HTML - ⏭️ 跳过")

    if stopped():
        return snapshot()

    # ========== 步骤 4: AI 摘要与索引页 ==========
    if should_run(4):
        log("")
        log(f"[步骤 4/{total_steps}] AI 摘要与索引页生成...")
        if on_progress:
            on_progress(80, "步骤 4: AI 摘要...")

        step_start = time.time()
        summary_result = _summarize_step(today, log, should_stop)
        step_times[4] = time.time() - step_start
        log(f"  ⏱ 耗时: {format_duration(step_times[4])}")

        if summary_result:
            for filename, err in summary_result.get("failures", []):
                failures.append(f"步骤 4 摘要失败: {filename} - {err}")
    else:
        log(f"[步骤 4/{total_steps}] AI 摘要 - ⏭️ 跳过")

    # ========== 汇总 ==========
    total_time = sum(step_times.values())
    log("")
    log(f"{'=' * 60}")
    log("归档完成")
    log(f"{'=' * 60}")

    if article_list:
        log(f"文章总数: {len(article_list)} 篇")
    if should_run(2):
        log(f"下载成功: {success} 篇")
        if skipped > 0:
            log(f"跳过: {skipped} 篇")
        if fail > 0:
            log(f"下载失败: {fail} 篇")
            log("  失败原因：")
            for idx, title, url, reason in failed:
                log(f"    [{idx}] {title} - {reason}")
    if conversion:
        convert_ok = len(conversion.get("success", []))
        log(f"渲染成功: {convert_ok} 篇")
        convert_failed = conversion.get("failed", [])
        convert_unknown = conversion.get("unknown", [])
        if convert_failed or convert_unknown:
            log(f"渲染失败: {len(convert_failed)} 篇，未知来源: {len(convert_unknown)} 篇")
            log("  失败原因：")
            for name, source in convert_failed:
                log(f"    [{source}] {name} - 已识别来源但渲染失败")
            for name, _ in convert_unknown:
                log(f"    [未知来源] {name} - 未添加解析策略")
    if summary_result:
        log(f"AI 摘要: {summary_result['processed']} 篇")
        summary_failures = summary_result.get("failures", [])
        if summary_failures:
            log(f"AI 摘要失败: {len(summary_failures)} 篇")
            log("  失败原因：")
            for filename, err in summary_failures:
                log(f"    {filename}: {err}")

    if failures:
        log("")
        log(f"⚠️  共 {len(failures)} 项失败，详见上方日志")

    log(f"输出目录: {output_dir.absolute()}")
    log("")
    log(f"总耗时: {format_duration(total_time)}")
    log(f"{'=' * 60}")

    return snapshot()
