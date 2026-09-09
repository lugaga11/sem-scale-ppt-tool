from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QWidget,
)

from .core import DEFAULTS

COL_GENERATE = 0
COL_NAME = 1
COL_MEASUREMENT = 2


class FileQueueWidget(QTableWidget):
    QUEUE_ROW_MIME = "application/x-sem-scale-queue-rows"

    paths_dropped = Signal(list)
    rows_reorder_requested = Signal(list, int)

    def __init__(self) -> None:
        super().__init__(0, 3)
        self.setHorizontalHeaderLabels(["生成", "文件", "测量"])

        self.drag_start_position: QPoint | None = None
        self.drag_start_row = -1
        self.drop_indicator_row: int | None = None
        self.drop_indicator_phase = 0
        self.drop_indicator_timer = QTimer(self)
        self.drop_indicator_timer.setInterval(90)
        self.drop_indicator_timer.timeout.connect(self.advance_drop_indicator)

        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDragDropOverwriteMode(False)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        self.setAutoScroll(True)
        self.setAutoScrollMargin(36)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setMinimumHeight(DEFAULTS["queue_min_height"])
        self.verticalHeader().setVisible(False)

        header = self.horizontalHeader()
        header.setSectionResizeMode(COL_GENERATE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_MEASUREMENT, QHeaderView.ResizeMode.ResizeToContents)
        self.setStyleSheet(
            """
            QTableWidget {
                border: 1px solid #7aa7ff;
                border-radius: 8px;
            }
            QTableWidget::item {
                padding: 4px;
            }
            """
        )

    def event_position(self, event) -> QPoint:
        return event.position().toPoint()

    def is_queue_drag(self, event) -> bool:
        return event.source() is self and event.mimeData().hasFormat(self.QUEUE_ROW_MIME)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            self.clear_drop_indicator()
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
        elif self.is_queue_drag(event):
            self.set_drop_indicator(self.drop_row(event))
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            self.clear_drop_indicator()
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
        elif self.is_queue_drag(event):
            self.set_drop_indicator(self.drop_row(event))
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.clear_drop_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            self.clear_drop_indicator()
            paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
            self.paths_dropped.emit(paths)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
        elif self.is_queue_drag(event):
            row = self.mime_row(event.mimeData())
            if row is not None:
                drop_row = self.drop_row(event)
                self.clear_drop_indicator()
                self.rows_reorder_requested.emit([row], drop_row)
                event.setDropAction(Qt.DropAction.MoveAction)
                event.accept()
            else:
                self.clear_drop_indicator()
                event.ignore()
        else:
            self.clear_drop_indicator()
            event.ignore()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.drop_indicator_row is None:
            return

        y = self.drop_indicator_y(self.drop_indicator_row)
        if y is None:
            return
        pulse = 40 + abs((self.drop_indicator_phase % 12) - 6) * 8
        color = QColor(68, 122, 255, min(235, 150 + pulse))
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        left = 8
        right = self.viewport().width() - 8
        painter.drawLine(left, y, right, y)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPoint(left, y), 4, 4)
        painter.drawEllipse(QPoint(right, y), 4, 4)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            self.drag_start_position = None
            self.drag_start_row = -1
            return
        position = self.event_position(event)
        index = self.indexAt(position)
        self.drag_start_position = position
        self.drag_start_row = index.row() if index.isValid() else -1

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        if self.drag_start_position is None or self.drag_start_row < 0:
            super().mouseMoveEvent(event)
            return

        position = self.event_position(event)
        if (position - self.drag_start_position).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        self.start_queue_row_drag(self.drag_start_row)
        self.drag_start_position = None
        self.drag_start_row = -1

    def mouseReleaseEvent(self, event) -> None:
        self.drag_start_position = None
        self.drag_start_row = -1
        super().mouseReleaseEvent(event)

    def start_queue_row_drag(self, row: int) -> None:
        if row < 0 or row >= self.rowCount():
            return
        mime_data = QMimeData()
        mime_data.setData(self.QUEUE_ROW_MIME, str(row).encode("ascii"))
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        preview = self.drag_preview(row)
        if not preview.isNull():
            drag.setPixmap(preview)
            drag.setHotSpot(QPoint(18, min(24, max(1, preview.height() // 2))))
        drag.exec(Qt.DropAction.MoveAction)
        self.clear_drop_indicator()

    def mime_row(self, mime_data: QMimeData) -> int | None:
        try:
            encoded_row = mime_data.data(self.QUEUE_ROW_MIME)
            text = bytes(encoded_row.data()).decode("ascii")
            return int(text)
        except (UnicodeDecodeError, ValueError):
            return None

    def drop_row(self, event) -> int:
        position = self.event_position(event)
        index = self.indexAt(position)
        if not index.isValid():
            return self.rowCount()
        row = index.row()
        rect = self.visualRect(index)
        if position.y() > rect.center().y():
            row += 1
        return max(0, min(row, self.rowCount()))

    def drop_indicator_y(self, row: int) -> int | None:
        if self.rowCount() == 0:
            return self.viewport().height() // 2
        if row <= 0:
            index = self.model().index(0, 0)
            return self.visualRect(index).top()
        if row >= self.rowCount():
            index = self.model().index(self.rowCount() - 1, 0)
            return self.visualRect(index).bottom()
        index = self.model().index(row, 0)
        return self.visualRect(index).top()

    def set_drop_indicator(self, row: int) -> None:
        row = max(0, min(row, self.rowCount()))
        if self.drop_indicator_row != row:
            self.drop_indicator_row = row
            self.drop_indicator_phase = 0
        if not self.drop_indicator_timer.isActive():
            self.drop_indicator_timer.start()
        self.viewport().update()

    def clear_drop_indicator(self) -> None:
        if self.drop_indicator_row is None and not self.drop_indicator_timer.isActive():
            return
        self.drop_indicator_row = None
        self.drop_indicator_timer.stop()
        self.viewport().update()

    def advance_drop_indicator(self) -> None:
        self.drop_indicator_phase = (self.drop_indicator_phase + 1) % 12
        self.viewport().update()

    def drag_preview(self, row: int) -> QPixmap:
        if row < 0 or row >= self.rowCount() or self.isRowHidden(row):
            return QPixmap()
        top_index = self.model().index(row, 0)
        bottom_index = self.model().index(row, 0)
        top = self.visualRect(top_index).top()
        bottom = self.visualRect(bottom_index).bottom()
        if bottom <= top:
            return QPixmap()
        rect = QRect(0, top, self.viewport().width(), min(bottom - top + 1, 140))
        pixmap = self.viewport().grab(rect)
        if pixmap.isNull():
            return pixmap
        preview = QPixmap(pixmap.size())
        preview.fill(Qt.GlobalColor.transparent)
        painter = QPainter(preview)
        painter.setOpacity(0.78)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
        return preview


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class MeasurementCellWidget(QWidget):
    edit_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.edit_button = QPushButton("编辑")
        self.edit_button.setToolTip("编辑测膜厚和测距标注；已标状态会在生成时自动导出")
        self.edit_button.setMinimumWidth(58)
        self.default_button_palette = QPalette(self.edit_button.palette())
        self.marked_button_palette = QPalette(self.default_button_palette)
        self.marked_button_palette.setColor(QPalette.ColorRole.Button, QColor("#DDF3E3"))
        self.missing_calibration_button_palette = QPalette(self.default_button_palette)
        self.missing_calibration_button_palette.setColor(QPalette.ColorRole.Button, QColor("#F3DDDD"))
        self.summary = "编辑"
        self.calibration_available = True
        self.edit_button.setFixedWidth(self.edit_button.sizeHint().width())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)
        layout.addWidget(self.edit_button)

        self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self))

    def set_summary(self, summary: str) -> None:
        self.summary = summary
        self.update_button_style()

    def set_calibration_available(self, available: bool) -> None:
        self.calibration_available = available
        self.update_button_style()

    def update_button_style(self) -> None:
        self.edit_button.setStyleSheet("")
        self.edit_button.setAutoFillBackground(False)
        if not self.calibration_available:
            self.edit_button.setText("手动标定")
            self.edit_button.setPalette(self.missing_calibration_button_palette)
            self.edit_button.setToolTip("缺少可用的像素标定；应用手动标定后返回列表，需要测量时再点击“编辑”")
        else:
            marked = self.summary != "编辑"
            self.edit_button.setText(self.summary)
            self.edit_button.setPalette(self.marked_button_palette if marked else self.default_button_palette)
            self.edit_button.setToolTip("编辑测膜厚和测距标注；已标状态会在生成时自动导出")
        self.edit_button.setFixedWidth(self.edit_button.sizeHint().width())
        self.edit_button.update()
