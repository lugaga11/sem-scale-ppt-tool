from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QCursor, QDrag, QDragLeaveEvent, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyleOptionViewItem,
    QTableWidget,
    QWidget,
)

from ..image_widgets import load_display_pixmap
from ..theme import CHECK_SIZE, COLORS, FONT_BODY, FONT_CAPTION, RADIUS, RADIUS_SMALL, SPACE, icon
from ..widgets import EmptyState, describe
from .core import DEFAULTS
from .hover import ImageHoverCard

COL_GENERATE = 0
COL_NAME = 1
COL_MEASUREMENT = 2


class GenerateCheckDelegate(QStyledItemDelegate):
    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self.table = table

    @staticmethod
    def indicator_rect(option) -> QRect:
        rect = QRect(0, 0, CHECK_SIZE, CHECK_SIZE)
        rect.moveCenter(option.rect.center())
        return rect

    def paint(self, painter, option, index) -> None:
        background = QStyleOptionViewItem(option)
        self.initStyleOption(background, index)
        background.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        self.table.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, self.table)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect.left(), option.rect.top() + SPACE.sm, 3, option.rect.height() - 2 * SPACE.sm, QColor(COLORS.primary))
        indicator = QStyleOptionButton()
        indicator.rect = self.indicator_rect(option)
        indicator.state = QStyle.StateFlag.State_Enabled if index.flags() & Qt.ItemFlag.ItemIsEnabled else QStyle.StateFlag.State_None
        indicator.state |= QStyle.StateFlag.State_On if Qt.CheckState(index.data(Qt.ItemDataRole.CheckStateRole)) == Qt.CheckState.Checked else QStyle.StateFlag.State_Off
        self.table.style().drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, indicator, painter, self.table)

    def editorEvent(self, event, model, option, index) -> bool:
        if not (index.flags() & Qt.ItemFlag.ItemIsEnabled and index.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return False
        if event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() != Qt.MouseButton.LeftButton or not self.indicator_rect(option).contains(event.position().toPoint()):
                return False
        elif event.type() == QEvent.Type.KeyPress:
            if event.key() not in (Qt.Key.Key_Space, Qt.Key.Key_Select):
                return False
        else:
            return False
        checked = Qt.CheckState(index.data(Qt.ItemDataRole.CheckStateRole)) == Qt.CheckState.Checked
        # PySide6 6.6 stores an enum passed to setData as "unchecked"; the integer value works on every version.
        new_state = Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
        return model.setData(index, new_state.value, Qt.ItemDataRole.CheckStateRole)


class FileNameDelegate(QStyledItemDelegate):
    """Load only visible thumbnails, one per event-loop turn, with a bounded cache."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self.table = table
        self.thumbnails: OrderedDict[str, QPixmap] = OrderedDict()
        self.pending: dict[str, Path] = {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.load_next_thumbnail)

    @staticmethod
    def thumbnail_rect(cell: QRect) -> QRect:
        return QRect(cell.left() + SPACE.sm, cell.center().y() - 22, 64, 44)

    @Slot()
    def load_next_thumbnail(self) -> None:
        if not self.table.isVisible():
            self.pending.clear()
            return
        if not self.pending:
            return
        key = next(iter(self.pending))
        path = self.pending.pop(key)
        try:
            pixmap, _size, _format = load_display_pixmap(path, QSize(64, 44))
        except (OSError, ValueError):
            pixmap = QPixmap()
        self.thumbnails[key] = pixmap
        while len(self.thumbnails) > 128:
            self.thumbnails.popitem(last=False)
        self.table.viewport().update()
        if self.pending:
            self.timer.start(10)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        title = opt.text
        opt.text = ""
        self.table.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, self.table)
        path = Path(index.data(Qt.ItemDataRole.UserRole))
        key = str(path)
        thumbnail = self.thumbnails.get(key)
        if thumbnail is None and key not in self.pending:
            self.pending[key] = path
            if not self.timer.isActive():
                self.timer.start(20)

        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        image_rect = self.thumbnail_rect(option.rect)
        painter.setPen(QPen(QColor(COLORS.border), 1))
        painter.setBrush(QColor(COLORS.subtle))
        painter.drawRoundedRect(image_rect.adjusted(-SPACE.xs, -SPACE.xs, SPACE.xs, SPACE.xs), RADIUS, RADIUS)
        if thumbnail is not None and not thumbnail.isNull():
            self.thumbnails.move_to_end(key)
            target = QRect(0, 0, thumbnail.width(), thumbnail.height())
            target.moveCenter(image_rect.center())
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(image_rect), RADIUS_SMALL, RADIUS_SMALL)
            painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
            painter.drawPixmap(target, thumbnail)
            painter.restore()
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS.tint))
            painter.drawRoundedRect(image_rect, RADIUS_SMALL, RADIUS_SMALL)
            painter.setPen(QColor(COLORS.muted))
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, path.suffix.lstrip(".").upper())

        text_rect = option.rect.adjusted(0, SPACE.sm, -SPACE.sm, -SPACE.sm)
        text_rect.setLeft(image_rect.right() + 1 + SPACE.md)
        font = QFont(option.font)
        font.setPixelSize(FONT_BODY)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(COLORS.heading))
        painter.drawText(
            QRect(text_rect.left(), text_rect.top(), text_rect.width(), 22),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, max(0, text_rect.width())),
        )
        font = painter.font()
        font.setPixelSize(FONT_CAPTION)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(COLORS.muted))
        painter.drawText(
            QRect(text_rect.left(), text_rect.top() + 24, text_rect.width(), 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(str(path.parent), Qt.TextElideMode.ElideMiddle, max(0, text_rect.width())),
        )
        painter.restore()


class FileQueueWidget(QTableWidget):
    QUEUE_ROW_MIME = "application/x-sem-scale-queue-rows"

    paths_dropped = Signal(list)
    row_reorder_requested = Signal(int, int)

    def sizeHintForColumn(self, column: int) -> int:
        width = super().sizeHintForColumn(column)
        return max(160, width) if column == COL_MEASUREMENT else width

    def __init__(self) -> None:
        super().__init__(0, 3)
        self.setHorizontalHeaderLabels(["生成", "文件", "测量"])

        self.drag_start_position: QPoint | None = None
        self.drag_start_row = -1
        self.dragged_row: int | None = None
        self.drop_indicator_row: int | None = None
        self.drop_position: QPoint | None = None

        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDropIndicatorShown(False)
        self.setAutoScroll(True)
        self.setAutoScrollMargin(36)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(False)
        self.setShowGrid(False)
        self.setMinimumHeight(DEFAULTS["queue_min_height"])
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(68)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.name_delegate = FileNameDelegate(self)
        self.setItemDelegateForColumn(COL_NAME, self.name_delegate)
        self.setItemDelegateForColumn(COL_GENERATE, GenerateCheckDelegate(self))

        header = self.horizontalHeader()
        header.setSectionResizeMode(COL_GENERATE, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(COL_GENERATE, 58)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_MEASUREMENT, QHeaderView.ResizeMode.ResizeToContents)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        generate_header = self.horizontalHeaderItem(COL_GENERATE)
        assert generate_header is not None
        generate_header.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state = EmptyState(self.viewport(), "拖入图片或文件夹", "支持批量添加 · 悬停缩略图放大查看", "image")
        self.model().rowsInserted.connect(self.update_empty_state)
        self.model().rowsRemoved.connect(self.update_empty_state)
        self.model().modelReset.connect(self.update_empty_state)
        self.hover_card = ImageHoverCard(self)
        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.setInterval(350)
        self.hover_timer.timeout.connect(self.show_hover_preview)
        self._hover_index = QPersistentModelIndex()
        self._hover_window = None
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.verticalScrollBar().valueChanged.connect(self.dismiss_hover)
        self.horizontalScrollBar().valueChanged.connect(self.dismiss_hover)
        self.model().rowsAboutToBeRemoved.connect(self.dismiss_hover)
        self.model().modelAboutToBeReset.connect(self.dismiss_hover)
        self.model().layoutAboutToBeChanged.connect(self.dismiss_hover)

    def update_empty_state(self, *_args) -> None:
        self.empty_state.setVisible(self.rowCount() == 0)

    @Slot()
    def dismiss_hover(self) -> None:
        self.hover_timer.stop()
        self.hover_card.hide()
        self._hover_index = QPersistentModelIndex()

    def track_thumbnail(self, position: QPoint) -> None:
        index = self.indexAt(position)
        if index.column() != COL_NAME or not self.name_delegate.thumbnail_rect(self.visualRect(index)).contains(position):
            self.dismiss_hover()
            return
        if index != self._hover_index:
            self.dismiss_hover()
            self._hover_index = QPersistentModelIndex(index)
            self.hover_timer.start()

    @Slot()
    def show_hover_preview(self) -> None:
        if not self.isVisible() or not self._hover_index.isValid() or QApplication.activeModalWidget() is not None:
            self.dismiss_hover()
            return
        position = self.viewport().mapFromGlobal(QCursor.pos())
        index = self.indexAt(position)
        if index != self._hover_index:
            self.dismiss_hover()
            return
        rect = self.name_delegate.thumbnail_rect(self.visualRect(index))
        if not rect.contains(position):
            self.dismiss_hover()
            return
        rect.moveTopLeft(self.viewport().mapToGlobal(rect.topLeft()))
        self.hover_card.show_image(Path(index.data(Qt.ItemDataRole.UserRole)), index.data(Qt.ItemDataRole.WhatsThisRole) or "", rect)

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.Leave:
            self.dismiss_hover()
        return super().viewportEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._hover_window is not self.window():
            if self._hover_window is not None:
                self._hover_window.removeEventFilter(self)
            self._hover_window = self.window()
            self._hover_window.installEventFilter(self)

    def hideEvent(self, event) -> None:
        self.dismiss_hover()
        self.dragLeaveEvent(QDragLeaveEvent())
        super().hideEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._hover_window and event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.Hide, QEvent.Type.Move, QEvent.Type.Resize):
            self.dismiss_hover()
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        self.dismiss_hover()
        super().keyPressEvent(event)

    def is_queue_drag(self, event) -> bool:
        return event.source() is self and event.mimeData().hasFormat(self.QUEUE_ROW_MIME)

    def dragEnterEvent(self, event) -> None:
        self.dismiss_hover()
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event) -> None:
        if not (event.mimeData().hasUrls() or self.is_queue_drag(event)):
            self.dragLeaveEvent(QDragLeaveEvent())
            event.ignore()
            return

        # Our MIME is handled outside the table model; Qt still owns edge scrolling.
        self.setState(QAbstractItemView.State.DraggingState)
        super().dragMoveEvent(event)
        if event.mimeData().hasUrls():
            self.clear_drop_indicator()
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
        else:
            position: QPoint = event.position().toPoint()
            self.drop_position = position
            self.set_drop_indicator(self.drop_row(position))
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()

    def dragLeaveEvent(self, event) -> None:
        self.clear_drop_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        # Stop edge scrolling at release; our handler moves the row exactly once.
        self.dragLeaveEvent(QDragLeaveEvent())
        if event.mimeData().hasUrls():
            paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
            self.paths_dropped.emit(paths)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
        elif self.is_queue_drag(event):
            assert self.dragged_row is not None
            self.row_reorder_requested.emit(self.dragged_row, self.drop_row(event.position().toPoint()))
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        if self.drop_position is not None:
            self.set_drop_indicator(self.drop_row(self.drop_position))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.drop_indicator_row is None:
            return

        y = self.drop_indicator_y(self.drop_indicator_row)
        color = QColor(COLORS.primary)
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        left = SPACE.sm
        right = self.viewport().width() - SPACE.sm
        painter.drawLine(left, y, right, y)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPoint(left, y), 4, 4)
        painter.drawEllipse(QPoint(right, y), 4, 4)

    def mousePressEvent(self, event) -> None:
        self.dismiss_hover()
        super().mousePressEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            self.drag_start_position = None
            self.drag_start_row = -1
            return
        position = event.position().toPoint()
        index = self.indexAt(position)
        self.drag_start_position = position
        self.drag_start_row = index.row() if index.isValid() else -1

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self.track_thumbnail(event.position().toPoint())
            super().mouseMoveEvent(event)
            return
        self.dismiss_hover()
        if self.drag_start_position is None or self.drag_start_row < 0:
            super().mouseMoveEvent(event)
            return

        position = event.position().toPoint()
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
        mime_data = QMimeData()
        mime_data.setData(self.QUEUE_ROW_MIME, b"")
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.setPixmap(self.drag_preview(row))
        drag.setHotSpot(QPoint(18, 24))
        self.dragged_row = row
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.dragged_row = None
            self.dragLeaveEvent(QDragLeaveEvent())

    def drop_row(self, position: QPoint) -> int:
        index = self.indexAt(position)
        if not index.isValid():
            return self.rowCount()
        row = index.row()
        rect = self.visualRect(index)
        if position.y() > rect.center().y():
            row += 1
        return row

    def drop_indicator_y(self, row: int) -> int:
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
        if self.drop_indicator_row != row:
            self.drop_indicator_row = row
            self.viewport().update()

    def clear_drop_indicator(self) -> None:
        self.drop_position = None
        if self.drop_indicator_row is None:
            return
        self.drop_indicator_row = None
        self.viewport().update()

    def drag_preview(self, row: int) -> QPixmap:
        row_rect = self.visualRect(self.model().index(row, 0))
        rect = QRect(0, row_rect.top(), self.viewport().width(), row_rect.height())
        pixmap = self.viewport().grab(rect)
        preview = QPixmap(pixmap.size())
        preview.fill(Qt.GlobalColor.transparent)
        painter = QPainter(preview)
        painter.setOpacity(0.78)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
        return preview


class MeasurementCellWidget(QWidget):
    edit_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.edit_button = QPushButton("编辑")
        self.edit_button.setProperty("variant", "measurement")
        self.edit_button.setIconSize(QSize(14, 14))
        self.edit_button.setMinimumWidth(58)
        self.summary = "编辑"
        self.calibration_available = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE.sm, 0, SPACE.md, 0)
        layout.setSpacing(0)
        layout.addWidget(self.edit_button)
        layout.addStretch()

        self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self))
        self.update_button_style()

    def set_summary(self, summary: str) -> None:
        if self.summary == summary:
            return
        self.summary = summary
        self.update_button_style()

    def set_calibration_available(self, available: bool) -> None:
        if self.calibration_available == available:
            return
        self.calibration_available = available
        self.update_button_style()

    def update_button_style(self) -> None:
        if not self.calibration_available:
            self.edit_button.setIcon(icon("ruler", COLORS.warning))
            self.edit_button.setProperty("status", "missing")
            self.edit_button.setText("手动标定")
            describe(self.edit_button, "缺少像素标定：先沿已知长度画线完成手动标定，回到队列后再点“编辑”添加测量")
        else:
            marked = self.summary != "编辑"
            self.edit_button.setIcon(icon("check", COLORS.success) if marked else icon("edit"))
            self.edit_button.setProperty("status", "marked" if marked else "ready")
            self.edit_button.setText(self.summary)
            describe(self.edit_button, "添加或修改膜厚、测距标注；已应用的标注会随图片一起导出")
        self.edit_button.style().unpolish(self.edit_button)
        self.edit_button.style().polish(self.edit_button)
        self.edit_button.setFixedWidth(self.edit_button.sizeHint().width())
        self.updateGeometry()
        self.edit_button.update()
