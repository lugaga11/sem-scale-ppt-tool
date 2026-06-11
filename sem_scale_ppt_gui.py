#!/usr/bin/env python3
"""PySide6 GUI for the SEM scale-bar PowerPoint generator."""

from __future__ import annotations

import re
import sys
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from PIL import Image
from PySide6.QtCore import QObject, QRectF, Qt, QThread, Signal
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
    ThicknessOverride,
    detect_thickness_measurement,
    format_thickness_label,
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
    thickness_paths: list[Path]
    thickness_overrides: dict[Path, ThicknessOverride]


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
COL_THICKNESS = 2


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
        self.setHorizontalHeaderLabels(["生成", "文件", "厚度"])
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
        header.setSectionResizeMode(COL_THICKNESS, QHeaderView.ResizeMode.ResizeToContents)
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


class ThicknessCellWidget(QWidget):
    toggled = Signal(object, bool)
    preview_requested = Signal(object)

    def __init__(self, checked: bool) -> None:
        super().__init__()
        self.checkbox = QCheckBox("测厚")
        self.checkbox.setChecked(checked)
        self.preview_button = QPushButton("微调")
        self.preview_button.setFixedWidth(58)
        self.preview_button.setEnabled(checked)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(6)
        layout.addWidget(self.checkbox)
        layout.addWidget(self.preview_button)

        self.checkbox.toggled.connect(lambda value: self.toggled.emit(self, value))
        self.preview_button.clicked.connect(lambda: self.preview_requested.emit(self))

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def set_checked(self, checked: bool) -> None:
        self.checkbox.setChecked(checked)
        self.preview_button.setEnabled(checked)

    def set_adjusted(self, adjusted: bool) -> None:
        self.preview_button.setText("已微调" if adjusted else "微调")


class ThicknessPreviewWidget(QWidget):
    lines_changed = Signal(float, float)

    def __init__(self, qimage: QImage, top_y: float, bottom_y: float) -> None:
        super().__init__()
        self.pixmap = QPixmap.fromImage(qimage)
        self.image_w = qimage.width()
        self.image_h = qimage.height()
        self.top_y = top_y
        self.bottom_y = bottom_y
        self.dragging: str | None = None
        self.zoom_factor = 1.0
        self.max_zoom_factor = 10.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.setMinimumSize(640, 420)
        self.setMouseTracking(True)

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

    def image_y_to_widget(self, image_y: float) -> float:
        rect = self.image_rect()
        return rect.top() + image_y / self.image_h * rect.height()

    def widget_y_to_image(self, widget_y: float) -> float:
        _ratio_x, ratio = self.widget_to_image_ratio(0.0, widget_y)
        return max(0.0, min(ratio * self.image_h, float(self.image_h - 1)))

    def set_lines(self, top_y: float, bottom_y: float) -> None:
        self.top_y = max(0.0, min(float(top_y), float(self.image_h - 1)))
        self.bottom_y = max(0.0, min(float(bottom_y), float(self.image_h - 1)))
        if self.bottom_y < self.top_y:
            self.top_y, self.bottom_y = self.bottom_y, self.top_y
        if self.bottom_y <= self.top_y:
            self.bottom_y = min(float(self.image_h - 1), self.top_y + 1)
        self.lines_changed.emit(self.top_y, self.bottom_y)
        self.update()

    def nearest_line(self, widget_y: float) -> str | None:
        top_distance = abs(widget_y - self.image_y_to_widget(self.top_y))
        bottom_distance = abs(widget_y - self.image_y_to_widget(self.bottom_y))
        nearest = "top" if top_distance <= bottom_distance else "bottom"
        if min(top_distance, bottom_distance) <= 10:
            return nearest
        return None

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#151515"))
        rect = self.image_rect()
        painter.drawPixmap(rect.toRect(), self.pixmap)

        top_line_y = self.image_y_to_widget(self.top_y)
        bottom_line_y = self.image_y_to_widget(self.bottom_y)
        top_pen = QPen(QColor("#00D1FF"), 3)
        bottom_pen = QPen(QColor("#FF4D4D"), 3)

        painter.setPen(top_pen)
        painter.drawLine(int(rect.left()), int(top_line_y), int(rect.right()), int(top_line_y))
        label_x = max(8, int(rect.left()) + 8)
        painter.drawText(label_x, int(top_line_y) - 8, "上界面")
        painter.setPen(bottom_pen)
        painter.drawLine(int(rect.left()), int(bottom_line_y), int(rect.right()), int(bottom_line_y))
        painter.drawText(label_x, int(bottom_line_y) - 8, "下界面")
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
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        line = self.nearest_line(event.position().y())
        if line:
            self.dragging = line
            self.setCursor(Qt.CursorShape.SizeVerCursor)

    def mouseMoveEvent(self, event) -> None:
        if self.dragging:
            image_y = self.widget_y_to_image(event.position().y())
            if self.dragging == "top":
                self.top_y = min(image_y, self.bottom_y - 1)
            else:
                self.bottom_y = max(image_y, self.top_y + 1)
            self.lines_changed.emit(self.top_y, self.bottom_y)
            self.update()
            return
        self.setCursor(Qt.CursorShape.SizeVerCursor if self.nearest_line(event.position().y()) else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, _event) -> None:
        self.dragging = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def resizeEvent(self, _event) -> None:
        super().resizeEvent(_event)
        self.clamp_pan()


class ThicknessPreviewDialog(QDialog):
    def __init__(self, path: Path, override: ThicknessOverride | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.override = override
        self.setWindowTitle(f"预览/微调厚度 - {path.name}")
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
        self.x_center_px = (
            override.x_center_px
            if override and override.x_center_px is not None
            else auto_measurement.x_center_px
            if auto_measurement
            else self.image_w / 2
        )
        if override:
            top_y = override.top_y_px
            bottom_y = override.bottom_y_px
        elif auto_measurement:
            top_y = auto_measurement.top_y_px
            bottom_y = auto_measurement.bottom_y_px
        else:
            top_y = self.image_h * 0.4
            bottom_y = self.image_h * 0.6

        layout = QVBoxLayout(self)
        self.preview = ThicknessPreviewWidget(qimage, top_y, bottom_y)
        self.preview.lines_changed.connect(self.update_label)
        layout.addWidget(self.preview, stretch=1)

        self.info_label = QLabel()
        layout.addWidget(self.info_label)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.reset_button = QPushButton("重置为自动识别")
        self.reset_button.clicked.connect(self.reset_to_auto)
        self.apply_button = QPushButton("应用")
        self.apply_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.reset_button)
        button_row.addWidget(self.apply_button)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

        self.reset_button.setEnabled(self.auto_measurement is not None)
        self.update_label(self.preview.top_y, self.preview.bottom_y)

    def update_label(self, top_y: float, bottom_y: float) -> None:
        thickness_nm = abs(bottom_y - top_y) * self.nm_per_pixel
        self.info_label.setText(
            f"上界面 y={top_y:.1f}px，下界面 y={bottom_y:.1f}px，厚度 {format_thickness_label(thickness_nm)}"
        )

    def reset_to_auto(self) -> None:
        if not self.auto_measurement:
            return
        self.x_center_px = self.auto_measurement.x_center_px
        self.preview.set_lines(self.auto_measurement.top_y_px, self.auto_measurement.bottom_y_px)

    def result_override(self) -> ThicknessOverride:
        return ThicknessOverride(
            top_y_px=self.preview.top_y,
            bottom_y_px=self.preview.bottom_y,
            x_center_px=self.x_center_px,
        )


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
                    thickness_paths=self.options["thickness_paths"],
                    thickness_overrides=self.options["thickness_overrides"],
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
                    thickness_paths=self.options["thickness_paths"],
                    thickness_overrides=self.options["thickness_overrides"],
                )
                output_paths = [self.options["output_path"]]
            if len(output_paths) == 1:
                self.log.emit(f"已生成: {output_paths[0]}")
            else:
                self.log.emit(f"已生成 {len(output_paths)} 个 {output_format.upper()}:")
                for path in output_paths:
                    self.log.emit(f"  {path}")
            thickness_requested = {path.resolve() for path in self.options["thickness_paths"]}
            for item in items:
                self.log.emit(
                    f"{item.source_path.name}: {item.calibration.nm_per_pixel:g} nm/px, "
                    f"标尺 {item.label} = {item.scale_px:.1f} px"
                )
                if item.source_path.resolve() in thickness_requested:
                    if item.thickness:
                        self.log.emit(
                            f"  截面厚度 {item.thickness.label} = "
                            f"{item.thickness.bottom_y_px - item.thickness.top_y_px:.1f} px, "
                            f"置信度 {item.thickness.confidence:.2f}"
                        )
                    else:
                        self.log.emit("  未识别到可靠的截面厚度界面。")
            self.finished.emit([str(path) for path in output_paths])
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.thread: QThread | None = None
        self.worker: GeneratorWorker | None = None
        self.open_after_generate = False
        self.thickness_overrides: dict[str, ThicknessOverride] = {}
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
        queue_layout.addLayout(queue_buttons)

        queue_buttons_2 = QHBoxLayout()
        queue_buttons_2.addWidget(self._button("删除选中", self.remove_selected))
        queue_buttons_2.addWidget(self._button("自然排序", self.sort_queue))
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
        self.generate_button.clicked.connect(lambda: self.generate(open_after=False))
        self.generate_open_button = QPushButton("生成并打开")
        self.generate_open_button.clicked.connect(lambda: self.generate(open_after=True))
        action_row.addWidget(self.generate_button)
        action_row.addWidget(self.generate_open_button)
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
            self.add_queue_row(resolved, generate=True, thickness=False)
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

    def add_queue_row(self, path: Path, generate: bool, thickness: bool) -> None:
        row = self.file_list.rowCount()
        was_blocked = self.file_list.blockSignals(True)
        try:
            self.file_list.insertRow(row)
            self.file_list.setItem(row, COL_GENERATE, self.checkbox_item(generate))
            self.file_list.setItem(row, COL_NAME, self.text_item(path.name, path))
            thickness_cell = ThicknessCellWidget(thickness)
            thickness_cell.toggled.connect(self.on_thickness_cell_toggled)
            thickness_cell.preview_requested.connect(self.on_thickness_preview_requested)
            self.file_list.setCellWidget(row, COL_THICKNESS, thickness_cell)
        finally:
            self.file_list.blockSignals(was_blocked)
        self.update_thickness_status(row)

    def item_path(self, row: int) -> Path:
        item = self.file_list.item(row, COL_NAME)
        if item is None:
            raise ValueError(f"Queue row {row} has no file item.")
        return Path(item.data(Qt.ItemDataRole.UserRole))

    def thickness_cell(self, row: int) -> ThicknessCellWidget | None:
        widget = self.file_list.cellWidget(row, COL_THICKNESS)
        return widget if isinstance(widget, ThicknessCellWidget) else None

    def row_for_thickness_cell(self, cell: ThicknessCellWidget) -> int:
        for row in range(self.file_list.rowCount()):
            if self.file_list.cellWidget(row, COL_THICKNESS) is cell:
                return row
        return -1

    def row_checked(self, row: int, column: int) -> bool:
        if column == COL_THICKNESS:
            cell = self.thickness_cell(row)
            return cell.is_checked() if cell else False
        item = self.file_list.item(row, column)
        return item is not None and item.checkState() == Qt.CheckState.Checked

    def set_row_checked(self, row: int, column: int, checked: bool) -> None:
        if column == COL_THICKNESS:
            cell = self.thickness_cell(row)
            if cell is not None:
                cell.set_checked(checked)
                self.update_thickness_status(row)
            return
        item = self.file_list.item(row, column)
        if item is not None:
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def update_thickness_status(self, row: int) -> None:
        if row < 0 or row >= self.file_list.rowCount():
            return
        cell = self.thickness_cell(row)
        if cell is None:
            return
        path = self.item_path(row)
        cell.set_adjusted(path_key(path) in self.thickness_overrides)
        cell.preview_button.setEnabled(cell.is_checked())

    def refresh_thickness_statuses(self) -> None:
        for row in range(self.file_list.rowCount()):
            self.update_thickness_status(row)

    def checked_paths(self) -> list[Path]:
        paths: list[Path] = []
        for row in range(self.file_list.rowCount()):
            if self.row_checked(row, COL_GENERATE):
                paths.append(self.item_path(row))
        return paths

    def thickness_paths(self) -> list[Path]:
        paths: list[Path] = []
        for row in range(self.file_list.rowCount()):
            if self.row_checked(row, COL_GENERATE) and self.row_checked(row, COL_THICKNESS):
                paths.append(self.item_path(row))
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
        thickness = len(self.thickness_paths())
        self.count_label.setText(f"生成 {selected} / 共 {total}，测厚 {thickness}")

    def on_queue_item_changed(self, item: QTableWidgetItem) -> None:
        self.update_count()

    def on_thickness_cell_toggled(self, cell: ThicknessCellWidget, _checked: bool) -> None:
        row = self.row_for_thickness_cell(cell)
        if row >= 0:
            self.update_thickness_status(row)
            self.update_count()

    def on_thickness_preview_requested(self, cell: ThicknessCellWidget) -> None:
        row = self.row_for_thickness_cell(cell)
        if row >= 0:
            self.preview_selected_thickness(row)

    def show_queue_context_menu(self, position) -> None:
        row = self.file_list.rowAt(position.y())
        if row >= 0 and row not in self.selected_rows():
            self.file_list.selectRow(row)

        menu = QMenu(self)
        mark_action = menu.addAction("选中设为测厚")
        unmark_action = menu.addAction("选中取消测厚")
        clear_thickness_action = menu.addAction("全部取消测厚")
        menu.addSeparator()
        remove_action = menu.addAction("删除选中")

        has_selection = bool(self.selected_rows())
        mark_action.setEnabled(has_selection)
        unmark_action.setEnabled(has_selection)
        remove_action.setEnabled(has_selection)

        action = menu.exec(self.file_list.viewport().mapToGlobal(position))
        if action == mark_action:
            self.mark_selected_thickness()
        elif action == unmark_action:
            self.unmark_selected_thickness()
        elif action == clear_thickness_action:
            self.clear_all_thickness()
        elif action == remove_action:
            self.remove_selected()

    def remove_selected(self) -> None:
        for row in self.selected_rows():
            self.thickness_overrides.pop(path_key(self.item_path(row)), None)
        for row in self.selected_rows():
            self.file_list.removeRow(row)
        self.refresh_item_labels()
        self.update_count()

    def sort_queue(self) -> None:
        rows = [
            (self.item_path(row), self.row_checked(row, COL_GENERATE), self.row_checked(row, COL_THICKNESS))
            for row in range(self.file_list.rowCount())
        ]
        rows.sort(key=lambda row: natural_key(row[0]))
        self.file_list.setRowCount(0)
        for path, generate, thickness in rows:
            self.add_queue_row(path, generate=generate, thickness=thickness)
        self.refresh_item_labels()
        self.refresh_thickness_statuses()
        self.update_count()

    def clear_queue(self) -> None:
        self.file_list.setRowCount(0)
        self.thickness_overrides.clear()
        self.file_list.setHorizontalHeaderLabels(["生成", "文件", "厚度"])
        self.update_count()

    def mark_selected_thickness(self) -> None:
        for row in self.selected_rows():
            self.set_row_checked(row, COL_THICKNESS, True)
        self.update_count()

    def unmark_selected_thickness(self) -> None:
        for row in self.selected_rows():
            self.set_row_checked(row, COL_THICKNESS, False)
        self.update_count()

    def clear_all_thickness(self) -> None:
        for row in range(self.file_list.rowCount()):
            self.set_row_checked(row, COL_THICKNESS, False)
        self.update_count()

    def thickness_overrides_for_generation(self) -> dict[Path, ThicknessOverride]:
        overrides: dict[Path, ThicknessOverride] = {}
        for path in self.thickness_paths():
            override = self.thickness_overrides.get(path_key(path))
            if override is not None:
                overrides[path] = override
        return overrides

    def preview_selected_thickness(self, row: int | None = None) -> None:
        if row is None:
            selected_rows = list(reversed(self.selected_rows()))
            if len(selected_rows) != 1:
                QMessageBox.warning(self, "选择图片", "请在队列中只选中一张需要微调厚度的图片。")
                return
            row = selected_rows[0]
        path = self.item_path(row)
        key = path_key(path)
        self.set_row_checked(row, COL_THICKNESS, True)
        try:
            dialog = ThicknessPreviewDialog(path, self.thickness_overrides.get(key), self)
        except Exception as exc:
            QMessageBox.warning(self, "无法预览厚度", str(exc))
            return
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.thickness_overrides[key] = dialog.result_override()
            self.update_thickness_status(row)
            self.update_count()

    def on_auto_scale_toggled(self, checked: bool) -> None:
        self.auto_scale_combo.setEnabled(checked)
        self.fixed_scale_edit.setEnabled(not checked)

    def temporary_output_path(self, output_format: str) -> Path:
        output_dir = Path(tempfile.gettempdir()) / "sem_scale_ppt"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return output_dir / f"sem_scale_{timestamp}.{output_format}"

    def generate(self, open_after: bool = False) -> None:
        try:
            input_paths = self.checked_paths()
            if not input_paths:
                raise ValueError("请至少添加并勾选一张 SEM 图片。")
            thickness_paths = self.thickness_paths()
            thickness_overrides = self.thickness_overrides_for_generation()
            output_format = self.current_output_format()
            suffix = f".{output_format}"
            if open_after:
                output_path = self.temporary_output_path(output_format)
            else:
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
                "thickness_paths": thickness_paths,
                "thickness_overrides": thickness_overrides,
            }
        except Exception as exc:
            QMessageBox.warning(self, "参数错误", str(exc))
            return

        self.log_edit.clear()
        self.generate_button.setEnabled(False)
        self.generate_open_button.setEnabled(False)
        self.open_after_generate = open_after
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
        self.generate_open_button.setEnabled(True)
        output_count = len(output_paths)
        if self.open_after_generate:
            target_path = Path(output_paths[0]).parent if output_count > 1 else Path(output_paths[0])
            try:
                os.startfile(target_path)
            except OSError as exc:
                QMessageBox.warning(self, "无法打开文件", str(exc))
            else:
                if output_count > 1:
                    self.append_log("已打开输出文件夹。")
                else:
                    self.append_log("已打开临时文件。请在对应软件中手动另存为需要的位置。")
        else:
            if output_count == 1:
                message = f"文件已生成:\n{output_paths[0]}"
            else:
                output_dir = Path(output_paths[0]).parent
                output_format = Path(output_paths[0]).suffix.upper().lstrip(".")
                message = f"已生成 {output_count} 个 {output_format}:\n{output_dir}"
            QMessageBox.information(self, "完成", message)
        self.thread = None
        self.worker = None
        self.open_after_generate = False

    def on_failed(self, message: str) -> None:
        self.generate_button.setEnabled(True)
        self.generate_open_button.setEnabled(True)
        self.append_log(f"错误: {message}")
        QMessageBox.critical(self, "生成失败", message)
        self.thread = None
        self.worker = None
        self.open_after_generate = False


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
