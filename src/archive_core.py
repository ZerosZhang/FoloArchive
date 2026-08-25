#!/usr/bin/env python3
"""
归档核心流程模块
统一 CLI（archive.py）与 GUI（archive_gui.py）的 5 步执行逻辑：
fetch → download → convert → summarize → import

调用方通过回调注入日志、进度和停止检查，自身不依赖任何界面框架。
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 功能模块目录 src/core/（utils、folo_export 等）
sys.path.insert(0, str(Path(__file__).parent / "core"))

from utils import format_duration, TEMP_DIR, OUTPUT_BASE_DIR

# 步骤定义：(编号, 名称, 描述)
STEPS = [
    (1, "fetch", "获取 folo 的未读文章列表"),
    (2, "download", "下载网页并优化文件名"),
    (3, "convert", "Markdown 格式转换"),
    (4, "summarize", "AI 生成内容摘要"),
    (5, "import", "导入文章到数据库"),
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
    """步骤 2：下载网页并优化文件名"""
    from save_webpages import download_articles, optimize_titles

    output_dir = OUTPUT_BASE_DIR / today
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

    success, fail, skipped, failed = download_articles(
        article_list, output_dir, on_progress=on_download_progress
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


def _summarize_step(today, log, should_stop=None):
    """步骤 4：AI 生成文章总结（按来源分组，收集失败原因）"""
    from summarize_md import (load_config, create_client, scan_md_files,
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

    md_files = scan_md_files(folder, today)
    if not md_files:
        log("⚠️  没有找到需要总结的 .md 文件")
        return None

    source_files = {}
    for f in md_files:
        source = extract_source(f.name)
        source_files.setdefault(source, []).append(f)

    total_count = len(md_files)
    log(f"找到 {total_count} 篇文章待处理，来源分布:")
    for source, files in sorted(source_files.items(), key=lambda x: len(x[1]), reverse=True):
        log(f"  - {source}: {len(files)} 篇")
    log("")

    results = []
    processed = 0
    failures = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_file = {
            executor.submit(process_article, client, model, md_file, today): md_file
            for md_file in md_files
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

    log("\n正在写入总结文件...")
    summary_path = folder / f"{today}.md"
    chinese_nums = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十"]

    source_groups = {}
    for r in results:
        source_groups.setdefault(r["source"], []).append(r)

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"本文件汇总了当日收录的 {processed} 篇文章，按来源分类整理，简要说明每篇文章的核心内容。\n\n")

        sorted_sources = sorted(source_groups.items(), key=lambda x: len(x[1]), reverse=True)
        for i, (source, articles) in enumerate(sorted_sources):
            if not articles:
                continue
            num = chinese_nums[i] if i < len(chinese_nums) else str(i + 1)
            f.write(f"## {num}、{source}（{len(articles)} 篇）\n\n")
            for r in articles:
                f.write(f"### [[{r['filename']}|{r['display_title']}]]\n\n{r['summary']}\n")

    log(f"已保存: {summary_path}")
    return {"processed": processed, "failed": len(failures), "failures": failures, "path": summary_path}


def run_archive(selected_steps, today, log, on_progress=None, should_stop=None):
    """执行归档流程

    参数:
        selected_steps: 要执行的步骤编号列表，如 [1, 2, 3, 4, 5]
        today: 日期，如 "2026年08月24日"
        log: 日志回调 log(message)
        on_progress: 进度回调 on_progress(value, text)，可为 None
        should_stop: 停止检查回调 should_stop() -> bool，可为 None

    返回:
        dict 包含 article_list / download / conversion / summary_result / step_times / error
    """
    from folo_export import export_articles
    from html_to_md import scan_and_convert
    from import_to_db import import_articles

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

    def should_run(step_num):
        return step_num in selected_steps

    def stopped():
        return bool(should_stop and should_stop())

    # ========== 步骤 1: 获取未读文章列表（含认证检查）==========
    if should_run(1):
        log(f"[步骤 1/{total_steps}] 获取未读文章列表...")
        if on_progress:
            on_progress(10, "步骤 1: 获取文章列表...")

        step_start = time.time()
        today_str, article_list, output_path = export_articles(skip_auth_check=False)

        if not article_list:
            log("✗ 没有未读文章或获取失败")
            return {"article_list": None, "download": (0, 0, 0, []),
                    "conversion": None, "summary_result": None,
                    "step_times": step_times, "error": error}

        step_times[1] = time.time() - step_start
        log(f"✓ 共 {len(article_list)} 篇文章")
        log(f"  列表保存: {output_path}")
        log(f"  ⏱ 耗时: {format_duration(step_times[1])}")
        log("")
    else:
        log(f"[步骤 1/{total_steps}] 获取文章列表 - ⏭️ 跳过")

    if stopped():
        return {"article_list": article_list, "download": (success, fail, skipped, failed),
                "conversion": conversion, "summary_result": summary_result,
                "step_times": step_times, "error": error}

    # ========== 步骤 2: 下载网页并优化文件名 ==========
    if should_run(2):
        if article_list is None:
            article_list = _load_article_list(today)
            if not article_list:
                log("❌ 无法加载文章列表，请先运行步骤 1")
                error = "无法加载文章列表，请先运行步骤 1"
                return {"article_list": None, "download": (0, 0, 0, []),
                        "conversion": None, "summary_result": None,
                        "step_times": step_times, "error": error}
            log(f"📂 从文件加载了 {len(article_list)} 篇文章")

        log(f"[步骤 2/{total_steps}] 下载网页并优化文件名...")
        if on_progress:
            on_progress(20, "步骤 2: 下载网页...")

        success, fail, skipped, failed, step_times[2] = _download_step(
            article_list, today, log, on_progress
        )
    else:
        log(f"[步骤 2/{total_steps}] 下载网页 - ⏭️ 跳过")

    if stopped():
        return {"article_list": article_list, "download": (success, fail, skipped, failed),
                "conversion": conversion, "summary_result": summary_result,
                "step_times": step_times, "error": error}

    # ========== 步骤 3: HTML → Markdown ==========
    if should_run(3):
        log("")
        log(f"[步骤 3/{total_steps}] HTML → Markdown 转换...")
        if on_progress:
            on_progress(55, "步骤 3: HTML → Markdown...")

        step_start = time.time()
        conversion = scan_and_convert(today)
        step_times[3] = time.time() - step_start
        log(f"  ⏱ 耗时: {format_duration(step_times[3])}")
    else:
        log(f"[步骤 3/{total_steps}] HTML → Markdown - ⏭️ 跳过")

    if stopped():
        return {"article_list": article_list, "download": (success, fail, skipped, failed),
                "conversion": conversion, "summary_result": summary_result,
                "step_times": step_times, "error": error}

    # ========== 步骤 4: AI 总结 ==========
    if should_run(4):
        log("")
        log(f"[步骤 4/{total_steps}] AI 生成文章总结...")
        if on_progress:
            on_progress(75, "步骤 4: AI 总结...")

        step_start = time.time()
        summary_result = _summarize_step(today, log, should_stop)
        step_times[4] = time.time() - step_start
        log(f"  ⏱ 耗时: {format_duration(step_times[4])}")
    else:
        log(f"[步骤 4/{total_steps}] AI 总结 - ⏭️ 跳过")

    if stopped():
        return {"article_list": article_list, "download": (success, fail, skipped, failed),
                "conversion": conversion, "summary_result": summary_result,
                "step_times": step_times, "error": error}

    # ========== 步骤 5: 导入数据库 ==========
    if should_run(5):
        log("")
        log(f"[步骤 5/{total_steps}] 导入文章到数据库...")
        if on_progress:
            on_progress(90, "步骤 5: 导入数据库...")

        step_start = time.time()
        import_articles(today)
        step_times[5] = time.time() - step_start
        log(f"  ⏱ 耗时: {format_duration(step_times[5])}")
    else:
        log(f"[步骤 5/{total_steps}] 导入数据库 - ⏭️ 跳过")

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
        log(f"转换成功: {convert_ok} 篇")
        convert_failed = conversion.get("failed", [])
        convert_unknown = conversion.get("unknown", [])
        if convert_failed or convert_unknown:
            log(f"转换失败: {len(convert_failed)} 篇，未知来源: {len(convert_unknown)} 篇")
            log("  失败原因：")
            for name, source in convert_failed:
                log(f"    [{source}] {name} - 已识别来源但转换失败")
            for name, _ in convert_unknown:
                log(f"    [未知来源] {name} - 未添加解析策略")
    if summary_result:
        log(f"AI 总结: {summary_result['processed']} 篇")
        summary_failures = summary_result.get("failures", [])
        if summary_failures:
            log(f"AI 总结失败: {len(summary_failures)} 篇")
            log("  失败原因：")
            for filename, err in summary_failures:
                log(f"    {filename}: {err}")

    log(f"输出目录: {output_dir.absolute()}")
    log("")
    log(f"总耗时: {format_duration(total_time)}")
    log(f"{'=' * 60}")

    return {"article_list": article_list, "download": (success, fail, skipped, failed),
            "conversion": conversion, "summary_result": summary_result,
            "step_times": step_times, "error": error}
