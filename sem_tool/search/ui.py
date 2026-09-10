from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, cast

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QKeyEvent, QKeySequence, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..files import IMAGE_FILE_FILTER, is_supported_image, open_path, path_key, reveal_path
from ..image_widgets import ImageCanvas
from ..settings import AppSettings, default_db_path
from ..theme import icon
from ..widgets import ElidedLabel, EmptyState, PathLineEdit, section_heading
from .engine import SearchResult
from .index import ImageIndex, IndexStats, IndexStatus
from .worker import SearchWorker


class DropLineEdit(PathLineEdit):
    paste_requested = Signal()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.setText(urls[0].toLocalFile())
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.StandardKey.Paste):
            mime = QApplication.clipboard().mimeData()
            if mime.hasImage() or mime.hasUrls():
                self.paste_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)


class SearchPanel(QWidget):
    queue_requested = Signal(list)
    busy_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.cancel_event: Optional[threading.Event] = None
        self.thread: Optional[QThread] = None
        self.worker: Optional[SearchWorker] = None
        self.work_started_at: Optional[float] = None
        self.settings = AppSettings.load()
        self._queue_path_keys: set[str] = set()
        self.query_edit = DropLineEdit()
        self.root_edit = DropLineEdit()
        self.top_spin = QSpinBox()
        self.status_label = QLabel()
        self.index_status_label = QLabel()
        self.progress = QProgressBar()
        self.results_list = QListWidget()
        self.index_button = QPushButton("更新索引")
        self.search_button = QPushButton("搜索")
        self.search_button.setProperty("variant", "primary")
        self.index_management_button = QPushButton("索引管理")
        self.cancel_button = QPushButton("取消")
        self._build_ui()
        self._connect_signals()
        self._install_shortcuts()
        self._restore_settings()

    def _build_ui(self) -> None:
        self.setObjectName("searchPanel")
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 12, 16, 8)
        root_layout.setSpacing(12)
        controls_box = QFrame()
        controls_box.setProperty("panel", True)
        controls_layout = QVBoxLayout(controls_box)
        controls_layout.setContentsMargins(16, 14, 16, 12)
        controls_layout.setSpacing(10)
        root_layout.addWidget(controls_box)

        form = QGridLayout()
        form.setColumnStretch(1, 1)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        self.query_edit.setPlaceholderText("拖入 / 粘贴截图，或选择图片文件")
        self.root_edit.setPlaceholderText("选择或拖入需要搜索的图库文件夹")

        query_button = QPushButton("选择截图")
        query_button.clicked.connect(self.choose_query)
        paste_button = QPushButton("粘贴截图")
        paste_button.clicked.connect(self.paste_from_clipboard)
        root_button = QPushButton("选择图库")
        root_button.clicked.connect(self.choose_root)
        for button, name in ((query_button, "image"), (paste_button, "clipboard"), (root_button, "folder"),
                             (self.index_button, "refresh"), (self.search_button, "search")):
            button.setIcon(icon(name, "#ffffff" if button is self.search_button else "#7a8fac"))
            button.setIconSize(QSize(16, 16))
        self.input_controls = (query_button, paste_button, root_button, self.query_edit, self.root_edit, self.top_spin)
        self._set_uniform_width([
            query_button, paste_button, root_button, self.index_button, self.search_button, self.cancel_button,
        ])
        form.addWidget(QLabel("截图"), 0, 0)
        form.addWidget(self.query_edit, 0, 1)
        form.addWidget(query_button, 0, 2)
        form.addWidget(paste_button, 0, 3)

        form.addWidget(QLabel("图库"), 1, 0)
        form.addWidget(self.root_edit, 1, 1)
        form.addWidget(root_button, 1, 2)
        form.addWidget(self.index_button, 1, 3)

        self.top_spin.setRange(1, 50)
        self.top_spin.setValue(10)
        self.top_spin.setFixedWidth(80)

        controls_layout.addLayout(form)

        index_menu = QMenu(self.index_management_button)
        index_menu.addAction("打开索引目录").triggered.connect(self.open_index_directory)
        index_menu.addSeparator()
        self.index_maintenance_actions = []
        for text, callback in (
            ("重建索引", self.rebuild_current_index),
            ("清理失效", self.prune_missing_index),
            ("压缩索引库", self.vacuum_index_database),
            ("清空索引", self.clear_current_index),
        ):
            action = index_menu.addAction(text)
            action.triggered.connect(callback)
            self.index_maintenance_actions.append(action)
        self.index_management_button.setMenu(index_menu)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addWidget(self.top_spin)
        buttons.addWidget(self.index_management_button)
        buttons.addStretch(1)
        form.addWidget(QLabel("结果数"), 2, 0)
        form.addLayout(buttons, 2, 1)
        form.addWidget(self.cancel_button, 2, 2)
        form.addWidget(self.search_button, 2, 3)

        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.progress.setObjectName("searchProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.setVisible(False)
        controls_layout.addWidget(self.progress)

        self.status_label.setText("选择截图和图库后搜索原图。")
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.setProperty("role", "status")
        self.status_label.setWordWrap(True)
        controls_layout.addWidget(self.status_label)

        self.index_status_label.setText("索引状态: 未选择图库")
        self.index_status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.index_status_label.setProperty("role", "muted")
        self.index_status_label.setWordWrap(True)
        controls_layout.addWidget(self.index_status_label)

        results_box = QFrame()
        results_box.setProperty("panel", True)
        results_layout = QVBoxLayout(results_box)
        results_layout.setContentsMargins(14, 12, 14, 10)
        results_layout.setSpacing(10)
        result_actions = section_heading("匹配结果", "search")
        self.result_count = QLabel("0 个候选")
        self.result_count.setProperty("role", "badge")
        result_actions.insertWidget(2, self.result_count)
        results_layout.addLayout(result_actions)

        self.results_list.setObjectName("searchResults")
        self.results_list.setSpacing(5)
        self.results_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.results_list.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.results_list.setAcceptDrops(True)
        self.results_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        results_layout.addWidget(self.results_list, 1)
        self.results_empty = EmptyState(self.results_list.viewport(), "等待查找原图", "选择截图和图库，搜索对应的原始图片", "search")
        for signal in (self.results_list.model().rowsInserted, self.results_list.model().rowsRemoved,
                       self.results_list.model().modelReset):
            signal.connect(self._update_results_view)
        root_layout.addWidget(results_box, 1)
        self._install_drop_targets()

    def _update_results_view(self, *_args) -> None:
        count = self.results_list.count()
        self.result_count.setText(f"{count} 个候选")
        self.results_empty.setVisible(count == 0)

    def _connect_signals(self) -> None:
        self.index_button.clicked.connect(self.start_index)
        self.search_button.clicked.connect(self.start_search)
        self.cancel_button.clicked.connect(self.cancel_work)
        self.query_edit.paste_requested.connect(self.paste_from_clipboard)
        self.root_edit.paste_requested.connect(self.paste_from_clipboard)
        self.root_edit.editingFinished.connect(self.remember_gallery_dir)
        self.root_edit.editingFinished.connect(self.refresh_index_status)
        self.results_list.itemDoubleClicked.connect(self.open_result_item)
        self.results_list.itemSelectionChanged.connect(self._update_result_selection)

    def _update_result_selection(self) -> None:
        for row in range(self.results_list.count()):
            item = self.results_list.item(row)
            widget = self.results_list.itemWidget(item)
            if widget is not None and widget.property("selected") != item.isSelected():
                widget.setProperty("selected", item.isSelected())
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()

    def _install_shortcuts(self) -> None:
        paste_action = QAction(self)
        paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        paste_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        paste_action.triggered.connect(self.paste_from_clipboard)
        self.addAction(paste_action)

    def _set_uniform_width(self, buttons: list[QPushButton]) -> None:
        width = max(button.sizeHint().width() for button in buttons)
        for button in buttons:
            button.setFixedWidth(width)

    def _install_drop_targets(self) -> None:
        for widget in [
            self.status_label,
            self.index_status_label,
            self.progress,
            self.results_list,
        ]:
            widget.setAcceptDrops(True)
            widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.DragEnter:
            drag_event = cast(QDragEnterEvent, event)
            if self._has_supported_drop(drag_event):
                drag_event.acceptProposedAction()
                return True
        if event.type() == QEvent.Type.Drop:
            if self._handle_drop(cast(QDropEvent, event)):
                return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._has_supported_drop(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if not self._handle_drop(event):
            super().dropEvent(event)

    def _has_supported_drop(self, event: QDragEnterEvent | QDropEvent) -> bool:
        if self.thread is not None:
            return False
        for path in self._paths_from_drop(event):
            if path.is_dir() or is_supported_image(path):
                return True
        return False

    def _handle_drop(self, event: QDropEvent) -> bool:
        if self.thread is not None:
            return False
        for path in self._paths_from_drop(event):
            if path.is_dir():
                self.root_edit.setText(str(path))
                self.remember_gallery_dir()
                self.refresh_index_status()
                self.status_label.setText(f"已拖入图库目录: {path.name}")
                event.acceptProposedAction()
                return True
            if path.is_file() and is_supported_image(path):
                self.query_edit.setText(str(path))
                self.status_label.setText(f"已拖入截图: {path.name}")
                event.acceptProposedAction()
                return True
        self.status_label.setText("拖入的内容不是支持的图片或图库目录")
        return False

    def _paths_from_drop(self, event: QDragEnterEvent | QDropEvent) -> list[Path]:
        if not event.mimeData().hasUrls():
            return []
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                paths.append(Path(local))
        return paths

    def _restore_settings(self) -> None:
        if self.settings.last_gallery_dir:
            path = Path(self.settings.last_gallery_dir)
            if path.is_dir():
                self.root_edit.setText(str(path))
                self.refresh_index_status()
            else:
                self.index_status_label.setText(f"索引状态: 上次图库不存在: {path}")

    def remember_gallery_dir(self) -> None:
        text = self.root_edit.text().strip()
        if not text:
            return
        path = Path(text)
        if not path.is_dir():
            return
        self.settings.last_gallery_dir = str(path.resolve())
        try:
            self.settings.save()
        except OSError as exc:
            self.status_label.setText(f"无法保存图库设置: {exc}")

    def paste_from_clipboard(self) -> None:
        if self.thread is not None:
            return
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                if not url.isLocalFile():
                    continue
                path = Path(url.toLocalFile())
                if path.is_dir():
                    self.root_edit.setText(str(path))
                    self.remember_gallery_dir()
                    self.refresh_index_status()
                    return
                if path.is_file() and is_supported_image(path):
                    self.query_edit.setText(str(path))
                    self.status_label.setText(f"已从剪贴板粘贴文件: {path.name}")
                    return

        if mime.hasImage():
            image = clipboard.image()
            if image.isNull():
                self.status_label.setText("剪贴板图片为空")
                return
            output_path = clipboard_image_path()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if not image.save(str(output_path)):
                QMessageBox.warning(self, "粘贴失败", "无法保存剪贴板图片。")
                return
            self.query_edit.setText(str(output_path))
            self.status_label.setText(f"已从剪贴板粘贴截图: {output_path.name}")
            return

        self.status_label.setText("剪贴板里没有图片或文件路径")

    def choose_query(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择截图",
            "",
            IMAGE_FILE_FILTER,
        )
        if path:
            self.query_edit.setText(path)

    def choose_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择图库")
        if path:
            self.root_edit.setText(path)
            self.remember_gallery_dir()
            self.refresh_index_status()

    def start_index(self) -> None:
        root = self._validated_root()
        if root is None:
            return
        self.remember_gallery_dir()
        self.results_list.clear()
        self._start_worker(SearchWorker(root, self._new_cancel_event()), self._on_index_finished)

    def rebuild_current_index(self) -> None:
        root = self._validated_root()
        if root is None:
            return
        if not self._confirm_action(
            "重建索引",
            "将删除当前图库已有索引并重新扫描全部图片。\n\n继续吗？",
        ):
            return
        self.remember_gallery_dir()
        self.results_list.clear()
        self._start_worker(SearchWorker(root, self._new_cancel_event(), rebuild=True), self._on_index_finished)

    def clear_current_index(self) -> None:
        root = self._validated_root()
        if root is None:
            return
        if not self._confirm_action(
            "清空索引",
            "将删除当前图库的全部索引记录，但不会删除任何图片文件。\n\n继续吗？",
        ):
            return
        removed = ImageIndex().clear(root)
        self.progress.setValue(0)
        self.status_label.setText(f"已清空当前图库索引: 删除 {removed} 条记录")
        self.results_list.clear()
        self.refresh_index_status()

    def prune_missing_index(self) -> None:
        root = self._validated_root()
        if root is None:
            return
        removed = ImageIndex().prune_missing(root)
        self.status_label.setText(f"已清理失效索引记录: 删除 {removed} 条")
        self.refresh_index_status()

    def vacuum_index_database(self) -> None:
        if not self._confirm_action(
            "压缩索引库",
            "将整理整个 index.sqlite 文件并释放清空索引后留下的空闲空间。\n\n图库很大时可能需要一些时间，继续吗？",
        ):
            return
        before = db_file_size()
        ImageIndex().vacuum()
        after = db_file_size()
        self.status_label.setText(f"索引库压缩完成: {format_bytes(before)} -> {format_bytes(after)}")
        self.refresh_index_status()

    def start_search(self) -> None:
        root = self._validated_root()
        query = self._validated_query()
        if root is None or query is None:
            return
        self.remember_gallery_dir()
        index = ImageIndex()
        if index.count(root) == 0:
            QMessageBox.information(self, "需要索引", "当前图库还没有索引，请先更新索引。")
            return
        try:
            status = index.status(root)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "索引状态检查失败", str(exc))
            return
        self._set_index_status(status)
        if status.needs_update and not self._confirm_search_with_stale_index(status):
            return
        self.results_list.clear()
        worker = SearchWorker(root, self._new_cancel_event(), query=query, top_n=self.top_spin.value())
        self._start_worker(worker, self._on_search_finished)

    def cancel_work(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.status_label.setText("正在取消...")

    def open_index_directory(self) -> None:
        path = default_db_path().parent
        path.mkdir(parents=True, exist_ok=True)
        open_path(path)
        self.status_label.setText(f"已打开索引目录: {path}")

    def _start_worker(self, worker: SearchWorker, finish_slot) -> None:
        self._set_busy(True)
        self.progress.setValue(0)
        self.work_started_at = time.monotonic()
        self.thread = QThread(self)
        self.worker = worker
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(finish_slot, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)
        worker.canceled.connect(self._on_canceled, Qt.ConnectionType.QueuedConnection)
        worker.done.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        worker.done.connect(worker.deleteLater)
        self.thread.finished.connect(self._cleanup_worker, Qt.ConnectionType.QueuedConnection)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    @Slot()
    def _cleanup_worker(self) -> None:
        assert self.thread is not None
        self.thread.wait()
        self.thread = None
        self.worker = None
        self.work_started_at = None
        self._set_busy(False)

    @Slot(int, int, str)
    def _on_progress(self, current: int, total: int, message: str) -> None:
        value = int(current * 100 / total) if total else 0
        self.progress.setValue(max(0, min(100, value)))
        self.status_label.setText(self._format_progress_message(current, total, message))

    def _format_progress_message(self, current: int, total: int, message: str) -> str:
        if total <= 0:
            return shorten_progress_message(message)
        percent = int(current * 100 / total)
        parts = [f"进度 {current}/{total} ({percent}%)"]
        if self.work_started_at and current > 0 and current < total:
            elapsed = time.monotonic() - self.work_started_at
            remaining = elapsed / current * (total - current)
            parts.append(f"预计剩余 {format_duration(remaining)}")
        parts.append(shorten_progress_message(message))
        return " | ".join(parts)

    @Slot(object)
    def _on_index_finished(self, stats: IndexStats) -> None:
        self.progress.setValue(100)
        self.status_label.setText(
            f"索引完成: 总数 {stats.total}, 新增/更新 {stats.indexed}, 跳过 {stats.skipped}, 删除 {stats.removed}, 错误 {stats.errors}"
        )
        self.refresh_index_status()
        self.results_empty.title.setText("图库已准备好")
        self.results_empty.detail.setText("选择截图，点击“搜索”查找对应原图")

    @Slot(object)
    def _on_search_finished(self, results: list[SearchResult]) -> None:
        self.progress.setValue(100)
        self.status_label.setText(f"搜索完成: {len(results)} 个候选")
        self.results_empty.title.setText("没有找到匹配原图")
        self.results_empty.detail.setText("可以更换截图，或检查图库索引是否已更新")
        for index, result in enumerate(results, start=1):
            self._add_result(index, result)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.status_label.setText("操作失败")
        self.results_empty.title.setText("操作未完成")
        self.results_empty.detail.setText("检查图片和图库后重试")
        if self.isEnabled():
            QMessageBox.critical(self, "操作失败", message)

    @Slot()
    def _on_canceled(self) -> None:
        self.status_label.setText("已取消")
        self.results_empty.title.setText("操作已取消")
        self.results_empty.detail.setText("可以更换截图或图库后重新开始")

    def refresh_index_status(self) -> None:
        text = self.root_edit.text().strip()
        if not text:
            self.index_status_label.setText("索引状态: 未选择图库")
            return
        root = Path(text)
        if not root.is_dir():
            self.index_status_label.setText("索引状态: 图库目录不存在")
            return
        try:
            status = ImageIndex().status(root)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.index_status_label.setText(f"索引状态: 检查失败: {exc}")
            return
        self._set_index_status(status)

    def _set_index_status(self, status: IndexStatus) -> None:
        last_indexed = format_timestamp(status.last_indexed_at)
        parts = [
            f"当前图库: 已索引 {status.indexed_files}/{status.total_files} 张",
            f"上次索引: {last_indexed}",
        ]
        if status.needs_update:
            parts.append(
                f"建议更新索引: 新增 {status.new_files}, 修改 {status.changed_files}, 删除 {status.removed_files}, 错误 {status.error_files}"
            )
        else:
            parts.append("索引已是最新")
        self.index_status_label.setText("；".join(parts))

    def _confirm_search_with_stale_index(self, status: IndexStatus) -> bool:
        message = (
            "检测到图库可能有变化，建议先更新索引。\n\n"
            f"新增: {status.new_files}\n"
            f"修改: {status.changed_files}\n"
            f"删除: {status.removed_files}\n"
            f"错误: {status.error_files}\n\n"
            "仍要使用当前索引继续搜索吗？"
        )
        answer = QMessageBox.question(
            self,
            "建议更新索引",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _confirm_action(self, title: str, message: str) -> bool:
        answer = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    @Slot(list)
    def set_queue_paths(self, paths: list[Path]) -> None:
        keys = {path_key(path) for path in paths}
        if keys == self._queue_path_keys:
            return
        self._queue_path_keys = keys
        for row in range(self.results_list.count()):
            widget = cast(ResultWidget, self.results_list.itemWidget(self.results_list.item(row)))
            widget.set_queued(path_key(widget.result.path) in keys)

    def _add_result(self, rank: int, result: SearchResult) -> None:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, str(result.path))
        widget = ResultWidget(rank, result)
        widget.set_queued(path_key(result.path) in self._queue_path_keys)
        widget.queue_requested.connect(self.queue_requested)
        item.setSizeHint(QSize(0, max(widget.minimumHeight(), widget.sizeHint().height())))
        self.results_list.addItem(item)
        self.results_list.setItemWidget(item, widget)

    def open_result_item(self, item: QListWidgetItem) -> None:
        path_text = item.data(Qt.ItemDataRole.UserRole)
        if path_text:
            open_path(Path(path_text))

    def _set_busy(self, busy: bool) -> None:
        if busy:
            self.results_empty.title.setText("正在处理图片")
            self.results_empty.detail.setText("匹配结果将在这里显示")
        self.index_button.setEnabled(not busy)
        self.search_button.setEnabled(not busy)
        for action in self.index_maintenance_actions:
            action.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)
        for control in self.input_controls:
            control.setEnabled(not busy)
        self.busy_changed.emit(busy)

    def _new_cancel_event(self) -> threading.Event:
        self.cancel_event = threading.Event()
        return self.cancel_event

    def _validated_root(self) -> Optional[Path]:
        text = self.root_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "缺少图库", "请选择图库目录。")
            return None
        path = Path(text)
        if not path.is_dir():
            QMessageBox.warning(self, "图库无效", "图库目录不存在。")
            return None
        return path

    def _validated_query(self) -> Optional[Path]:
        text = self.query_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "缺少截图", "请选择或拖入一张截图。")
            return None
        path = Path(text)
        if not path.is_file():
            QMessageBox.warning(self, "截图无效", "截图文件不存在。")
            return None
        return path


class ResultWidget(QFrame):
    queue_requested = Signal(list)

    def __init__(self, rank: int, result: SearchResult) -> None:
        super().__init__()
        self.result = result
        self.setObjectName("searchResult")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(144)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 14, 12)
        layout.setSpacing(16)

        preview = ImageCanvas("无预览")
        preview.setFixedSize(128, 96)
        if result.thumbnail:
            pixmap = QPixmap()
            pixmap.loadFromData(result.thumbnail)
            preview.setPixmap(pixmap)
        layout.addWidget(preview)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(7)
        title_row = QHBoxLayout()
        title = ElidedLabel(f"{rank:02d}   {result.path.name}")
        title.setProperty("role", "resultTitle")
        title_row.addWidget(title, 1)
        confidence = QLabel(f"置信度 · {result.confidence_label}")
        confidence.setProperty("role", "confidence")
        confidence.setProperty("level", "high" if result.confidence_label == "高" else "low")
        title_row.addWidget(confidence)
        path_label = ElidedLabel(str(result.path))
        path_label.setProperty("role", "muted")
        metrics = QLabel(
            f"匹配内点 {result.inliers}   ·   有效匹配 {result.good_matches}   ·   平均距离 {result.avg_distance:.2f}"
        )
        metrics.setProperty("role", "metrics")
        metrics.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_layout.addLayout(title_row)
        text_layout.addWidget(path_label)
        text_layout.addWidget(metrics)
        text_layout.addStretch(1)
        layout.addLayout(text_layout, 1)

        action_layout = QHBoxLayout()
        self.add_button = QPushButton("加入列表")
        self.add_button.setProperty("variant", "soft")
        self.add_button.setIcon(icon("plus", "#2878f4"))
        self.add_button.setIconSize(QSize(15, 15))
        self.add_button.clicked.connect(lambda: self.queue_requested.emit([result.path]))
        open_button = QPushButton("打开")
        reveal_button = QPushButton("定位")
        copy_button = QPushButton("复制路径")
        for button in (open_button, reveal_button, copy_button):
            button.setProperty("variant", "toolbar")
        open_button.clicked.connect(self.open_file)
        reveal_button.clicked.connect(self.reveal_file)
        copy_button.clicked.connect(self.copy_path)
        action_layout.addWidget(self.add_button)
        action_layout.addWidget(open_button)
        action_layout.addWidget(reveal_button)
        action_layout.addWidget(copy_button)
        action_layout.addStretch(1)
        text_layout.addLayout(action_layout)
        self.add_button.ensurePolished()
        self.add_button.setFixedSize(self.add_button.sizeHint())

    def set_queued(self, queued: bool) -> None:
        variant = "added" if queued else "soft"
        if self.add_button.property("variant") == variant:
            return
        self.add_button.setText("已加入" if queued else "加入列表")
        self.add_button.setProperty("variant", variant)
        self.add_button.setIcon(icon("check" if queued else "plus", "#226c42" if queued else "#2878f4"))
        self.add_button.setEnabled(not queued)
        self.add_button.style().unpolish(self.add_button)
        self.add_button.style().polish(self.add_button)
        self.add_button.update()

    def open_file(self) -> None:
        open_path(self.result.path)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        open_path(self.result.path)
        event.accept()

    def reveal_file(self) -> None:
        reveal_path(self.result.path)

    def copy_path(self) -> None:
        QApplication.clipboard().setText(str(self.result.path))


def clipboard_image_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return default_db_path().parent / "clipboard" / f"clipboard_{stamp}.png"


def format_timestamp(value: Optional[float]) -> str:
    if value is None:
        return "从未"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {seconds} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def shorten_progress_message(message: str, max_length: int = 48) -> str:
    if len(message) <= max_length:
        return message
    keep = max(8, max_length - 3)
    return message[:keep] + "..."


def db_file_size() -> int:
    path = default_db_path()
    return path.stat().st_size if path.exists() else 0


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
