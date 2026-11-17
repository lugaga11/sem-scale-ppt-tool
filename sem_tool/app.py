"""One application connecting original-image search to measurement and export."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLocale, QSize, QTimer, QTranslator, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .annotation.ui import AnnotationPanel
from .files import is_supported_image
from .search.ui import SearchPanel
from .theme import CARD_MARGIN, COLORS, SPACING, apply_theme, icon
from .widgets import card, open_file_or_warn, section_heading

WINDOW_TITLE = "SEM 图片工具 · 搜图 / 标注 / 导出"


class LogPanel(QWidget):
    """The export log as a page of its own, with what a user needs after a run."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("logPanel")
        self.output_directory: Path | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(CARD_MARGIN, SPACING, CARD_MARGIN, SPACING)
        frame, layout = card()
        heading = section_heading("运行日志", "log")
        self.copy_button = QPushButton("复制")
        self.copy_button.setToolTip("复制全部日志")
        self.clear_button = QPushButton("清空")
        self.open_button = QPushButton("打开输出目录")
        self.open_button.setIcon(icon("folder"))
        self.open_button.setToolTip("打开最近一次导出所在的文件夹")
        for button in (self.copy_button, self.clear_button, self.open_button):
            heading.addWidget(button)
        layout.addLayout(heading)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setPlaceholderText("导出运行记录会显示在这里。")
        layout.addWidget(self.text, 1)
        root.addWidget(frame)
        self.copy_button.clicked.connect(self.copy_all)
        self.clear_button.clicked.connect(self.clear)
        self.open_button.clicked.connect(self.open_output_directory)
        self._refresh_actions()

    @Slot(str)
    def append(self, message: str) -> None:
        self.text.appendPlainText(message)
        self._refresh_actions()

    @Slot()
    def clear(self) -> None:
        self.text.clear()
        self._refresh_actions()

    @Slot(list)
    def set_outputs(self, paths: list[str]) -> None:
        self.output_directory = Path(paths[0]).parent
        self._refresh_actions()

    def copy_all(self) -> None:
        QApplication.clipboard().setText(self.text.toPlainText())

    def open_output_directory(self) -> None:
        if self.output_directory is not None:
            open_file_or_warn(self, self.output_directory)

    def _refresh_actions(self) -> None:
        has_text = bool(self.text.toPlainText())
        self.copy_button.setEnabled(has_text)
        self.clear_button.setEnabled(has_text)
        self.open_button.setEnabled(self.output_directory is not None)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1180, 760)
        self.setMinimumSize(940, 620)
        self._closing = False
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setIconSize(QSize(17, 17))
        self.tabs.tabBar().setFixedHeight(52)
        self.search_panel = SearchPanel()
        self.annotation_panel = AnnotationPanel()
        self.log_panel = LogPanel()
        self.tabs.addTab(self.annotation_panel, "标注与导出 (0)")
        self.tabs.addTab(self.search_panel, "查找原图")
        self.tabs.addTab(self.log_panel, "运行日志")
        self.tabs.currentChanged.connect(self._refresh_navigation)
        self._refresh_navigation(0)
        self.setCentralWidget(self.tabs)
        self.search_panel.queue_requested.connect(self.add_to_annotation_queue)
        self.search_panel.queue_removal_requested.connect(self.annotation_panel.remove_paths)
        self.annotation_panel.queue_changed.connect(self._update_queue_title)
        self.annotation_panel.queue_changed.connect(self.search_panel.set_queue_paths)
        self.annotation_panel.log_message.connect(self.log_panel.append)
        self.annotation_panel.log_reset.connect(self.log_panel.clear)
        self.annotation_panel.log_requested.connect(self._show_log)
        self.annotation_panel.export_finished.connect(self.log_panel.set_outputs)
        self.search_panel.busy_changed.connect(self._on_busy_changed)
        self.annotation_panel.busy_changed.connect(self._on_busy_changed)

    @Slot(int)
    def _refresh_navigation(self, current: int) -> None:
        for index, name in enumerate(("edit", "search", "log")):
            self.tabs.setTabIcon(index, icon(name, COLORS.primary if index == current else COLORS.muted))

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
            self.search_panel.closing = True
            self.search_panel.cancel_work()
            self.tabs.setEnabled(False)
            self.setWindowTitle("正在结束搜索并等待导出完成，随后自动关闭…")
            event.ignore()
            return
        event.accept()


def install_qt_translation(app: QApplication) -> QTranslator | None:
    """Give Qt's own texts (dialog buttons, the text-field context menu) the interface language."""
    translator = QTranslator()
    directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if not translator.load(QLocale(QLocale.Language.Chinese, QLocale.Country.China), "qtbase", "_", directory):
        return None  # a Qt build shipped without translations keeps its English defaults
    translator.setParent(app)
    app.installTranslator(translator)
    return translator


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("SEMTool")
    install_qt_translation(app)
    apply_theme(app)
    window = MainWindow()
    window.annotation_panel.add_paths([Path(argument) for argument in app.arguments()[1:]])
    window.show()
    return app.exec()
