#!/usr/bin/env python3
"""PySide6 GUI for the SEM scale-bar PowerPoint generator."""

from __future__ import annotations

import re
import sys
import os
import math
from pathlib import Path
from typing import TypedDict

from PIL import Image
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sem_scale_ppt import (
    DEFAULTS,
    DEFAULT_MARGIN_INCHES,
    IMAGE_EXTENSIONS,
    MeasurementOverride,
    detect_thickness_measurement,
    format_length_label,
    generate_pptx,
    generate_svg,
    parse_sem_metadata,
    path_key,
    svg_output_path,
)


class GenerateOptions(TypedDict):
    input_path: list[Path]
    output_path: Path
    output_format: str
    fixed_scale: str | None
    auto_scale_preset: str
    position: str
    bar_color: str
    font_size: int | None
    font_bold: bool
    margin_inches: float
    inset_x_percent: float
    inset_y_percent: float
    line_width_pt: float
    measurement_overrides: dict[Path, list[MeasurementOverride]]


POSITIONS = {
    "右下": "bottom-right",
    "左下": "bottom-left",
    "右上": "top-right",
    "左上": "top-left",
}
AUTO_SCALE_PRESETS = {
    "短": "short",
    "中": "medium",
    "长": "long",
}
OUTPUT_FORMATS = {
    "PPTX": "pptx",
    "SVG": "svg",
}
COL_GENERATE = 0
COL_NAME = 1
COL_MEASUREMENT = 2


def natural_key(path: Path) -> list[object]:
    parts = re.split(r"(\d+)", str(path).lower())
    return [int(part) if part.isdigit() else part for part in parts]


def image_paths_from_inputs(paths: list[Path]) -> list[Path]:
    images: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            images.append(path)
        elif path.is_dir():
            images.extend(
                child
                for child in path.rglob("*")
                if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS
            )
    return sorted(images, key=natural_key)


def label_for_value(options: dict[str, str], value: str) -> str:
    for label, option_value in options.items():
        if option_value == value:
            return label
    return next(iter(options))


class FileQueueWidget(QTableWidget):
    paths_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__(0, 3)
        self.setHorizontalHeaderLabels(["生成", "文件", "测量"])
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
            self.paths_dropped.emit(paths)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
        else:
            event.ignore()


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

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)
        layout.addWidget(self.edit_button)

        self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self))

    def set_summary(self, summary: str) -> None:
        marked = summary != "编辑"
        self.edit_button.setText(summary)
        self.edit_button.setMinimumWidth(58 if not marked else 92)
        self.edit_button.setStyleSheet(
            (
                "QPushButton { "
                "background-color: #DDF3E3; "
                "border: 1px solid #8BC79A; "
                "border-radius: 4px; "
                "padding: 2px 8px; "
                "}"
            )
            if marked
            else ""
        )

    def set_marked(self, marked: bool) -> None:
        self.set_summary("已标" if marked else "编辑")


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
    ) -> None:
        super().__init__()
        self.pixmap = QPixmap.fromImage(qimage)
        self.image_w = qimage.width()
        self.image_h = qimage.height()
        self.nm_per_pixel = nm_per_pixel
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
        self.creating_start: tuple[float, float] | None = None
        self.creating_end: tuple[float, float] | None = None
        self.zoom_factor = 1.0
        self.max_zoom_factor = 10.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.setMinimumSize(640, 420)
        self.setMouseTracking(True)

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

    def set_tool(self, tool: str) -> None:
        if tool not in {"thickness", "distance"}:
            return
        if self.current_tool != tool:
            self.current_tool = tool
            self.tool_changed.emit(tool)
        self.dragging_thickness = None
        self.dragging_distance = None
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

    def widget_y_to_image(self, widget_y: float) -> float:
        _ratio_x, ratio = self.widget_to_image_ratio(0.0, widget_y)
        return max(0.0, min(ratio * self.image_h, float(self.image_h - 1)))

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

    def update_annotation(self, index: int, annotation: MeasurementOverride) -> None:
        clamped = self.clamped_annotation(annotation)
        if clamped is None or not 0 <= index < len(self.annotations):
            return
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

    def draw_distance(self, painter: QPainter, annotation: MeasurementOverride, selected: bool, temporary: bool = False) -> None:
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
        new_zoom = self.zoom_factor * (1.18 ** steps)
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
        if event.button() != Qt.MouseButton.LeftButton:
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
                self.dragging_thickness = endpoint
                self.setCursor(Qt.CursorShape.SizeHorCursor if endpoint == "center" else Qt.CursorShape.SizeVerCursor)
            elif endpoint in {"start", "end"}:
                self.dragging_distance = (index, endpoint)
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
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
                self.setCursor(Qt.CursorShape.SizeHorCursor if endpoint == "center" else Qt.CursorShape.SizeVerCursor)
            elif endpoint in {"start", "end"}:
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self.current_tool == "distance":
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, _event) -> None:
        if self.creating_start and self.creating_end:
            length_px = math.hypot(
                self.creating_end[0] - self.creating_start[0],
                self.creating_end[1] - self.creating_start[1],
            )
            if length_px >= 3:
                self.annotations.append(
                    MeasurementOverride(
                        kind="distance",
                        x1_px=self.creating_start[0],
                        y1_px=self.creating_start[1],
                        x2_px=self.creating_end[0],
                        y2_px=self.creating_end[1],
                    )
                )
                self.selected_index = len(self.annotations) - 1
                self.annotations_changed.emit()
                self.selection_changed.emit()
            self.update()
        self.creating_start = None
        self.creating_end = None
        self.dragging_distance = None
        self.dragging_thickness = None
        self.setCursor(Qt.CursorShape.CrossCursor if self.current_tool == "distance" else Qt.CursorShape.ArrowCursor)

    def delete_selected_annotation(self) -> None:
        if not 0 <= self.selected_index < len(self.annotations):
            return
        del self.annotations[self.selected_index]
        self.selected_index = -1
        self.annotations_changed.emit()
        self.selection_changed.emit()
        self.update()

    def clear_annotations(self) -> None:
        if not self.annotations:
            return
        self.annotations.clear()
        self.selected_index = -1
        self.annotations_changed.emit()
        self.selection_changed.emit()
        self.set_tool("distance")
        self.update()

    def reset_selected_annotation(self) -> None:
        if not self.can_reset_selected():
            return
        self.annotations[self.selected_index] = self.auto_thickness or self.fallback_thickness()
        self.annotations_changed.emit()
        self.selection_changed.emit()
        self.update()

    def can_reset_selected(self) -> bool:
        annotation = self.selected_annotation()
        return annotation is not None and annotation.kind == "thickness" and self.auto_thickness is not None

    def info_text(self) -> str:
        annotation = self.selected_annotation()
        thickness_count = 1 if self.thickness_index() >= 0 else 0
        distance_count = self.distance_count()
        if annotation and annotation.kind == "thickness":
            return (
                f"膜厚：上界面 y={annotation.y1_px:.1f}px，"
                f"下界面 y={annotation.y2_px:.1f}px，"
                f"x={annotation.x1_px:.1f}px，厚度 {self.measurement_label(annotation)}"
            )
        if annotation and annotation.kind == "distance":
            return (
                f"测距 {distance_count} 条，当前 {self.distance_number(self.selected_index)}: "
                f"{self.measurement_label(annotation)}"
            )
        if self.current_tool == "thickness":
            return "膜厚工具：点击膜厚可新增或选中膜厚标注；拖动红/蓝线或中间竖线调整"
        return f"测距工具：拖拽新增距离标注；当前膜厚 {thickness_count} 条，测距 {distance_count} 条"

    def resizeEvent(self, _event) -> None:
        super().resizeEvent(_event)
        self.clamp_pan()


class MeasurementPreviewDialog(QDialog):
    def __init__(
        self,
        path: Path,
        measurement_overrides: list[MeasurementOverride],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(f"测量标注 - {path.name}")
        self.resize(900, 650)

        with Image.open(path) as image:
            self.image_w, self.image_h = image.size
            calibration = parse_sem_metadata(image)
            if calibration is None:
                raise ValueError(f"没有在 {path.name} 中找到像素标定信息。")
            self.nm_per_pixel = calibration.nm_per_pixel
            auto_measurement = detect_thickness_measurement(image, calibration)
            rgb = image.convert("RGB")
            self._image_bytes = rgb.tobytes()
            qimage = QImage(
                self._image_bytes,
                self.image_w,
                self.image_h,
                self.image_w * 3,
                QImage.Format.Format_RGB888,
            ).copy()

        self.auto_measurement = auto_measurement
        auto_thickness = (
            MeasurementOverride(
                kind="thickness",
                x1_px=auto_measurement.x_center_px,
                y1_px=auto_measurement.top_y_px,
                x2_px=auto_measurement.x_center_px,
                y2_px=auto_measurement.bottom_y_px,
            )
            if auto_measurement
            else None
        )

        layout = QVBoxLayout(self)
        tool_row = QHBoxLayout()
        tool_row.addWidget(QLabel("添加/编辑"))
        self.thickness_tool_button = QPushButton("膜厚")
        self.thickness_tool_button.setCheckable(True)
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
            auto_thickness,
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

    def on_thickness_tool_clicked(self) -> None:
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

    def result_measurement_overrides(self) -> list[MeasurementOverride]:
        return list(self.preview.annotations)


class GeneratorWorker(QObject):
    finished = Signal(list)
    failed = Signal(str)
    log = Signal(str)

    def __init__(self, options: GenerateOptions) -> None:
        super().__init__()
        self.options = options

    def run(self) -> None:
        try:
            output_format = self.options["output_format"]
            self.log.emit(f"正在生成 {output_format.upper()}...")
            output_paths: list[Path]
            if output_format == "svg":
                items = generate_svg(
                    input_path=self.options["input_path"],
                    output_path=self.options["output_path"],
                    fixed_scale=self.options["fixed_scale"],
                    auto_scale_preset=self.options["auto_scale_preset"],
                    position=self.options["position"],
                    bar_color=self.options["bar_color"],
                    font_size=self.options["font_size"],
                    font_bold=self.options["font_bold"],
                    margin_inches=self.options["margin_inches"],
                    inset_x_percent=self.options["inset_x_percent"],
                    inset_y_percent=self.options["inset_y_percent"],
                    line_width_pt=self.options["line_width_pt"],
                    measurement_overrides=self.options["measurement_overrides"],
                )
                total = len(items)
                output_paths = [
                    svg_output_path(self.options["output_path"], item, index, total)
                    for index, item in enumerate(items, start=1)
                ]
            else:
                items = generate_pptx(
                    input_path=self.options["input_path"],
                    output_path=self.options["output_path"],
                    fixed_scale=self.options["fixed_scale"],
                    auto_scale_preset=self.options["auto_scale_preset"],
                    position=self.options["position"],
                    bar_color=self.options["bar_color"],
                    font_size=self.options["font_size"],
                    font_bold=self.options["font_bold"],
                    margin_inches=self.options["margin_inches"],
                    inset_x_percent=self.options["inset_x_percent"],
                    inset_y_percent=self.options["inset_y_percent"],
                    line_width_pt=self.options["line_width_pt"],
                    measurement_overrides=self.options["measurement_overrides"],
                )
                output_paths = [self.options["output_path"]]
            if len(output_paths) == 1:
                self.log.emit(f"已生成: {output_paths[0]}")
            else:
                self.log.emit(f"已生成 {len(output_paths)} 个 {output_format.upper()}:")
                for path in output_paths:
                    self.log.emit(f"  {path}")
            for item in items:
                self.log.emit(
                    f"{item.source_path.name}: {item.calibration.nm_per_pixel:g} nm/px, "
                    f"标尺 {item.label} = {item.scale_px:.1f} px"
                )
                distance_index = 0
                for measurement in item.measurements:
                    length_px = math.hypot(
                        measurement.x2_px - measurement.x1_px,
                        measurement.y2_px - measurement.y1_px,
                    )
                    if measurement.kind == "thickness":
                        self.log.emit(f"  膜厚: {measurement.label} = {length_px:.1f} px")
                    else:
                        distance_index += 1
                        self.log.emit(f"  测距 {distance_index}: {measurement.label} = {length_px:.1f} px")
            self.finished.emit([str(path) for path in output_paths])
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.thread: QThread | None = None
        self.worker: GeneratorWorker | None = None
        self.measurement_overrides: dict[str, list[MeasurementOverride]] = {}
        self.setWindowTitle("SEM 标尺 PPT 生成器")
        self.resize(DEFAULTS["window_width"], DEFAULTS["window_height"])
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        main_layout = QHBoxLayout()
        root_layout.addLayout(main_layout, stretch=3)

        queue_box = QWidget()
        queue_layout = QVBoxLayout(queue_box)
        queue_layout.setContentsMargins(0, 0, 0, 0)
        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("文件队列"))
        title_row.addStretch()
        self.count_label = QLabel("0 个文件")
        title_row.addWidget(self.count_label)
        queue_layout.addLayout(title_row)

        self.file_list = FileQueueWidget()
        self.file_list.paths_dropped.connect(self.add_paths)
        self.file_list.itemChanged.connect(self.on_queue_item_changed)
        self.file_list.itemDoubleClicked.connect(self.open_original_file)
        self.file_list.customContextMenuRequested.connect(self.show_queue_context_menu)
        queue_layout.addWidget(self.file_list)

        queue_buttons = QHBoxLayout()
        queue_buttons.addWidget(self._button("添加文件", self.choose_input_files))
        queue_buttons.addWidget(self._button("添加文件夹", self.choose_input_folder))
        queue_buttons.addWidget(self._button("自然排序", self.sort_queue))
        queue_layout.addLayout(queue_buttons)

        queue_buttons_2 = QHBoxLayout()
        queue_buttons_2.addWidget(self._button("全选", self.select_all_generate))
        queue_buttons_2.addWidget(self._button("取消全选", self.unselect_all_generate))
        queue_buttons_2.addWidget(self._button("删除选中", self.remove_selected))
        queue_buttons_2.addWidget(self._button("清空", self.clear_queue))
        queue_layout.addLayout(queue_buttons_2)
        main_layout.addWidget(queue_box, stretch=1)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        output_box = QGroupBox("输出")
        output_layout = QGridLayout(output_box)
        self.output_edit = QLineEdit(str(Path.cwd() / DEFAULTS["output_filename"]))
        self.output_format_group = QButtonGroup(self)
        self.output_format_group.setExclusive(True)
        self.output_format_buttons: dict[str, QRadioButton] = {}
        format_row = QHBoxLayout()
        for label in OUTPUT_FORMATS:
            button = QRadioButton(label)
            button.toggled.connect(self.on_output_format_changed)
            self.output_format_group.addButton(button)
            self.output_format_buttons[label] = button
            format_row.addWidget(button)
        format_row.addStretch()
        self.output_format_buttons["PPTX"].setChecked(True)
        output_layout.addWidget(QLabel("输出格式"), 0, 0)
        output_layout.addLayout(format_row, 0, 1)
        output_layout.addWidget(QLabel("输出文件"), 1, 0)
        output_layout.addWidget(self.output_edit, 2, 0)
        output_layout.addWidget(self._button("另存为", self.choose_output), 2, 1)
        right_layout.addWidget(output_box)

        options_box = QGroupBox("标尺设置")
        form = QFormLayout(options_box)
        self.auto_scale_check = QCheckBox("自动选择标尺长度")
        self.auto_scale_check.setChecked(True)
        self.auto_scale_check.toggled.connect(self.on_auto_scale_toggled)
        self.auto_scale_combo = NoWheelComboBox()
        self.auto_scale_combo.addItems(list(AUTO_SCALE_PRESETS.keys()))
        self.auto_scale_combo.setCurrentText(label_for_value(AUTO_SCALE_PRESETS, DEFAULTS["auto_scale_preset"]))
        self.fixed_scale_edit = QLineEdit(DEFAULTS["fixed_scale"])
        self.fixed_scale_edit.setEnabled(False)

        self.position_combo = NoWheelComboBox()
        self.position_combo.addItems(list(POSITIONS.keys()))
        self.position_combo.setCurrentText(label_for_value(POSITIONS, DEFAULTS["scale_position"]))
        self.color_edit = QLineEdit(DEFAULTS["bar_color"])
        self.line_width_spin = NoWheelDoubleSpinBox()
        self.line_width_spin.setRange(0.25, 20.0)
        self.line_width_spin.setDecimals(2)
        self.line_width_spin.setSingleStep(0.25)
        self.line_width_spin.setValue(DEFAULTS["line_width_pt"])
        self.font_spin = NoWheelSpinBox()
        self.font_spin.setRange(0, 72)
        self.font_spin.setValue(DEFAULTS["font_size_pt"])
        self.font_spin.setSpecialValueText("自动")
        self.font_bold_check = QCheckBox("加粗")
        self.font_bold_check.setChecked(bool(DEFAULTS["font_bold"]))
        self.margin_edit = QLineEdit(f"{DEFAULT_MARGIN_INCHES:g}")
        self.inset_x_spin = NoWheelSpinBox()
        self.inset_x_spin.setRange(0, 40)
        self.inset_x_spin.setValue(DEFAULTS["inset_x_percent"])
        self.inset_y_spin = NoWheelSpinBox()
        self.inset_y_spin.setRange(0, 40)
        self.inset_y_spin.setValue(DEFAULTS["inset_y_percent"])

        color_row = QHBoxLayout()
        color_row.addWidget(self.color_edit)
        color_row.addWidget(self._button("取色", self.choose_color))

        form.addRow("长度策略", self.auto_scale_check)
        form.addRow("自动档位", self.auto_scale_combo)
        form.addRow("固定长度", self.fixed_scale_edit)
        form.addRow("角落位置", self.position_combo)
        form.addRow("横向内缩 (%)", self.inset_x_spin)
        form.addRow("纵向内缩 (%)", self.inset_y_spin)
        form.addRow("线宽 pt", self.line_width_spin)
        form.addRow("颜色", color_row)
        form.addRow("字体大小 pt", self.font_spin)
        form.addRow("字体样式", self.font_bold_check)
        form.addRow("页面边距 inch", self.margin_edit)
        right_layout.addWidget(options_box)

        action_row = QHBoxLayout()
        action_row.addStretch()
        self.generate_button = QPushButton("生成 PPTX")
        self.generate_button.clicked.connect(self.generate)
        action_row.addWidget(self.generate_button)
        right_layout.addLayout(action_row)

        right_layout.addStretch()
        main_layout.addWidget(right_panel, stretch=1)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        root_layout.addWidget(self.log_edit, stretch=1)
        self.setCentralWidget(root)

    def _button(self, text: str, callback) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(callback)
        return button

    def current_output_format(self) -> str:
        for label, output_format in OUTPUT_FORMATS.items():
            if self.output_format_buttons[label].isChecked():
                return output_format
        return OUTPUT_FORMATS["PPTX"]

    def current_output_suffix(self) -> str:
        return f".{self.current_output_format()}"

    def on_output_format_changed(self, *_args) -> None:
        suffix = self.current_output_suffix()
        output_text = self.output_edit.text().strip()
        if output_text:
            self.output_edit.setText(str(Path(output_text).with_suffix(suffix)))
        if hasattr(self, "generate_button"):
            self.generate_button.setText(f"生成 {self.current_output_format().upper()}")

    def choose_input_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 SEM 图片文件夹", str(Path.cwd()))
        if path:
            self.add_paths([Path(path)])

    def choose_input_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 SEM 图片",
            str(Path.cwd()),
            "图片 (*.tif *.tiff *.png *.jpg *.jpeg *.bmp)",
        )
        if paths:
            self.add_paths([Path(path) for path in paths])

    def choose_output(self) -> None:
        output_format = self.current_output_format()
        if output_format == "svg":
            file_filter = "SVG (*.svg)"
            title = "保存 SVG"
        else:
            file_filter = "PowerPoint (*.pptx)"
            title = "保存 PPTX"
        path, _ = QFileDialog.getSaveFileName(self, title, self.output_edit.text(), file_filter)
        if path:
            suffix = self.current_output_suffix()
            if Path(path).suffix.lower() != suffix:
                path += suffix
            self.output_edit.setText(path)

    def choose_color(self) -> None:
        color = QColorDialog.getColor(QColor("#FFFFFF"), self, "选择标尺颜色")
        if color.isValid():
            self.color_edit.setText(color.name().upper())

    def open_original_file(self, item: QTableWidgetItem) -> None:
        path = self.item_path(item.row())
        if not path.exists():
            QMessageBox.warning(self, "文件不存在", str(path))
            return
        try:
            os.startfile(path)
        except OSError as exc:
            QMessageBox.warning(self, "无法打开文件", str(exc))

    def add_paths(self, paths: list[Path]) -> None:
        existing = {self.item_path(row).resolve() for row in range(self.file_list.rowCount())}
        added = False
        for path in image_paths_from_inputs(paths):
            resolved = path.resolve()
            if resolved in existing:
                continue
            existing.add(resolved)
            self.add_queue_row(resolved, generate=True)
            added = True
        if added:
            self.refresh_item_labels()
            self.update_count()

    def checkbox_item(self, checked: bool) -> QTableWidgetItem:
        item = QTableWidgetItem()
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    def text_item(self, text: str, path: Path) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setToolTip(str(path))
        return item

    def add_queue_row(self, path: Path, generate: bool) -> None:
        row = self.file_list.rowCount()
        was_blocked = self.file_list.blockSignals(True)
        try:
            self.file_list.insertRow(row)
            self.file_list.setItem(row, COL_GENERATE, self.checkbox_item(generate))
            self.file_list.setItem(row, COL_NAME, self.text_item(path.name, path))
            measurement_cell = MeasurementCellWidget()
            measurement_cell.edit_requested.connect(self.on_measurement_edit_requested)
            self.file_list.setCellWidget(row, COL_MEASUREMENT, measurement_cell)
        finally:
            self.file_list.blockSignals(was_blocked)
        self.update_measurement_status(row)

    def item_path(self, row: int) -> Path:
        item = self.file_list.item(row, COL_NAME)
        if item is None:
            raise ValueError(f"Queue row {row} has no file item.")
        return Path(item.data(Qt.ItemDataRole.UserRole))

    def measurement_cell(self, row: int) -> MeasurementCellWidget | None:
        widget = self.file_list.cellWidget(row, COL_MEASUREMENT)
        return widget if isinstance(widget, MeasurementCellWidget) else None

    def row_for_measurement_cell(self, cell: MeasurementCellWidget) -> int:
        for row in range(self.file_list.rowCount()):
            if self.file_list.cellWidget(row, COL_MEASUREMENT) is cell:
                return row
        return -1

    def row_checked(self, row: int, column: int) -> bool:
        item = self.file_list.item(row, column)
        return item is not None and item.checkState() == Qt.CheckState.Checked

    def set_row_checked(self, row: int, column: int, checked: bool) -> None:
        item = self.file_list.item(row, column)
        if item is not None:
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def update_measurement_status(self, row: int) -> None:
        if row < 0 or row >= self.file_list.rowCount():
            return
        cell = self.measurement_cell(row)
        if cell is None:
            return
        path = self.item_path(row)
        key = path_key(path)
        cell.set_summary(self.measurement_summary(key))

    def measurement_summary(self, key: str) -> str:
        annotations = self.measurement_overrides.get(key, [])
        has_thickness = any(annotation.kind == "thickness" for annotation in annotations)
        distance_count = sum(1 for annotation in annotations if annotation.kind == "distance")
        parts: list[str] = []
        if has_thickness:
            parts.append("膜厚")
        if distance_count:
            parts.append(f"测距 {distance_count}")
        return "+".join(parts) if parts else "编辑"

    def refresh_measurement_statuses(self) -> None:
        for row in range(self.file_list.rowCount()):
            self.update_measurement_status(row)

    def checked_paths(self) -> list[Path]:
        paths: list[Path] = []
        for row in range(self.file_list.rowCount()):
            if self.row_checked(row, COL_GENERATE):
                paths.append(self.item_path(row))
        return paths

    def marked_measurement_paths(self) -> list[Path]:
        paths: list[Path] = []
        for row in range(self.file_list.rowCount()):
            if not self.row_checked(row, COL_GENERATE):
                continue
            path = self.item_path(row)
            key = path_key(path)
            if self.measurement_overrides.get(key):
                paths.append(path)
        return paths

    def selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.file_list.selectedIndexes()}, reverse=True)

    def refresh_item_labels(self) -> None:
        name_counts: dict[str, int] = {}
        for row in range(self.file_list.rowCount()):
            name = self.item_path(row).name.lower()
            name_counts[name] = name_counts.get(name, 0) + 1
        for row in range(self.file_list.rowCount()):
            path = self.item_path(row)
            name_item = self.file_list.item(row, COL_NAME)
            if name_item:
                display_name = path.name
                if name_counts.get(path.name.lower(), 0) > 1:
                    display_name = f"{path.name} [{path.parent.name or path.parent}]"
                name_item.setText(display_name)
                name_item.setToolTip(str(path))

    def update_count(self, *_args) -> None:
        total = self.file_list.rowCount()
        selected = len(self.checked_paths())
        measurement = len(self.marked_measurement_paths())
        marked_annotations = [
            annotation
            for path in self.marked_measurement_paths()
            for annotation in self.measurement_overrides.get(path_key(path), [])
        ]
        thickness = sum(1 for annotation in marked_annotations if annotation.kind == "thickness")
        distance = sum(1 for annotation in marked_annotations if annotation.kind == "distance")
        self.count_label.setText(f"生成 {selected} / 共 {total}，已标 {measurement}，膜厚 {thickness}，测距 {distance}")

    def on_queue_item_changed(self, item: QTableWidgetItem) -> None:
        self.update_count()

    def on_measurement_edit_requested(self, cell: MeasurementCellWidget) -> None:
        row = self.row_for_measurement_cell(cell)
        if row >= 0:
            self.preview_selected_measurement(row)

    def show_queue_context_menu(self, position) -> None:
        row = self.file_list.rowAt(position.y())
        if row >= 0 and row not in self.selected_rows():
            self.file_list.selectRow(row)

        menu = QMenu(self)
        clear_measurement_action = menu.addAction("清除选中测量标注")
        menu.addSeparator()
        remove_action = menu.addAction("删除选中")

        has_selection = bool(self.selected_rows())
        clear_measurement_action.setEnabled(has_selection)
        remove_action.setEnabled(has_selection)

        action = menu.exec(self.file_list.viewport().mapToGlobal(position))
        if action == clear_measurement_action:
            self.clear_selected_measurements()
        elif action == remove_action:
            self.remove_selected()

    def remove_selected(self) -> None:
        for row in self.selected_rows():
            key = path_key(self.item_path(row))
            self.measurement_overrides.pop(key, None)
        for row in self.selected_rows():
            self.file_list.removeRow(row)
        self.refresh_item_labels()
        self.update_count()

    def sort_queue(self) -> None:
        rows = [
            (self.item_path(row), self.row_checked(row, COL_GENERATE))
            for row in range(self.file_list.rowCount())
        ]
        rows.sort(key=lambda row: natural_key(row[0]))
        self.file_list.setRowCount(0)
        for path, generate in rows:
            self.add_queue_row(path, generate=generate)
        self.refresh_item_labels()
        self.refresh_measurement_statuses()
        self.update_count()

    def clear_queue(self) -> None:
        self.file_list.setRowCount(0)
        self.measurement_overrides.clear()
        self.file_list.setHorizontalHeaderLabels(["生成", "文件", "测量"])
        self.update_count()

    def select_all_generate(self) -> None:
        for row in range(self.file_list.rowCount()):
            self.set_row_checked(row, COL_GENERATE, True)
        self.update_count()

    def unselect_all_generate(self) -> None:
        for row in range(self.file_list.rowCount()):
            self.set_row_checked(row, COL_GENERATE, False)
        self.update_count()

    def clear_selected_measurements(self) -> None:
        for row in self.selected_rows():
            key = path_key(self.item_path(row))
            self.measurement_overrides.pop(key, None)
            self.update_measurement_status(row)
        self.update_count()

    def measurement_overrides_for_generation(self) -> dict[Path, list[MeasurementOverride]]:
        overrides: dict[Path, list[MeasurementOverride]] = {}
        for path in self.marked_measurement_paths():
            annotations = self.measurement_overrides.get(path_key(path))
            if annotations:
                overrides[path] = list(annotations)
        return overrides

    def preview_selected_measurement(self, row: int | None = None) -> None:
        if row is None:
            selected_rows = list(reversed(self.selected_rows()))
            if len(selected_rows) != 1:
                QMessageBox.warning(self, "选择图片", "请在队列中只选中一张需要编辑测量标注的图片。")
                return
            row = selected_rows[0]
        path = self.item_path(row)
        key = path_key(path)
        try:
            dialog = MeasurementPreviewDialog(
                path,
                self.measurement_overrides.get(key, []),
                self,
            )
        except Exception as exc:
            QMessageBox.warning(self, "无法编辑测量", str(exc))
            return
        if dialog.exec() == QDialog.DialogCode.Accepted:
            annotations = dialog.result_measurement_overrides()
            if annotations:
                self.measurement_overrides[key] = annotations
            else:
                self.measurement_overrides.pop(key, None)
            self.update_measurement_status(row)
            self.update_count()

    def on_auto_scale_toggled(self, checked: bool) -> None:
        self.auto_scale_combo.setEnabled(checked)
        self.fixed_scale_edit.setEnabled(not checked)

    def generate(self) -> None:
        try:
            input_paths = self.checked_paths()
            if not input_paths:
                raise ValueError("请至少添加并勾选一张 SEM 图片。")
            measurement_overrides = self.measurement_overrides_for_generation()
            output_format = self.current_output_format()
            suffix = f".{output_format}"
            output_text = self.output_edit.text().strip()
            if not output_text:
                raise ValueError("请选择输出文件路径。")
            output_path = Path(output_text)
            if output_path.suffix.lower() != suffix:
                output_path = output_path.with_suffix(suffix)

            margin = float(self.margin_edit.text().strip())
            fixed_scale = None if self.auto_scale_check.isChecked() else self.fixed_scale_edit.text().strip()
            if fixed_scale == "":
                raise ValueError("请输入固定长度，例如 1um 或 500nm。")

            options: GenerateOptions = {
                "input_path": input_paths,
                "output_path": output_path,
                "output_format": output_format,
                "fixed_scale": fixed_scale,
                "auto_scale_preset": AUTO_SCALE_PRESETS[self.auto_scale_combo.currentText()],
                "position": POSITIONS[self.position_combo.currentText()],
                "bar_color": self.color_edit.text().strip(),
                "font_size": self.font_spin.value() or None,
                "font_bold": self.font_bold_check.isChecked(),
                "margin_inches": margin,
                "inset_x_percent": float(self.inset_x_spin.value()),
                "inset_y_percent": float(self.inset_y_spin.value()),
                "line_width_pt": float(self.line_width_spin.value()),
                "measurement_overrides": measurement_overrides,
            }
        except Exception as exc:
            QMessageBox.warning(self, "参数错误", str(exc))
            return

        self.log_edit.clear()
        self.generate_button.setEnabled(False)
        self.thread = QThread()
        self.worker = GeneratorWorker(options)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.append_log)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def append_log(self, message: str) -> None:
        self.log_edit.appendPlainText(message)

    def on_finished(self, output_paths: list[str]) -> None:
        self.generate_button.setEnabled(True)
        output_count = len(output_paths)
        if output_count == 1:
            message = f"文件已生成:\n{output_paths[0]}"
        else:
            output_dir = Path(output_paths[0]).parent
            output_format = Path(output_paths[0]).suffix.upper().lstrip(".")
            message = f"已生成 {output_count} 个 {output_format}:\n{output_dir}"
        QMessageBox.information(self, "完成", message)
        self.thread = None
        self.worker = None

    def on_failed(self, message: str) -> None:
        self.generate_button.setEnabled(True)
        self.append_log(f"错误: {message}")
        QMessageBox.critical(self, "生成失败", message)
        self.thread = None
        self.worker = None


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
