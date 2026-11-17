from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, cast

from PySide6.QtCore import QSize, Qt, QThread, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QDragEnterEvent,
    QDropEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPixmap,
    QShowEvent,
)
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

from ..files import IMAGE_FILE_FILTER, is_supported_image, open_path, path_key
from ..image_widgets import ImageCanvas
from ..settings import AppSettings, default_db_path
from ..theme import CARD_MARGIN, COLORS, ICON_SIZE, SPACING, icon
from ..widgets import (
    SPIN_TEXT_MARGIN,
    ElidedLabel,
    EmptyState,
    PathLineEdit,
    card,
    open_file_or_warn,
    restyle,
    reveal_file_or_warn,
    section_heading,
)
from .engine import SearchResult
from .index import IndexStats, IndexStatus
from .worker import SearchBlocked, SearchWorker, Task


class DropLineEdit(PathLineEdit):
    paste_requested = Signal()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        # Files and folders belong to the panel, which decides between screenshot and gallery and
        # refreshes the index status; only plain text is dropped into the field itself.
        if event.mimeData().hasUrls():
            event.ignore()
        else:
            super().dragEnterEvent(event)

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
    queue_removal_requested = Signal(list)
    busy_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.cancel_event: Optional[threading.Event] = None
        self.thread: Optional[QThread] = None
        self.worker: Optional[SearchWorker] = None
        self.work_started_at: Optional[float] = None
        self.closing = False
        # A background task is the quiet status check: it leaves the controls usable and gives way to any other task.
        self._background = False
        self._follow_up: Optional[Callable[[], None]] = None
        self._status_root: Optional[str] = None
        self._search_request: Optional[tuple[Path, Path, int]] = None
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
        # Files and folders dropped anywhere on the page arrive here; child widgets pass them up.
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(CARD_MARGIN, SPACING, CARD_MARGIN, SPACING)
        root_layout.setSpacing(SPACING)
        root_layout.addWidget(self._build_controls_card())
        root_layout.addWidget(self._build_results_card(), 1)

    def _build_controls_card(self) -> QFrame:
        controls_box, controls_layout = card()
        controls_layout.setSpacing(10)
        form = QGridLayout()
        form.setColumnStretch(1, 1)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        self.query_edit.setPlaceholderText("拖入 / 粘贴截图，或选择图片文件")
        self.root_edit.setPlaceholderText("选择或拖入需要搜索的图库文件夹")

        query_button = QPushButton("选择截图")
        query_button.clicked.connect(self.choose_query)
        paste_button = QPushButton("粘贴截图")
        paste_button.setToolTip("粘贴剪贴板里的截图或文件（Ctrl+V）")
        paste_button.clicked.connect(self.paste_from_clipboard)
        root_button = QPushButton("选择图库")
        root_button.clicked.connect(self.choose_root)
        self.index_button.setToolTip("扫描图库，只处理新增、修改和上次失败的图片")
        for button, name in ((query_button, "image"), (paste_button, "clipboard"), (root_button, "folder"),
                             (self.index_button, "refresh"), (self.search_button, "search")):
            button.setIcon(icon(name, COLORS.surface if button is self.search_button else COLORS.icon))
            button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
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
        self.top_spin.lineEdit().setTextMargins(SPIN_TEXT_MARGIN, 0, 0, 0)
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
        self.index_management_button.setProperty("variant", "menu")
        self.index_management_button.setMenu(index_menu)

        options = QHBoxLayout()
        options.setSpacing(10)
        options.addWidget(self.top_spin)
        options.addWidget(self.index_management_button)
        options.addStretch(1)
        form.addWidget(QLabel("结果数"), 2, 0)
        form.addLayout(options, 2, 1)
        form.addWidget(self.cancel_button, 2, 2)
        form.addWidget(self.search_button, 2, 3)
        controls_layout.addLayout(form)

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
        self.status_label.setProperty("role", "status")
        self.index_status_label.setText("索引状态：未选择图库")
        self.index_status_label.setProperty("role", "muted")
        for label in (self.status_label, self.index_status_label):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)
            controls_layout.addWidget(label)
        return controls_box

    def _build_results_card(self) -> QFrame:
        results_box, results_layout = card()
        heading = section_heading("匹配结果", "search")
        self.result_count = QLabel("0 个候选")
        self.result_count.setProperty("role", "badge")
        heading.insertWidget(2, self.result_count)
        results_layout.addLayout(heading)

        self.results_list.setObjectName("searchResults")
        self.results_list.setSpacing(5)
        self.results_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.results_list.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.results_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        results_layout.addWidget(self.results_list, 1)
        self.results_empty = EmptyState(self.results_list.viewport(), "等待查找原图", "选择截图和图库，搜索对应的原始图片", "search")
        for signal in (self.results_list.model().rowsInserted, self.results_list.model().rowsRemoved,
                       self.results_list.model().modelReset):
            signal.connect(self._update_results_view)
        return results_box

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
        self.results_list.itemSelectionChanged.connect(self._update_result_selection)

    def _update_result_selection(self) -> None:
        for row in range(self.results_list.count()):
            item = self.results_list.item(row)
            widget = self.results_list.itemWidget(item)
            if widget is not None and widget.property("selected") != item.isSelected():
                widget.setProperty("selected", item.isSelected())
                restyle(widget)

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

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._has_supported_drop(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if not self._handle_drop(event):
            super().dropEvent(event)

    def _foreground_busy(self) -> bool:
        return self.thread is not None and not self._background

    def _has_supported_drop(self, event: QDragEnterEvent | QDropEvent) -> bool:
        if self._foreground_busy():
            return False
        for path in self._paths_from_drop(event):
            if path.is_dir() or is_supported_image(path):
                return True
        return False

    def _handle_drop(self, event: QDropEvent) -> bool:
        if self._foreground_busy():
            return False
        for path in self._paths_from_drop(event):
            if path.is_dir():
                self.root_edit.setText(str(path))
                self.remember_gallery_dir()
                self.refresh_index_status()
                self.status_label.setText(f"已拖入图库目录：{path.name}")
                event.acceptProposedAction()
                return True
            if path.is_file() and is_supported_image(path):
                self.query_edit.setText(str(path))
                self.status_label.setText(f"已拖入截图：{path.name}")
                event.acceptProposedAction()
                return True
        self.status_label.setText("拖入的内容不是支持的图片或图库目录")
        return False

    def _paths_from_drop(self, event: QDragEnterEvent | QDropEvent) -> list[Path]:
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
                self.index_status_label.setText("索引状态：尚未检查")
            else:
                self.index_status_label.setText(f"索引状态：上次的图库已不存在（{path}）")

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # The gallery is scanned when the page is first used, never while the application starts.
        if self.root_edit.text().strip() != self._status_root:
            self.refresh_index_status()

    def remember_gallery_dir(self) -> None:
        text = self.root_edit.text().strip()
        if not text:
            return
        path = Path(text)
        if not path.is_dir():
            return
        resolved = str(path.resolve())
        if resolved == self.settings.last_gallery_dir:
            return
        self.settings.last_gallery_dir = resolved
        try:
            self.settings.save()
        except OSError as exc:
            self.status_label.setText(f"无法保存图库设置：{exc}")

    def paste_from_clipboard(self) -> None:
        if self._foreground_busy():
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
                    self.status_label.setText(f"已从剪贴板粘贴文件：{path.name}")
                    return

        if mime.hasImage():
            image = clipboard.image()
            if image.isNull():
                self.status_label.setText("剪贴板图片为空")
                return
            output_path = clipboard_image_path()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            prune_clipboard_images(output_path.parent)
            if not image.save(str(output_path)):
                QMessageBox.warning(self, "粘贴失败", "无法保存剪贴板图片。")
                return
            self.query_edit.setText(str(output_path))
            self.status_label.setText(f"已从剪贴板粘贴截图：{output_path.name}")
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
        self._when_idle(lambda: self._run_task(root, "update", "正在扫描图库…", self._on_index_finished))

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
        self._when_idle(lambda: self._run_task(root, "rebuild", "正在扫描图库…", self._on_index_finished))

    def clear_current_index(self) -> None:
        root = self._validated_root()
        if root is None:
            return
        if not self._confirm_action(
            "清空索引",
            "将删除当前图库的全部索引记录，但不会删除任何图片文件。\n\n继续吗？",
        ):
            return
        self._when_idle(lambda: self._run_task(root, "clear", "正在清空索引…", self._on_clear_finished))

    def prune_missing_index(self) -> None:
        root = self._validated_root()
        if root is None:
            return
        self._when_idle(lambda: self._run_task(root, "prune", "正在清理失效记录…", self._on_prune_finished))

    def vacuum_index_database(self) -> None:
        if not self._confirm_action(
            "压缩索引库",
            "将整理整个 index.sqlite 文件并释放清空索引后留下的空闲空间。\n\n图库很大时可能需要一些时间，继续吗？",
        ):
            return
        self._when_idle(lambda: self._run_task(None, "vacuum", "正在压缩索引库…", self._on_vacuum_finished))

    def start_search(self) -> None:
        root = self._validated_root()
        query = self._validated_query()
        if root is None or query is None:
            return
        self.remember_gallery_dir()
        top_n = self.top_spin.value()
        self._when_idle(lambda: self._run_search(root, query, top_n, precheck=True))

    def _run_task(self, root: Optional[Path], task: Task, message: str, finish_slot) -> None:
        if task in ("update", "rebuild", "clear"):
            self.results_list.clear()  # the shown matches no longer describe the index
        self.status_label.setText(message)
        self._start_worker(SearchWorker(root, self._new_cancel_event(), task), finish_slot)

    def _run_search(self, root: Path, query: Path, top_n: int, *, precheck: bool) -> None:
        self.results_list.clear()
        self.status_label.setText("正在检查索引状态…" if precheck else "正在匹配…")
        self._search_request = (root, query, top_n)
        worker = SearchWorker(root, self._new_cancel_event(), "search", query=query, top_n=top_n, precheck=precheck)
        self._start_worker(worker, self._on_search_finished)

    def _when_idle(self, action: Callable[[], None]) -> None:
        """Run now, or as soon as a background status check has been stopped to make room."""
        if self.thread is None:
            action()
        elif self._background:
            assert self.cancel_event is not None
            self.cancel_event.set()
            self._follow_up = action
        # Otherwise a foreground task owns the disabled controls, so there is nothing to queue.

    def cancel_work(self) -> None:
        self._follow_up = None
        if self.thread is not None and self.cancel_event is not None:
            self.cancel_event.set()
            if not self._background:
                self.status_label.setText("正在取消…")

    def open_index_directory(self) -> None:
        path = default_db_path().parent
        path.mkdir(parents=True, exist_ok=True)
        open_path(path)
        self.status_label.setText(f"已打开索引目录：{path}")

    def _start_worker(self, worker: SearchWorker, finish_slot=None, *, background: bool = False) -> None:
        self._background = background
        if not background:
            self._set_busy(True)
            self.progress.setValue(0)
            self.work_started_at = time.monotonic()
        self.thread = QThread(self)
        self.worker = worker
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        queued = Qt.ConnectionType.QueuedConnection
        worker.status_ready.connect(self._set_index_status, queued)
        if background:
            worker.failed.connect(self._on_status_failed, queued)
        else:
            worker.progress.connect(self._on_progress, queued)
            worker.failed.connect(self._on_failed, queued)
            worker.canceled.connect(self._on_canceled, queued)
        if finish_slot is not None:
            worker.finished.connect(finish_slot, queued)
        worker.done.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        worker.done.connect(worker.deleteLater)
        self.thread.finished.connect(self._cleanup_worker, queued)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    @Slot()
    def _cleanup_worker(self) -> None:
        assert self.thread is not None
        self.thread.wait()
        self.thread = None
        self.worker = None
        self.work_started_at = None
        follow_up, self._follow_up = self._follow_up, None
        if follow_up is not None and not self.closing:
            follow_up()
        # The follow-up may have started the next task; the controls stay locked only for a foreground one.
        self._set_busy(self._foreground_busy())

    @Slot(int, int, str)
    def _on_progress(self, current: int, total: int, message: str) -> None:
        value = int(current * 100 / total) if total else 0
        self.progress.setValue(max(0, min(100, value)))
        self.status_label.setText(self._format_progress_message(current, total, value, message))

    def _format_progress_message(self, current: int, total: int, percent: int, message: str) -> str:
        if total <= 0:
            return shorten_progress_message(message)
        parts = [f"进度 {current}/{total}（{percent}%）"]
        if self.work_started_at and current > 0 and current < total:
            elapsed = time.monotonic() - self.work_started_at
            remaining = elapsed / current * (total - current)
            parts.append(f"预计剩余 {format_duration(remaining)}")
        parts.append(shorten_progress_message(message))
        return " · ".join(parts)

    @Slot(object)
    def _on_index_finished(self, stats: IndexStats) -> None:
        self.progress.setValue(100)
        self.status_label.setText(
            f"索引完成：总数 {stats.total} · 新增/更新 {stats.indexed} · 跳过 {stats.skipped} · 删除 {stats.removed} · 错误 {stats.errors}"
        )
        self.results_empty.title.setText("图库已准备好")
        self.results_empty.detail.setText("选择截图，点击“搜索”查找对应原图")

    @Slot(object)
    def _on_clear_finished(self, removed: int) -> None:
        self.status_label.setText(f"已清空当前图库索引：删除 {removed} 条记录")

    @Slot(object)
    def _on_prune_finished(self, removed: int) -> None:
        self.status_label.setText(f"已清理失效索引记录：删除 {removed} 条")

    @Slot(object)
    def _on_vacuum_finished(self, sizes: tuple[int, int]) -> None:
        before, after = sizes
        self.status_label.setText(f"索引库压缩完成：{format_bytes(before)} → {format_bytes(after)}")

    @Slot(object)
    def _on_search_finished(self, outcome: list[SearchResult] | SearchBlocked) -> None:
        if isinstance(outcome, SearchBlocked):
            # Ask once this worker has been cleaned up, so the dialog never overlaps the thread hand-over.
            assert self._search_request is not None
            root, query, top_n = self._search_request
            status = outcome.status
            self._follow_up = lambda: self._resolve_blocked_search(status, root, query, top_n)
            return
        results = outcome
        self.progress.setValue(100)
        self.status_label.setText(f"搜索完成：{len(results)} 个候选")
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
        if self.worker is not None and self.worker.task != "search":
            self._follow_up = self.refresh_index_status  # a stopped index task leaves the index half updated

    def _resolve_blocked_search(self, status: IndexStatus, root: Path, query: Path, top_n: int) -> None:
        if status.searchable_files == 0:
            self.status_label.setText("当前图库还没有索引")
            self.results_empty.title.setText("图库还没有索引")
            self.results_empty.detail.setText("点击“更新索引”后再搜索")
            QMessageBox.information(self, "需要索引", "当前图库还没有索引，请先更新索引。")
        elif self._confirm_search_with_stale_index(status):
            self._run_search(root, query, top_n, precheck=False)
        else:
            self.status_label.setText("已取消搜索，可以先更新索引")
            self.results_empty.title.setText("搜索未开始")
            self.results_empty.detail.setText("更新索引后重新搜索，或直接使用当前索引")

    def refresh_index_status(self) -> None:
        """Compare the gallery with its index in the background; the label follows when the result arrives."""
        if self._background:
            self.cancel_work()
        text = self.root_edit.text().strip()
        self._status_root = text
        if not text:
            self.index_status_label.setText("索引状态：未选择图库")
            return
        root = Path(text)
        if not root.is_dir():
            self.index_status_label.setText("索引状态：图库目录不存在")
            return
        self._when_idle(lambda: self._start_status_check(root))

    def _start_status_check(self, root: Path) -> None:
        self.index_status_label.setText("索引状态：正在检查…")
        self._start_worker(SearchWorker(root, self._new_cancel_event(), "status"), background=True)

    @Slot(str)
    def _on_status_failed(self, message: str) -> None:
        if self._status_is_current():
            self.index_status_label.setText(f"索引状态：检查失败，{message}")

    def _status_is_current(self) -> bool:
        # Queued results can arrive after the user edits or clears the gallery field.
        return (
            self.worker is not None
            and self.worker.root == Path(self.root_edit.text().strip())
            and self.cancel_event is not None
            and not self.cancel_event.is_set()
        )

    @Slot(object)
    def _set_index_status(self, status: IndexStatus) -> None:
        if not self._status_is_current():
            return
        last_indexed = format_timestamp(status.last_indexed_at)
        parts = [
            f"当前图库：已索引 {status.indexed_files}/{status.total_files} 张",
            f"上次索引：{last_indexed}",
        ]
        if status.needs_update:
            parts.append(
                f"建议更新索引：新增 {status.new_files} · 修改 {status.changed_files} · 删除 {status.removed_files} · 错误 {status.error_files}"
            )
        else:
            parts.append("索引已是最新")
        self.index_status_label.setText("；".join(parts))

    def _confirm_search_with_stale_index(self, status: IndexStatus) -> bool:
        message = (
            "检测到图库可能有变化，建议先更新索引。\n\n"
            f"新增：{status.new_files}\n"
            f"修改：{status.changed_files}\n"
            f"删除：{status.removed_files}\n"
            f"错误：{status.error_files}\n\n"
            "仍要使用当前索引继续搜索吗？"
        )
        return self._confirm_action("建议更新索引", message)

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
        widget = ResultWidget(rank, result)
        widget.set_queued(path_key(result.path) in self._queue_path_keys)
        widget.queue_requested.connect(self.queue_requested)
        widget.queue_removal_requested.connect(self.queue_removal_requested)
        item.setSizeHint(QSize(0, max(widget.minimumHeight(), widget.sizeHint().height())))
        self.results_list.addItem(item)
        self.results_list.setItemWidget(item, widget)

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
    queue_removal_requested = Signal(list)

    def __init__(self, rank: int, result: SearchResult) -> None:
        super().__init__()
        self.result = result
        self._queued = False
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
        confidence = QLabel(f"置信度 · {CONFIDENCE_LABELS[result.confidence]}")
        confidence.setProperty("role", "confidence")
        confidence.setProperty("level", result.confidence)
        confidence.setToolTip("按几何一致的匹配点数量估计：20 个以上为高，8 个以上为中")
        title_row.addWidget(confidence)
        path_label = ElidedLabel(str(result.path))
        path_label.setProperty("role", "muted")
        metrics = QLabel(
            f"匹配内点 {result.inliers} · 有效匹配 {result.good_matches} · 平均距离 {result.avg_distance:.2f}"
        )
        metrics.setProperty("role", "metrics")
        metrics.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_layout.addLayout(title_row)
        text_layout.addWidget(path_label)
        text_layout.addWidget(metrics)
        text_layout.addStretch(1)
        layout.addLayout(text_layout, 1)

        action_layout = QHBoxLayout()
        self.add_button = QPushButton("加入队列")
        self.add_button.setProperty("variant", "soft")
        self.add_button.setIcon(icon("plus", COLORS.primary))
        self.add_button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.add_button.setToolTip("把这张原图加入“标注与导出”的文件队列")
        self.add_button.clicked.connect(self.toggle_queue)
        open_button = QPushButton("打开")
        open_button.setToolTip("用默认程序打开原图")
        reveal_button = QPushButton("定位")
        reveal_button.setToolTip("在文件管理器中显示")
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
        self._queued = queued
        variant = "added" if queued else "soft"
        if self.add_button.property("variant") == variant:
            return
        self.add_button.setText("已加入" if queued else "加入队列")
        self.add_button.setProperty("variant", variant)
        self.add_button.setIcon(icon("check" if queued else "plus", COLORS.success if queued else COLORS.primary))
        self.add_button.setToolTip("再次点击，从标注与导出队列移除" if queued else "把这张原图加入“标注与导出”的文件队列")
        restyle(self.add_button)

    @Slot()
    def toggle_queue(self) -> None:
        signal = self.queue_removal_requested if self._queued else self.queue_requested
        signal.emit([self.result.path])

    def open_file(self) -> None:
        open_file_or_warn(self, self.result.path)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.open_file()
        event.accept()

    def reveal_file(self) -> None:
        reveal_file_or_warn(self, self.result.path)

    def copy_path(self) -> None:
        QApplication.clipboard().setText(str(self.result.path))


CLIPBOARD_KEEP_DAYS = 7
CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}


def clipboard_image_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return default_db_path().parent / "clipboard" / f"clipboard_{stamp}.png"


def prune_clipboard_images(directory: Path) -> None:
    """A pasted screenshot only serves the search it was pasted for; drop the ones from earlier weeks."""
    cutoff = time.time() - CLIPBOARD_KEEP_DAYS * 24 * 3600
    for path in directory.glob("clipboard_*.png"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue  # still open in a viewer, or already removed by another window


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
    return message if len(message) <= max_length else message[: max_length - 1] + "…"


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
