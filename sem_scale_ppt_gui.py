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

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from sem_scale_ppt import (
    DEFAULTS,
    DEFAULT_MARGIN_INCHES,
    IMAGE_EXTENSIONS,
    generate_pptx,
    generate_svg,
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


class FileQueueWidget(QListWidget):
    paths_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setMinimumHeight(DEFAULTS["queue_min_height"])
        self.setStyleSheet(
            """
            QListWidget {
                border: 1px solid #7aa7ff;
                border-radius: 8px;
                padding: 6px;
            }
            QListWidget::item {
                padding: 5px;
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
            self.finished.emit([str(path) for path in output_paths])
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.thread: QThread | None = None
        self.worker: GeneratorWorker | None = None
        self.open_after_generate = False
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
        self.file_list.itemChanged.connect(self.update_count)
        self.file_list.itemDoubleClicked.connect(self.open_original_file)
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

    def open_original_file(self, item: QListWidgetItem) -> None:
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        if not path.exists():
            QMessageBox.warning(self, "文件不存在", str(path))
            return
        try:
            os.startfile(path)
        except OSError as exc:
            QMessageBox.warning(self, "无法打开文件", str(exc))

    def add_paths(self, paths: list[Path]) -> None:
        existing = {self.item_path(index).resolve() for index in range(self.file_list.count())}
        added = False
        for path in image_paths_from_inputs(paths):
            resolved = path.resolve()
            if resolved in existing:
                continue
            existing.add(resolved)
            self.file_list.addItem(self.create_queue_item(resolved, checked=True))
            added = True
        if added:
            self.refresh_item_labels()
            self.update_count()

    def create_queue_item(self, path: Path, checked: bool) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        item.setFlags(
            item.flags()
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        return item

    def item_path(self, index: int) -> Path:
        return Path(self.file_list.item(index).data(Qt.ItemDataRole.UserRole))

    def checked_paths(self) -> list[Path]:
        paths: list[Path] = []
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                paths.append(Path(item.data(Qt.ItemDataRole.UserRole)))
        return paths

    def refresh_item_labels(self) -> None:
        for index in range(self.file_list.count()):
            path = self.item_path(index)
            item = self.file_list.item(index)
            item.setText(f"{index + 1}. {path.name}    [{path.parent}]")
            item.setToolTip(str(path))

    def update_count(self) -> None:
        total = self.file_list.count()
        selected = len(self.checked_paths())
        self.count_label.setText(f"已选 {selected} / 共 {total}")

    def remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))
        self.refresh_item_labels()
        self.update_count()

    def sort_queue(self) -> None:
        paths = sorted((self.item_path(index) for index in range(self.file_list.count())), key=natural_key)
        checked = {path.resolve() for path in self.checked_paths()}
        self.file_list.clear()
        for path in paths:
            self.file_list.addItem(self.create_queue_item(path, checked=path.resolve() in checked))
        self.refresh_item_labels()
        self.update_count()

    def clear_queue(self) -> None:
        self.file_list.clear()
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
