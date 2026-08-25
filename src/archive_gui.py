#!/usr/bin/env python3
"""
Folo 文章归档 GUI 程序
基于 PyQt6 的图形界面，支持步骤选择、实时日志、进度显示、耗时统计
"""

import io
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                              QGroupBox, QCheckBox, QPushButton, QLineEdit, QLabel,
                              QTextEdit, QProgressBar, QGridLayout, QMessageBox, QSizePolicy)
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QFont, QTextCursor, QColor, QTextCharFormat

# 脚本所在目录（src/）
SCRIPT_DIR = Path(__file__).parent
# 功能模块目录 src/core/（utils、archive_core 依赖）
sys.path.insert(0, str(SCRIPT_DIR / "core"))

# Windows 终端编码修复（必须在导入其他脚本之前执行）
# pythonw.exe 下 stdout/stderr 为 None，需跳过包装
from utils import fix_encoding, format_duration
fix_encoding()

from archive_core import STEPS


class LogSignal(QObject):
    """日志信号"""
    message = Signal(str)


class LogStream(io.TextIOBase):
    """将控制台输出重定向到 GUI 日志（线程安全）"""

    def __init__(self, emit_callback):
        super().__init__()
        self._emit = emit_callback
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, text):
        with self._lock:
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                self._emit(line)
        return len(text)

    def flush(self):
        # 不发射无换行的缓冲残留，避免 print(..., end="") 前缀被提前拆行；
        # 残留内容会与后续带换行的输出合并为完整一行
        pass


class ProgressSignal(QObject):
    """进度信号"""
    update = Signal(float, str)


class StatsSignal(QObject):
    """统计信号"""
    update = Signal()


class ControlSignal(QObject):
    """控制信号"""
    stop_timer = Signal()
    enable_start = Signal()
    disable_stop = Signal()


class ArchiveGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Folo 文章归档")
        self.setFixedSize(900, 700)

        # 状态变量
        self.is_running = False
        self.should_stop = False
        self.step_checks = {}  # 步骤复选框
        self.step_times = {}  # 步骤耗时记录
        self.start_time = None
        self._last_log_line = None

        # 信号
        self.log_signal = LogSignal()
        self.log_signal.message.connect(self.append_log)
        self.progress_signal = ProgressSignal()
        self.progress_signal.update.connect(self.update_progress)
        self.stats_signal = StatsSignal()
        self.stats_signal.update.connect(self.update_stats)
        self.control_signal = ControlSignal()
        self.control_signal.stop_timer.connect(self.stop_timer)
        self.control_signal.enable_start.connect(lambda: self.start_btn.setEnabled(True))
        self.control_signal.disable_stop.connect(lambda: self.stop_btn.setEnabled(False))

        # 创建界面
        self.create_widgets()

        # 重定向控制台日志到 GUI（所有 print 统一进日志框，不再显示在控制台）
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        sys.stdout = LogStream(self.log_signal.message.emit)
        sys.stderr = LogStream(self.log_signal.message.emit)

        # 定时器更新统计
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_stats)
        self.timer.setInterval(1000)

    def create_widgets(self):
        """创建界面组件"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)

        # ===== 标题 =====
        title_label = QLabel("Folo 文章归档")
        title_label.setFont(QFont("微软雅黑", 16, QFont.Weight.Bold))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title_label)

        # ===== 顶部水平布局：任务设置（左）+ 耗时统计（右）=====
        top_layout = QHBoxLayout()

        # 任务设置（执行步骤 + 日期）
        settings_group = QGroupBox("任务设置")
        settings_group.setMaximumWidth(560)
        settings_layout = QVBoxLayout(settings_group)

        # 全选/反选按钮
        btn_layout = QHBoxLayout()
        select_all_btn = QPushButton("全选")
        select_all_btn.clicked.connect(self.select_all)
        select_none_btn = QPushButton("全不选")
        select_none_btn.clicked.connect(self.select_none)
        invert_btn = QPushButton("反选")
        invert_btn.clicked.connect(self.invert_selection)
        btn_layout.addWidget(select_all_btn)
        btn_layout.addWidget(select_none_btn)
        btn_layout.addWidget(invert_btn)
        btn_layout.addStretch()
        settings_layout.addLayout(btn_layout)

        # 步骤复选框（默认全选）
        grid_layout = QGridLayout()
        for i, (num, name, desc) in enumerate(STEPS):
            cb = QCheckBox(f"{num}. {desc}")
            cb.setChecked(True)  # 默认全选
            self.step_checks[num] = cb
            grid_layout.addWidget(cb, i // 3, i % 3)
        settings_layout.addLayout(grid_layout)

        # 日期设置
        date_layout = QHBoxLayout()
        date_layout.addWidget(QLabel("日期:"))
        self.date_input = QLineEdit(datetime.now().strftime("%Y年%m月%d日"))
        self.date_input.setMaximumWidth(200)
        date_layout.addWidget(self.date_input)
        today_btn = QPushButton("今天")
        today_btn.clicked.connect(self.set_today)
        date_layout.addWidget(today_btn)
        date_layout.addStretch()
        settings_layout.addLayout(date_layout)

        top_layout.addWidget(settings_group)

        # 耗时统计
        stats_group = QGroupBox("耗时统计")
        stats_layout = QVBoxLayout(stats_group)
        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setFont(QFont("Consolas", 9))
        # 忽略最小尺寸需求，避免撑高 GroupBox，同时拉伸填满内容区
        self.stats_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        stats_layout.addWidget(self.stats_text)

        top_layout.addWidget(stats_group, stretch=1)

        main_layout.addLayout(top_layout)

        # ===== 控制按钮 =====
        control_layout = QHBoxLayout()
        self.start_btn = QPushButton("开始执行")
        self.start_btn.clicked.connect(self.start_archive)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.clicked.connect(self.stop_archive)
        self.stop_btn.setEnabled(False)
        clear_btn = QPushButton("清除日志")
        clear_btn.clicked.connect(self.clear_log)

        control_layout.addWidget(self.start_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addWidget(clear_btn)

        # 进度提示标签放在按钮同一行（右侧）
        self.progress_label = QLabel("就绪")
        control_layout.addWidget(self.progress_label)

        control_layout.addStretch()

        main_layout.addLayout(control_layout)

        # ===== 进度条 =====
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        main_layout.addWidget(self.progress_bar)

        # ===== 日志区域 =====
        log_group = QGroupBox("执行日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_text)

        main_layout.addWidget(log_group, stretch=1)

    def select_all(self):
        """全选"""
        for cb in self.step_checks.values():
            cb.setChecked(True)

    def select_none(self):
        """全不选"""
        for cb in self.step_checks.values():
            cb.setChecked(False)

    def invert_selection(self):
        """反选"""
        for cb in self.step_checks.values():
            cb.setChecked(not cb.isChecked())

    def set_today(self):
        """设置为今天日期"""
        self.date_input.setText(datetime.now().strftime("%Y年%m月%d日"))

    def append_log(self, message):
        """写入日志（与上一条重复的行不显示；失败红色、警告黄色）"""
        if message == self._last_log_line:
            return
        self._last_log_line = message

        color = None
        # 失败标记（✗/❌）或明确的失败状态短语才标红，避免正文含"失败"字样被误判
        if any(mark in message for mark in ("✗", "❌")) or any(
                phrase in message for phrase in ("下载失败", "转换失败", "总结失败", "失败原因")):
            color = QColor("#cc0000")
        elif "⚠️" in message:
            color = QColor("#b8860b")

        # 用显式字符格式插入，避免富文本模式下格式被继承
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        if color:
            fmt.setForeground(color)
        cursor.insertText(message + "\n", fmt)
        self.log_text.setTextCursor(cursor)

    def clear_log(self):
        """清除日志"""
        self._last_log_line = None
        self.log_text.clear()

    def update_progress(self, value, text=""):
        """更新进度条"""
        self.progress_bar.setValue(int(value))
        if text:
            self.progress_label.setText(text)

    def stop_timer(self):
        """停止定时器（在主线程中调用）"""
        self.timer.stop()

    def update_stats(self):
        """更新耗时统计"""
        self.stats_text.clear()

        lines = []
        if self.step_times:
            lines.append("各步骤耗时:")
            for num, duration in sorted(self.step_times.items()):
                _, _, desc = STEPS[num - 1]
                lines.append(f"  步骤 {num} ({desc}): {format_duration(duration)}")
            lines.append(f"总耗时: {format_duration(sum(self.step_times.values()))}")
        else:
            lines.append("各步骤耗时: 等待完成首个步骤")

        if self.start_time and self.is_running:
            elapsed = time.time() - self.start_time
            lines.append(f"当前运行: {format_duration(elapsed)}")

            self.stats_text.setPlainText("\n".join(lines))

    def start_archive(self):
        """开始执行归档"""
        selected_steps = [num for num, cb in self.step_checks.items() if cb.isChecked()]
        if not selected_steps:
            QMessageBox.warning(self, "警告", "请至少选择一个步骤")
            return

        self.is_running = True
        self.should_stop = False
        self.step_times = {}
        self.start_time = time.time()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.clear_log()

        # 在新线程中执行
        thread = threading.Thread(target=self.run_archive, args=(selected_steps,), daemon=True)
        thread.start()

        # 启动定时器
        self.timer.start()

    def stop_archive(self):
        """停止执行"""
        self.should_stop = True
        self.log_signal.message.emit("\n⚠️ 正在停止...")

    def run_archive(self, selected_steps):
        """执行归档任务（在子线程中运行）"""
        try:
            sys.path.insert(0, str(SCRIPT_DIR))
            from archive_core import run_archive as run_core

            today = self.date_input.text()
            result = run_core(
                selected_steps,
                today,
                log=self.log_signal.message.emit,
                on_progress=lambda value, text: self.progress_signal.update.emit(value, text),
                should_stop=lambda: self.should_stop,
            )

            # 更新耗时统计面板
            self.step_times = result["step_times"]
            self.progress_signal.update.emit(100, "完成")

        except Exception as e:
            import traceback
            self.log_signal.message.emit(f"\n❌ 发生错误: {e}")
            self.log_signal.message.emit(traceback.format_exc())

        finally:
            self.is_running = False
            self.control_signal.enable_start.emit()
            self.control_signal.disable_stop.emit()
            self.control_signal.stop_timer.emit()
            self.stats_signal.update.emit()

def main():
    app = QApplication(sys.argv)
    window = ArchiveGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
