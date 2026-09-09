from __future__ import annotations

import math
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..imaging import image_to_display_rgb
from .core import (
    Calibration,
    CalibrationOverride,
    MeasurementOverride,
    detect_thickness_measurement,
    format_length_label,
    parse_scale_nm,
    parse_sem_metadata,
)


def scale_input_from_nm(length_nm: float) -> str:
    if length_nm >= 1000:
        return f"{length_nm / 1000:g}um"
    return f"{length_nm:g}nm"


def qimage_from_pil(image: Image.Image) -> QImage:
    rgb = image_to_display_rgb(image)
    width, height = rgb.size
    image_bytes = rgb.tobytes()
    return QImage(
        image_bytes,
        width,
        height,
        width * 3,
        QImage.Format.Format_RGB888,
    ).copy()


class MeasurementPreviewWidget(QWidget):
    annotations_changed = Signal()
    selection_changed = Signal()
    tool_changed = Signal(str)
    zoom_changed = Signal(float)

    def __init__(
        self,
        qimage: QImage,
        nm_per_pixel: float,
        annotations: list[MeasurementOverride],
        auto_thickness: MeasurementOverride | None,
        *,
        distance_label_mode: str = "length",
        single_distance: bool = False,
    ) -> None:
        super().__init__()
        self.pixmap = QPixmap.fromImage(qimage)
        self.image_w = qimage.width()
        self.image_h = qimage.height()
        self.nm_per_pixel = nm_per_pixel
        self.distance_label_mode = distance_label_mode
        self.single_distance = single_distance
        self.annotations = [
            annotation
            for annotation in (self.clamped_annotation(annotation) for annotation in annotations)
            if annotation is not None
        ]
        self.auto_thickness = self.clamped_annotation(auto_thickness) if auto_thickness else None
        self.current_tool = "distance"
        self.selected_index = -1
        self.dragging_thickness: str | None = None
        self.dragging_distance: tuple[int, str] | None = None
        self.dragging_whole: tuple[int, float, float, MeasurementOverride] | None = None
        self.drag_history_recorded = False
        self.creating_start: tuple[float, float] | None = None
        self.creating_end: tuple[float, float] | None = None
        self.undo_stack: list[tuple[list[MeasurementOverride], int, str]] = []
        self.redo_stack: list[tuple[list[MeasurementOverride], int, str]] = []
        self.history_limit = 100
        self.zoom_factor = 1.0
        self.max_zoom_factor = 10.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.panning = False
        self.pan_last_pos: QPointF | None = None
        self.setMinimumSize(640, 420)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setToolTip(
            "拖动线段整体移动；Ctrl+Z 撤销，Ctrl+Y/Ctrl+Shift+Z 重做；"
            "Delete 删除选中；Esc 取消绘制；Ctrl+滚轮缩放；放大后按住鼠标中键平移"
        )

    def clamped_annotation(self, annotation: MeasurementOverride | None) -> MeasurementOverride | None:
        if annotation is None:
            return None
        kind = annotation.kind if annotation.kind in {"thickness", "distance"} else "distance"
        x1 = max(0.0, min(float(annotation.x1_px), float(self.image_w - 1)))
        y1 = max(0.0, min(float(annotation.y1_px), float(self.image_h - 1)))
        x2 = max(0.0, min(float(annotation.x2_px), float(self.image_w - 1)))
        y2 = max(0.0, min(float(annotation.y2_px), float(self.image_h - 1)))
        if kind == "thickness":
            x_center = max(0.0, min((x1 + x2) / 2, float(self.image_w - 1)))
            y1, y2 = sorted((y1, y2))
            if y2 <= y1:
                y2 = min(float(self.image_h - 1), y1 + 1)
                if y2 <= y1:
                    y1 = max(0.0, y2 - 1)
            x1 = x2 = x_center
        elif math.hypot(x2 - x1, y2 - y1) <= 0:
            return None
        return MeasurementOverride(kind=kind, x1_px=x1, y1_px=y1, x2_px=x2, y2_px=y2)

    def fallback_thickness(self) -> MeasurementOverride:
        x_center = self.image_w / 2
        return MeasurementOverride(
            kind="thickness",
            x1_px=x_center,
            y1_px=self.image_h * 0.4,
            x2_px=x_center,
            y2_px=self.image_h * 0.6,
        )

    def thickness_index(self) -> int:
        for index, annotation in enumerate(self.annotations):
            if annotation.kind == "thickness":
                return index
        return -1

    def distance_count(self) -> int:
        return sum(1 for annotation in self.annotations if annotation.kind == "distance")

    def distance_number(self, annotation_index: int) -> int:
        return sum(1 for annotation in self.annotations[: annotation_index + 1] if annotation.kind == "distance")

    def selected_annotation(self) -> MeasurementOverride | None:
        if 0 <= self.selected_index < len(self.annotations):
            return self.annotations[self.selected_index]
        return None

    def has_annotations(self) -> bool:
        return bool(self.annotations)

    def history_state(self) -> tuple[list[MeasurementOverride], int, str]:
        return list(self.annotations), self.selected_index, self.current_tool

    def record_history(self) -> None:
        self.undo_stack.append(self.history_state())
        if len(self.undo_stack) > self.history_limit:
            del self.undo_stack[0]
        self.redo_stack.clear()

    def cancel_interaction(self) -> None:
        self.creating_start = None
        self.creating_end = None
        self.dragging_distance = None
        self.dragging_thickness = None
        self.dragging_whole = None
        self.drag_history_recorded = False
        self.panning = False
        self.pan_last_pos = None
        self.setCursor(Qt.CursorShape.CrossCursor if self.current_tool == "distance" else Qt.CursorShape.ArrowCursor)

    def restore_history_state(self, state: tuple[list[MeasurementOverride], int, str]) -> None:
        annotations, selected_index, tool = state
        previous_tool = self.current_tool
        self.annotations = list(annotations)
        self.selected_index = selected_index if 0 <= selected_index < len(self.annotations) else -1
        self.current_tool = tool if tool in {"thickness", "distance"} else "distance"
        self.cancel_interaction()
        self.annotations_changed.emit()
        self.selection_changed.emit()
        if self.current_tool != previous_tool:
            self.tool_changed.emit(self.current_tool)
        self.update()

    def undo(self) -> None:
        if not self.undo_stack:
            return
        self.redo_stack.append(self.history_state())
        self.restore_history_state(self.undo_stack.pop())

    def redo(self) -> None:
        if not self.redo_stack:
            return
        self.undo_stack.append(self.history_state())
        self.restore_history_state(self.redo_stack.pop())

    def cancel_current_drawing(self) -> bool:
        if self.creating_start is None and self.creating_end is None:
            return False
        self.creating_start = None
        self.creating_end = None
        self.setCursor(Qt.CursorShape.CrossCursor if self.current_tool == "distance" else Qt.CursorShape.ArrowCursor)
        self.update()
        return True

    def set_tool(self, tool: str) -> None:
        if tool not in {"thickness", "distance"}:
            return
        if self.current_tool != tool:
            self.current_tool = tool
            self.tool_changed.emit(tool)
        self.dragging_thickness = None
        self.dragging_distance = None
        self.dragging_whole = None
        self.drag_history_recorded = False
        self.creating_start = None
        self.creating_end = None
        self.update()

    def select_annotation(self, index: int) -> None:
        if not 0 <= index < len(self.annotations):
            self.select_none()
            return
        self.selected_index = index
        self.set_tool(self.annotations[index].kind)
        self.selection_changed.emit()
        self.update()

    def select_none(self) -> None:
        if self.selected_index == -1:
            return
        self.selected_index = -1
        self.selection_changed.emit()
        self.update()

    def add_or_select_thickness(self) -> None:
        index = self.thickness_index()
        if index < 0:
            thickness = self.auto_thickness or self.fallback_thickness()
            self.record_history()
            self.annotations.append(thickness)
            index = len(self.annotations) - 1
            self.annotations_changed.emit()
        self.select_annotation(index)

    def use_distance_tool(self) -> None:
        self.set_tool("distance")
        selected = self.selected_annotation()
        if selected is not None and selected.kind != "distance":
            self.select_none()

    def base_image_rect(self) -> QRectF:
        width = max(1, self.width())
        height = max(1, self.height())
        scale = min(width / self.image_w, height / self.image_h) * self.zoom_factor
        draw_w = self.image_w * scale
        draw_h = self.image_h * scale
        return QRectF((width - draw_w) / 2, (height - draw_h) / 2, draw_w, draw_h)

    def image_rect(self) -> QRectF:
        rect = self.base_image_rect()
        return QRectF(rect.left() + self.pan_x, rect.top() + self.pan_y, rect.width(), rect.height())

    def clamp_pan(self) -> None:
        if self.zoom_factor <= 1.0001:
            self.pan_x = 0.0
            self.pan_y = 0.0
            return

        base_rect = self.base_image_rect()
        width = max(1, self.width())
        height = max(1, self.height())

        left = base_rect.left() + self.pan_x
        if base_rect.width() <= width:
            left = max(0.0, min(width - base_rect.width(), left))
        else:
            left = max(width - base_rect.width(), min(0.0, left))
        self.pan_x = left - base_rect.left()

        top = base_rect.top() + self.pan_y
        if base_rect.height() <= height:
            top = max(0.0, min(height - base_rect.height(), top))
        else:
            top = max(height - base_rect.height(), min(0.0, top))
        self.pan_y = top - base_rect.top()

    def widget_to_image_ratio(self, widget_x: float, widget_y: float) -> tuple[float, float]:
        rect = self.image_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return 0.5, 0.5
        ratio_x = (widget_x - rect.left()) / rect.width()
        ratio_y = (widget_y - rect.top()) / rect.height()
        return max(0.0, min(1.0, ratio_x)), max(0.0, min(1.0, ratio_y))

    def widget_to_image(self, widget_x: float, widget_y: float) -> tuple[float, float]:
        ratio_x, ratio_y = self.widget_to_image_ratio(widget_x, widget_y)
        return (
            max(0.0, min(ratio_x * self.image_w, float(self.image_w - 1))),
            max(0.0, min(ratio_y * self.image_h, float(self.image_h - 1))),
        )

    def image_to_widget(self, image_x: float, image_y: float) -> QPointF:
        rect = self.image_rect()
        return QPointF(
            rect.left() + image_x / self.image_w * rect.width(),
            rect.top() + image_y / self.image_h * rect.height(),
        )

    def image_y_to_widget(self, image_y: float) -> float:
        rect = self.image_rect()
        return rect.top() + image_y / self.image_h * rect.height()

    def snap_distance_endpoint(
        self,
        anchor_x: float,
        anchor_y: float,
        target_x: float,
        target_y: float,
        locked: bool,
    ) -> tuple[float, float]:
        if not locked:
            return target_x, target_y
        dx = target_x - anchor_x
        dy = target_y - anchor_y
        length = math.hypot(dx, dy)
        if length <= 0:
            return target_x, target_y
        angle = math.atan2(dy, dx)
        snapped_angle = round(angle / (math.pi / 4)) * (math.pi / 4)
        unit_x = math.cos(snapped_angle)
        unit_y = math.sin(snapped_angle)
        max_length = length
        if unit_x > 0:
            max_length = min(max_length, (self.image_w - 1 - anchor_x) / unit_x)
        elif unit_x < 0:
            max_length = min(max_length, (0.0 - anchor_x) / unit_x)
        if unit_y > 0:
            max_length = min(max_length, (self.image_h - 1 - anchor_y) / unit_y)
        elif unit_y < 0:
            max_length = min(max_length, (0.0 - anchor_y) / unit_y)
        length = max(0.0, max_length)
        snapped_x = anchor_x + unit_x * length
        snapped_y = anchor_y + unit_y * length
        return (
            max(0.0, min(snapped_x, float(self.image_w - 1))),
            max(0.0, min(snapped_y, float(self.image_h - 1))),
        )

    def translated_annotation(
        self,
        annotation: MeasurementOverride,
        delta_x: float,
        delta_y: float,
    ) -> MeasurementOverride:
        min_x = min(annotation.x1_px, annotation.x2_px)
        max_x = max(annotation.x1_px, annotation.x2_px)
        min_y = min(annotation.y1_px, annotation.y2_px)
        max_y = max(annotation.y1_px, annotation.y2_px)
        delta_x = max(-min_x, min(delta_x, float(self.image_w - 1) - max_x))
        delta_y = max(-min_y, min(delta_y, float(self.image_h - 1) - max_y))
        return MeasurementOverride(
            annotation.kind,
            annotation.x1_px + delta_x,
            annotation.y1_px + delta_y,
            annotation.x2_px + delta_x,
            annotation.y2_px + delta_y,
        )

    def update_annotation(self, index: int, annotation: MeasurementOverride) -> None:
        clamped = self.clamped_annotation(annotation)
        if clamped is None or not 0 <= index < len(self.annotations):
            return
        if clamped == self.annotations[index]:
            return
        if not self.drag_history_recorded:
            self.record_history()
            self.drag_history_recorded = True
        self.annotations[index] = clamped
        self.annotations_changed.emit()
        self.update()

    def nearest_thickness_handle(self, annotation: MeasurementOverride, widget_x: float, widget_y: float) -> str | None:
        rect = self.image_rect()
        top_line_y = self.image_y_to_widget(annotation.y1_px)
        bottom_line_y = self.image_y_to_widget(annotation.y2_px)
        top_point = self.image_to_widget(annotation.x1_px, annotation.y1_px)
        bottom_point = self.image_to_widget(annotation.x2_px, annotation.y2_px)

        if rect.left() <= widget_x <= rect.right():
            top_distance = abs(widget_y - top_line_y)
            bottom_distance = abs(widget_y - bottom_line_y)
            nearest = "top" if top_distance <= bottom_distance else "bottom"
            if min(top_distance, bottom_distance) <= 10:
                return nearest

        if self.point_segment_distance(widget_x, widget_y, top_point, bottom_point) <= 10:
            return "center"
        return None

    def nearest_distance_handle(self, annotation: MeasurementOverride, widget_x: float, widget_y: float) -> str | None:
        start = self.image_to_widget(annotation.x1_px, annotation.y1_px)
        end = self.image_to_widget(annotation.x2_px, annotation.y2_px)
        if math.hypot(widget_x - start.x(), widget_y - start.y()) <= 14:
            return "start"
        if math.hypot(widget_x - end.x(), widget_y - end.y()) <= 14:
            return "end"
        if self.point_segment_distance(widget_x, widget_y, start, end) <= 10:
            return "line"
        return None

    def nearest_annotation_handle(self, widget_x: float, widget_y: float) -> tuple[int, str] | None:
        kinds = (self.current_tool, "distance" if self.current_tool == "thickness" else "thickness")
        for kind in kinds:
            for index in range(len(self.annotations) - 1, -1, -1):
                annotation = self.annotations[index]
                if annotation.kind != kind:
                    continue
                if kind == "thickness":
                    handle = self.nearest_thickness_handle(annotation, widget_x, widget_y)
                else:
                    handle = self.nearest_distance_handle(annotation, widget_x, widget_y)
                if handle:
                    return index, handle
        return None

    @staticmethod
    def point_segment_distance(px: float, py: float, a: QPointF, b: QPointF) -> float:
        dx = b.x() - a.x()
        dy = b.y() - a.y()
        length_sq = dx * dx + dy * dy
        if length_sq <= 0:
            return math.hypot(px - a.x(), py - a.y())
        t = max(0.0, min(1.0, ((px - a.x()) * dx + (py - a.y()) * dy) / length_sq))
        closest_x = a.x() + dx * t
        closest_y = a.y() + dy * t
        return math.hypot(px - closest_x, py - closest_y)

    def measurement_label(self, annotation: MeasurementOverride) -> str:
        length_px = math.hypot(annotation.x2_px - annotation.x1_px, annotation.y2_px - annotation.y1_px)
        if self.distance_label_mode == "pixels":
            return f"{length_px:.1f} px"
        return format_length_label(length_px * self.nm_per_pixel)

    def draw_endpoint_handle(self, painter: QPainter, point: QPointF, color: QColor, selected: bool) -> None:
        size = 7 if selected else 6
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 1.5))
        painter.drawLine(QPointF(point.x() - size, point.y()), QPointF(point.x() + size, point.y()))
        painter.drawLine(QPointF(point.x(), point.y() - size), QPointF(point.x(), point.y() + size))

    @staticmethod
    def color_with_alpha(color: QColor, selected: bool) -> QColor:
        result = QColor(color)
        result.setAlpha(255 if selected else 170)
        return result

    def draw_distance(
        self, painter: QPainter, annotation: MeasurementOverride, selected: bool, temporary: bool = False
    ) -> None:
        start = self.image_to_widget(annotation.x1_px, annotation.y1_px)
        end = self.image_to_widget(annotation.x2_px, annotation.y2_px)
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        span = math.hypot(dx, dy)
        if span <= 0:
            return
        color = self.color_with_alpha(QColor("#7CFF8E" if selected else "#FFE66D"), selected)
        if temporary:
            color = QColor("#FFFFFF")
        pen = QPen(color, 3 if selected else 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(start, end)
        self.draw_endpoint_handle(painter, start, color, selected)
        self.draw_endpoint_handle(painter, end, color, selected)

        mid_x = (start.x() + end.x()) / 2
        mid_y = (start.y() + end.y()) / 2
        label_offset = 16.0
        label_x = mid_x + dy / span * label_offset
        label_y = mid_y - dx / span * label_offset
        label = self.measurement_label(annotation)
        old_font = painter.font()
        font = painter.font()
        font.setPointSize((font.pointSize() if font.pointSize() > 0 else 10) + 1)
        painter.setFont(font)
        painter.setPen(QPen(color, 2))
        painter.drawText(int(label_x), int(label_y), label)
        painter.setFont(old_font)

    def draw_thickness(self, painter: QPainter, annotation: MeasurementOverride, selected: bool) -> None:
        rect = self.image_rect()
        top_line_y = self.image_y_to_widget(annotation.y1_px)
        bottom_line_y = self.image_y_to_widget(annotation.y2_px)
        top_point = self.image_to_widget(annotation.x1_px, annotation.y1_px)
        bottom_point = self.image_to_widget(annotation.x2_px, annotation.y2_px)
        top_color = self.color_with_alpha(QColor("#00D1FF"), selected)
        bottom_color = self.color_with_alpha(QColor("#FF4D4D"), selected)
        measure_color = self.color_with_alpha(QColor("#7CFF8E"), selected)
        width = 3 if selected else 2

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(top_color, width))
        painter.drawLine(int(rect.left()), int(top_line_y), int(rect.right()), int(top_line_y))
        painter.setPen(QPen(bottom_color, width))
        painter.drawLine(int(rect.left()), int(bottom_line_y), int(rect.right()), int(bottom_line_y))
        painter.setPen(QPen(measure_color, width))
        painter.drawLine(top_point, bottom_point)
        self.draw_endpoint_handle(painter, top_point, measure_color, selected)
        self.draw_endpoint_handle(painter, bottom_point, measure_color, selected)

        mid_y = (top_line_y + bottom_line_y) / 2
        label_x = min(max(int(top_point.x() + 8), int(rect.left()) + 8), int(rect.right()) - 80)
        painter.setPen(QPen(measure_color, 2))
        painter.drawText(label_x, int(mid_y - 8), self.measurement_label(annotation))
        if selected:
            guide_x = max(8, int(rect.left()) + 8)
            painter.setPen(QPen(top_color, 2))
            painter.drawText(guide_x, int(top_line_y) - 8, "上界面")
            painter.setPen(QPen(bottom_color, 2))
            painter.drawText(guide_x, int(bottom_line_y) - 8, "下界面")

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#151515"))
        rect = self.image_rect()
        painter.drawPixmap(rect.toRect(), self.pixmap)

        for index, annotation in enumerate(self.annotations):
            selected = index == self.selected_index
            if annotation.kind == "thickness":
                self.draw_thickness(painter, annotation, selected)
            else:
                self.draw_distance(painter, annotation, selected)
        if self.creating_start and self.creating_end:
            preview_distance = MeasurementOverride(
                kind="distance",
                x1_px=self.creating_start[0],
                y1_px=self.creating_start[1],
                x2_px=self.creating_end[0],
                y2_px=self.creating_end[1],
            )
            self.draw_distance(painter, preview_distance, selected=True, temporary=True)
        painter.end()

    def wheelEvent(self, event) -> None:
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.ignore()
            return

        delta_y = event.angleDelta().y() or event.pixelDelta().y()
        if delta_y == 0:
            event.ignore()
            return

        position = event.position()
        ratio_x, ratio_y = self.widget_to_image_ratio(position.x(), position.y())
        steps = delta_y / 120.0
        new_zoom = self.zoom_factor * (1.18**steps)
        new_zoom = max(1.0, min(self.max_zoom_factor, new_zoom))
        if abs(new_zoom - self.zoom_factor) < 0.001:
            event.accept()
            return

        self.zoom_factor = new_zoom
        base_rect = self.base_image_rect()
        self.pan_x = position.x() - ratio_x * base_rect.width() - base_rect.left()
        self.pan_y = position.y() - ratio_y * base_rect.height() - base_rect.top()
        self.clamp_pan()
        self.update()
        self.zoom_changed.emit(self.zoom_factor)
        event.accept()

    def reset_zoom(self) -> None:
        self.zoom_factor = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.update()
        self.zoom_changed.emit(self.zoom_factor)

    def mousePressEvent(self, event) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() == Qt.MouseButton.MiddleButton:
            if (
                self.zoom_factor > 1.0001
                and not self.creating_start
                and not self.dragging_distance
                and not self.dragging_thickness
                and not self.dragging_whole
            ):
                self.panning = True
                self.pan_last_pos = event.position()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
            else:
                event.ignore()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        widget_x = event.position().x()
        widget_y = event.position().y()
        image_x, image_y = self.widget_to_image(widget_x, widget_y)
        handle = self.nearest_annotation_handle(widget_x, widget_y)
        if handle:
            index, endpoint = handle
            annotation = self.annotations[index]
            self.select_annotation(index)
            if annotation.kind == "thickness":
                if endpoint == "center":
                    self.dragging_whole = (index, image_x, image_y, annotation)
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                else:
                    self.dragging_thickness = endpoint
                    self.setCursor(Qt.CursorShape.SizeVerCursor)
            elif endpoint in {"start", "end"}:
                self.dragging_distance = (index, endpoint)
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.dragging_whole = (index, image_x, image_y, annotation)
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            return

        if self.current_tool == "distance":
            self.select_none()
            self.creating_start = (image_x, image_y)
            self.creating_end = (image_x, image_y)
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
        else:
            self.select_none()

    def mouseMoveEvent(self, event) -> None:
        if self.panning:
            position = event.position()
            if self.pan_last_pos is not None:
                delta = position - self.pan_last_pos
                self.pan_x += delta.x()
                self.pan_y += delta.y()
                self.clamp_pan()
                self.update()
            self.pan_last_pos = position
            event.accept()
            return

        widget_x = event.position().x()
        widget_y = event.position().y()
        image_x, image_y = self.widget_to_image(widget_x, widget_y)
        shift_locked = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self.creating_start:
            self.creating_end = self.snap_distance_endpoint(
                self.creating_start[0],
                self.creating_start[1],
                image_x,
                image_y,
                shift_locked,
            )
            self.update()
            return
        if self.dragging_whole:
            index, anchor_x, anchor_y, original = self.dragging_whole
            if 0 <= index < len(self.annotations):
                self.update_annotation(
                    index,
                    self.translated_annotation(original, image_x - anchor_x, image_y - anchor_y),
                )
            return
        if self.dragging_distance:
            index, endpoint = self.dragging_distance
            if 0 <= index < len(self.annotations):
                current = self.annotations[index]
                if endpoint == "start":
                    image_x, image_y = self.snap_distance_endpoint(
                        current.x2_px,
                        current.y2_px,
                        image_x,
                        image_y,
                        shift_locked,
                    )
                    updated = MeasurementOverride(current.kind, image_x, image_y, current.x2_px, current.y2_px)
                else:
                    image_x, image_y = self.snap_distance_endpoint(
                        current.x1_px,
                        current.y1_px,
                        image_x,
                        image_y,
                        shift_locked,
                    )
                    updated = MeasurementOverride(current.kind, current.x1_px, current.y1_px, image_x, image_y)
                self.update_annotation(index, updated)
            return
        if self.dragging_thickness and 0 <= self.selected_index < len(self.annotations):
            current = self.annotations[self.selected_index]
            if current.kind == "thickness":
                if self.dragging_thickness == "top":
                    updated = MeasurementOverride(current.kind, current.x1_px, image_y, current.x2_px, current.y2_px)
                elif self.dragging_thickness == "bottom":
                    updated = MeasurementOverride(current.kind, current.x1_px, current.y1_px, current.x2_px, image_y)
                else:
                    updated = MeasurementOverride(current.kind, image_x, current.y1_px, image_x, current.y2_px)
                self.update_annotation(self.selected_index, updated)
            return

        handle = self.nearest_annotation_handle(widget_x, widget_y)
        if handle:
            index, endpoint = handle
            annotation = self.annotations[index]
            if annotation.kind == "thickness":
                self.setCursor(Qt.CursorShape.SizeAllCursor if endpoint == "center" else Qt.CursorShape.SizeVerCursor)
            elif endpoint in {"start", "end"}:
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif self.current_tool == "distance":
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            if self.panning:
                self.panning = False
                self.pan_last_pos = None
                self.setCursor(
                    Qt.CursorShape.CrossCursor if self.current_tool == "distance" else Qt.CursorShape.ArrowCursor
                )
                event.accept()
            else:
                event.ignore()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        if self.creating_start and self.creating_end:
            length_px = math.hypot(
                self.creating_end[0] - self.creating_start[0],
                self.creating_end[1] - self.creating_start[1],
            )
            if length_px >= 3:
                annotation = MeasurementOverride(
                    kind="distance",
                    x1_px=self.creating_start[0],
                    y1_px=self.creating_start[1],
                    x2_px=self.creating_end[0],
                    y2_px=self.creating_end[1],
                )
                self.record_history()
                if self.single_distance:
                    self.annotations = [annotation]
                    self.selected_index = 0
                else:
                    self.annotations.append(annotation)
                    self.selected_index = len(self.annotations) - 1
                self.annotations_changed.emit()
                self.selection_changed.emit()
            self.update()
        self.creating_start = None
        self.creating_end = None
        self.dragging_distance = None
        self.dragging_thickness = None
        self.dragging_whole = None
        self.drag_history_recorded = False
        self.setCursor(Qt.CursorShape.CrossCursor if self.current_tool == "distance" else Qt.CursorShape.ArrowCursor)

    def delete_selected_annotation(self) -> None:
        if not 0 <= self.selected_index < len(self.annotations):
            return
        self.record_history()
        del self.annotations[self.selected_index]
        self.selected_index = -1
        self.annotations_changed.emit()
        self.selection_changed.emit()
        self.update()

    def clear_annotations(self) -> None:
        if not self.annotations:
            return
        self.record_history()
        self.annotations.clear()
        self.selected_index = -1
        self.annotations_changed.emit()
        self.selection_changed.emit()
        self.set_tool("distance")
        self.update()

    def reset_selected_annotation(self) -> None:
        if not self.can_reset_selected():
            return
        reset_annotation = self.auto_thickness or self.fallback_thickness()
        if reset_annotation == self.annotations[self.selected_index]:
            return
        self.record_history()
        self.annotations[self.selected_index] = reset_annotation
        self.annotations_changed.emit()
        self.selection_changed.emit()
        self.update()

    def can_reset_selected(self) -> bool:
        annotation = self.selected_annotation()
        return annotation is not None and annotation.kind == "thickness" and self.auto_thickness is not None

    def info_text(self) -> str:
        annotation = self.selected_annotation()
        if self.distance_label_mode == "pixels":
            if annotation is None and self.annotations:
                annotation = self.annotations[0]
            if annotation is not None:
                return f"标定线长度：{self.measurement_label(annotation)}；拖动端点可微调"
            return "在图像中的已知长度上拖出一条标定线；按住 Shift 可锁定方向"
        thickness_count = 1 if self.thickness_index() >= 0 else 0
        distance_count = self.distance_count()
        if annotation and annotation.kind == "thickness":
            return (
                f"膜厚：上界面 y={annotation.y1_px:.1f}px，"
                f"下界面 y={annotation.y2_px:.1f}px，"
                f"x={annotation.x1_px:.1f}px，厚度 {self.measurement_label(annotation)}；拖动中间竖线整体移动"
            )
        if annotation and annotation.kind == "distance":
            return (
                f"测距 {distance_count} 条，当前 {self.distance_number(self.selected_index)}: "
                f"{self.measurement_label(annotation)}；拖动线段整体移动"
            )
        if self.current_tool == "thickness":
            return "膜厚工具：点击膜厚可新增或选中膜厚标注；拖动红/蓝线或中间竖线调整"
        return f"测距工具：拖拽新增距离标注；当前膜厚 {thickness_count} 条，测距 {distance_count} 条"

    def resizeEvent(self, _event) -> None:
        super().resizeEvent(_event)
        self.clamp_pan()


class CalibrationDialog(QDialog):
    def __init__(
        self,
        path: Path,
        existing_override: CalibrationOverride | None,
        metadata_calibration: Calibration | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = path
        self._result: CalibrationOverride | None = None
        self.setWindowTitle(f"手动标定 - {path.name}")
        self.resize(900, 680)

        with Image.open(path) as image:
            qimage = qimage_from_pil(image)

        annotations = []
        if existing_override is not None:
            annotations.append(
                MeasurementOverride(
                    kind="distance",
                    x1_px=existing_override.x1_px,
                    y1_px=existing_override.y1_px,
                    x2_px=existing_override.x2_px,
                    y2_px=existing_override.y2_px,
                )
            )

        layout = QVBoxLayout(self)
        source_text = (
            f"元数据标定：{metadata_calibration.nm_per_pixel:g} nm/px（{metadata_calibration.source}）"
            if metadata_calibration is not None
            else "元数据标定：未检测到"
        )
        source_label = QLabel(source_text)
        source_label.setWordWrap(True)
        layout.addWidget(source_label)

        instruction = QLabel(
            "在图中沿一段已知实际长度拖出标定线，再输入该长度，例如 1um 或 500nm。"
            "新标定会优先于图片元数据，并同时用于标尺和所有测量值。"
        )
        instruction.setWordWrap(True)
        layout.addWidget(instruction)

        self.preview = MeasurementPreviewWidget(
            qimage,
            1.0,
            annotations,
            None,
            distance_label_mode="pixels",
            single_distance=True,
        )
        self.preview.use_distance_tool()
        if annotations:
            self.preview.select_annotation(0)
        self.preview.annotations_changed.connect(self.update_info)
        self.preview.selection_changed.connect(self.update_info)
        self.preview.zoom_changed.connect(self.update_zoom_label)
        layout.addWidget(self.preview, stretch=1)

        preview_info_row = QHBoxLayout()
        self.info_label = QLabel()
        self.zoom_label = QLabel()
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        reset_zoom_button = QPushButton("重置缩放")
        reset_zoom_button.clicked.connect(self.preview.reset_zoom)
        preview_info_row.addWidget(self.info_label, stretch=1)
        preview_info_row.addWidget(self.zoom_label)
        preview_info_row.addWidget(reset_zoom_button)
        layout.addLayout(preview_info_row)

        form = QFormLayout()
        self.known_length_edit = QLineEdit(
            scale_input_from_nm(existing_override.known_length_nm) if existing_override else "1um"
        )
        self.pixel_length_label = QLabel("未绘制")
        self.calibration_result_label = QLabel("等待标定线")
        form.addRow("已知实际长度", self.known_length_edit)
        form.addRow("标定线像素长度", self.pixel_length_label)
        form.addRow("计算结果", self.calibration_result_label)
        layout.addLayout(form)

        button_row = QHBoxLayout()
        clear_button = QPushButton("清除标定线")
        clear_button.clicked.connect(self.preview.clear_annotations)
        self.apply_button = QPushButton("应用标定")
        self.apply_button.clicked.connect(self.accept)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(clear_button)
        button_row.addStretch()
        button_row.addWidget(self.apply_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

        self.known_length_edit.textChanged.connect(self.update_info)
        self.update_zoom_label(self.preview.zoom_factor)
        self.update_info()

    def distance_annotation(self) -> MeasurementOverride | None:
        for annotation in self.preview.annotations:
            if annotation.kind == "distance":
                return annotation
        return None

    def calculated_override(self) -> CalibrationOverride:
        annotation = self.distance_annotation()
        if annotation is None:
            raise ValueError("请先在图像上拖出一条已知长度的标定线。")
        known_length_nm = parse_scale_nm(self.known_length_edit.text().strip())
        if not math.isfinite(known_length_nm) or known_length_nm <= 0:
            raise ValueError("已知实际长度必须大于 0，例如 1um 或 500nm。")
        result = CalibrationOverride(
            known_length_nm=known_length_nm,
            x1_px=annotation.x1_px,
            y1_px=annotation.y1_px,
            x2_px=annotation.x2_px,
            y2_px=annotation.y2_px,
        )
        _ = result.nm_per_pixel
        return result

    def update_zoom_label(self, zoom_factor: float) -> None:
        self.zoom_label.setText(f"{zoom_factor * 100:.0f}%")

    def update_info(self, *_args) -> None:
        self.info_label.setText(self.preview.info_text())
        annotation = self.distance_annotation()
        if annotation is None:
            self.pixel_length_label.setText("未绘制")
            self.calibration_result_label.setText("等待标定线")
            self.apply_button.setEnabled(False)
            return
        pixel_length = math.hypot(
            annotation.x2_px - annotation.x1_px,
            annotation.y2_px - annotation.y1_px,
        )
        self.pixel_length_label.setText(f"{pixel_length:.3f} px")
        try:
            result = self.calculated_override()
        except ValueError as exc:
            self.calibration_result_label.setText(str(exc))
            self.apply_button.setEnabled(False)
        else:
            self.calibration_result_label.setText(f"{result.nm_per_pixel:.9g} nm/px")
            self.apply_button.setEnabled(True)

    def accept(self) -> None:
        try:
            self._result = self.calculated_override()
        except ValueError as exc:
            QMessageBox.warning(self, "标定参数错误", str(exc))
            return
        super().accept()

    def result_calibration_override(self) -> CalibrationOverride:
        if self._result is None:
            raise ValueError("Manual calibration dialog was not accepted.")
        return self._result


class MeasurementPreviewDialog(QDialog):
    def __init__(
        self,
        path: Path,
        measurement_overrides: list[MeasurementOverride],
        parent: QWidget | None = None,
        calibration: Calibration | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(f"测量标注 - {path.name}")
        self.resize(900, 650)

        with Image.open(path) as image:
            self.image_w, self.image_h = image.size
            selected_calibration = calibration or parse_sem_metadata(image)
            if selected_calibration is None:
                raise ValueError(f"没有在 {path.name} 中找到像素标定信息。")
            self.calibration = selected_calibration
            self.nm_per_pixel = selected_calibration.nm_per_pixel
            qimage = qimage_from_pil(image)

        self.auto_detection_attempted = False
        self.auto_measurement = None

        layout = QVBoxLayout(self)
        tool_row = QHBoxLayout()
        tool_row.addWidget(QLabel("添加/编辑"))
        self.thickness_tool_button = QPushButton("膜厚")
        self.thickness_tool_button.setCheckable(True)
        self.thickness_tool_button.setToolTip("首次点击时运行自动膜厚识别并给出初始建议")
        self.distance_tool_button = QPushButton("测距")
        self.distance_tool_button.setCheckable(True)
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_group.addButton(self.thickness_tool_button)
        self.tool_group.addButton(self.distance_tool_button)
        tool_row.addWidget(self.thickness_tool_button)
        tool_row.addWidget(self.distance_tool_button)
        tool_row.addStretch()
        layout.addLayout(tool_row)

        self.preview = MeasurementPreviewWidget(
            qimage,
            self.nm_per_pixel,
            list(measurement_overrides),
            None,
        )
        self.preview.annotations_changed.connect(self.update_info)
        self.preview.selection_changed.connect(self.update_info)
        self.preview.tool_changed.connect(self.update_tool_buttons)
        self.preview.zoom_changed.connect(self.update_zoom_label)
        layout.addWidget(self.preview, stretch=1)

        info_row = QHBoxLayout()
        self.info_label = QLabel()
        self.zoom_label = QLabel()
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.reset_zoom_button = QPushButton("重置缩放")
        self.reset_zoom_button.clicked.connect(self.preview.reset_zoom)
        info_row.addWidget(self.info_label, stretch=1)
        info_row.addWidget(self.zoom_label)
        info_row.addWidget(self.reset_zoom_button)
        layout.addLayout(info_row)

        button_row = QHBoxLayout()
        self.reset_selected_button = QPushButton("重置选中")
        self.reset_selected_button.clicked.connect(self.preview.reset_selected_annotation)
        self.delete_selected_button = QPushButton("删除选中")
        self.delete_selected_button.clicked.connect(self.preview.delete_selected_annotation)
        self.clear_button = QPushButton("清空标注")
        self.clear_button.clicked.connect(self.preview.clear_annotations)
        self.apply_button = QPushButton("应用")
        self.apply_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.reset_selected_button)
        button_row.addWidget(self.delete_selected_button)
        button_row.addWidget(self.clear_button)
        button_row.addStretch()
        button_row.addWidget(self.apply_button)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

        self.thickness_tool_button.clicked.connect(self.on_thickness_tool_clicked)
        self.distance_tool_button.clicked.connect(self.on_distance_tool_clicked)
        if self.preview.thickness_index() >= 0 and self.preview.distance_count() == 0:
            self.preview.select_annotation(self.preview.thickness_index())
        else:
            self.preview.use_distance_tool()
        self.update_tool_buttons(self.preview.current_tool)
        self.update_zoom_label(self.preview.zoom_factor)
        self.update_info()

    def update_zoom_label(self, zoom_factor: float) -> None:
        self.zoom_label.setText(f"{zoom_factor * 100:.0f}%")

    def update_tool_buttons(self, tool: str) -> None:
        was_thickness_blocked = self.thickness_tool_button.blockSignals(True)
        was_distance_blocked = self.distance_tool_button.blockSignals(True)
        try:
            self.thickness_tool_button.setChecked(tool == "thickness")
            self.distance_tool_button.setChecked(tool == "distance")
        finally:
            self.thickness_tool_button.blockSignals(was_thickness_blocked)
            self.distance_tool_button.blockSignals(was_distance_blocked)

    def update_info(self) -> None:
        self.info_label.setText(self.preview.info_text())
        self.update_mode_controls()

    def ensure_auto_thickness(self) -> None:
        if self.auto_detection_attempted:
            return
        self.thickness_tool_button.setEnabled(False)
        self.info_label.setText("正在自动识别膜厚界面…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            with Image.open(self.path) as image:
                auto_measurement = detect_thickness_measurement(image, self.calibration)
        except Exception as exc:
            QMessageBox.warning(self, "膜厚识别失败", str(exc))
            self.thickness_tool_button.setToolTip("自动识别失败；再次点击可重试")
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.thickness_tool_button.setEnabled(True)

        self.auto_detection_attempted = True
        self.auto_measurement = auto_measurement
        if auto_measurement is None:
            self.preview.auto_thickness = None
            self.thickness_tool_button.setToolTip("未识别到清晰界面；使用默认膜厚建议位置")
            return
        auto_thickness = MeasurementOverride(
            kind="thickness",
            x1_px=auto_measurement.x_center_px,
            y1_px=auto_measurement.top_y_px,
            x2_px=auto_measurement.x_center_px,
            y2_px=auto_measurement.bottom_y_px,
        )
        self.preview.auto_thickness = self.preview.clamped_annotation(auto_thickness)
        self.thickness_tool_button.setToolTip("已加载自动膜厚建议；可拖动界面线继续调整")

    def on_thickness_tool_clicked(self) -> None:
        self.ensure_auto_thickness()
        self.preview.add_or_select_thickness()
        self.update_tool_buttons(self.preview.current_tool)
        self.update_info()

    def on_distance_tool_clicked(self) -> None:
        self.preview.use_distance_tool()
        self.update_tool_buttons(self.preview.current_tool)
        self.update_info()

    def update_mode_controls(self) -> None:
        self.reset_selected_button.setEnabled(self.preview.can_reset_selected())
        self.delete_selected_button.setEnabled(self.preview.selected_annotation() is not None)
        self.clear_button.setEnabled(self.preview.has_annotations())

    def keyPressEvent(self, event) -> None:
        modifiers = event.modifiers()
        control_pressed = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift_pressed = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if control_pressed and event.key() == Qt.Key.Key_Z:
            if shift_pressed:
                self.preview.redo()
            else:
                self.preview.undo()
            event.accept()
            return
        if control_pressed and event.key() == Qt.Key.Key_Y:
            self.preview.redo()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Delete:
            self.preview.delete_selected_annotation()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.preview.cancel_current_drawing():
            event.accept()
            return
        super().keyPressEvent(event)

    def result_measurement_overrides(self) -> list[MeasurementOverride]:
        return list(self.preview.annotations)
