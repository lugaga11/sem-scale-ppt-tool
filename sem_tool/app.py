"""One application connecting original-image search to measurement and export."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

from PySide6.QtCore import QSize, QTimer, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QTabWidget,
)

from .annotation.ui import AnnotationPanel
from .files import is_supported_image
from .search.ui import SearchPanel
from .theme import apply_theme, icon


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        apply_theme(cast(QApplication, QApplication.instance()))
        self.setWindowTitle("SEM 图片工具 · 搜图 / 标注 / 导出")
        self.resize(1180, 760)
        self.setMinimumSize(940, 620)
        self._closing = False
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setIconSize(QSize(17, 17))
        self.tabs.tabBar().setFixedHeight(52)
        self.search_panel = SearchPanel()
        self.annotation_panel = AnnotationPanel()
        self.log_panel = QPlainTextEdit()
        self.log_panel.setReadOnly(True)
        self.log_panel.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_panel.setPlaceholderText("导出运行记录会显示在这里。")
        self.tabs.addTab(self.annotation_panel, "标注与导出 (0)")
        self.tabs.addTab(self.search_panel, "查找原图")
        self.tabs.addTab(self.log_panel, "运行日志")
        self.tabs.currentChanged.connect(self._refresh_navigation)
        self._refresh_navigation(0)
        self.setCentralWidget(self.tabs)
        self.search_panel.queue_requested.connect(self.add_to_annotation_queue)
        self.annotation_panel.queue_changed.connect(self._update_queue_title)
        self.annotation_panel.queue_changed.connect(self.search_panel.set_queue_paths)
        self.annotation_panel.log_message.connect(self.log_panel.appendPlainText)
        self.annotation_panel.log_reset.connect(self.log_panel.clear)
        self.annotation_panel.log_requested.connect(self._show_log)
        self.search_panel.busy_changed.connect(self._on_busy_changed)
        self.annotation_panel.busy_changed.connect(self._on_busy_changed)
        self.statusBar().setSizeGripEnabled(False)

    @Slot(int)
    def _refresh_navigation(self, current: int) -> None:
        for index, name in enumerate(("edit", "search", "log")):
            self.tabs.setTabIcon(index, icon(name, "#2878f4" if index == current else "#8a98ad"))
        self.statusBar().showMessage((
            "就绪 · 支持拖入图片与文件夹",
            "双击打开原图 · 加入列表后前往“标注与导出”处理",
            "导出运行记录",
        )[current])

    @Slot(list)
    def _update_queue_title(self, paths: list[Path]) -> None:
        self.tabs.setTabText(self.tabs.indexOf(self.annotation_panel), f"标注与导出 ({len(paths)})")

    @Slot()
    def _show_log(self) -> None:
        self.tabs.setCurrentWidget(self.log_panel)

    @Slot(list)
    def add_to_annotation_queue(self, paths: list[Path]) -> None:
        valid = [path for path in paths if path.is_file() and is_supported_image(path)]
        missing = [path for path in paths if path not in valid]
        if valid:
            self.annotation_panel.add_paths(valid)
            self.annotation_panel.select_path(valid[0])
            self.statusBar().showMessage(
                f"已加入 {len(valid)} 张原图 · 前往“标注与导出”处理"
            )
        if missing:
            QMessageBox.warning(
                self, "原图不可用", "以下文件已移走或不是支持的图片，请更新索引：\n" + "\n".join(map(str, missing))
            )

    @Slot(bool)
    def _on_busy_changed(self, _busy: bool) -> None:
        if self._closing and self.search_panel.thread is None and self.annotation_panel.thread is None:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.search_panel.thread is not None or self.annotation_panel.thread is not None:
            self._closing = True
            self.annotation_panel.closing = True
            self.search_panel.cancel_work()
            self.tabs.setEnabled(False)
            self.statusBar().showMessage("正在结束搜索并等待导出完成，随后自动关闭…")
            event.ignore()
            return
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("SEMTool")
    window = MainWindow()
    if len(sys.argv) > 1:
        window.annotation_panel.add_paths([Path(argument) for argument in sys.argv[1:]])
    window.show()
    return app.exec()
