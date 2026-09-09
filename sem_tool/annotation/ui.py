from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStyle,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..files import IMAGE_FILE_FILTER, image_paths_from_inputs, natural_key, open_path, path_key
from .core import (
    DEFAULT_MARGIN_INCHES,
    DEFAULTS,
    Calibration,
    CalibrationOverride,
    MeasurementOverride,
    choose_scale_nm,
    parse_scale_nm,
    parse_sem_metadata,
    validate_generation_options,
)
from .preview import CalibrationDialog, MeasurementPreviewDialog, scale_input_from_nm
from .queue import (
    COL_GENERATE,
    COL_MEASUREMENT,
    COL_NAME,
    FileQueueWidget,
    MeasurementCellWidget,
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
)
from .worker import GenerateOptions, GeneratorWorker

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


def label_for_value(options: dict[str, str], value: str) -> str:
    for label, option_value in options.items():
        if option_value == value:
            return label
    return next(iter(options))


class AnnotationPanel(QWidget):
    queue_changed = Signal(int)
    busy_changed = Signal(bool)
    log_message = Signal(str)
    log_reset = Signal()
    log_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.thread: QThread | None = None
        self.worker: GeneratorWorker | None = None
        self.measurement_overrides: dict[str, list[MeasurementOverride]] = {}
        self.calibration_overrides: dict[str, CalibrationOverride] = {}
        self.metadata_calibrations: dict[str, Calibration | None] = {}
        self.calibration_errors: dict[str, str] = {}
        self.closing = False
        self._build_ui()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        main_layout = QHBoxLayout()
        root_layout.addLayout(main_layout)

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
        self.file_list.rows_reorder_requested.connect(self.reorder_queue_rows)
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
        output_layout = QVBoxLayout(output_box)
        self.output_edit = QLineEdit(str(Path.cwd() / DEFAULTS["output_filename"]))
        self.output_format_group = QButtonGroup(self)
        self.output_format_group.setExclusive(True)
        self.output_format_buttons: dict[str, QRadioButton] = {}
        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("输出格式"))
        for label in OUTPUT_FORMATS:
            button = QRadioButton(label)
            self.output_format_group.addButton(button)
            self.output_format_buttons[label] = button
            format_row.addWidget(button)
        format_row.addStretch()
        self.output_format_buttons["PPTX"].setChecked(True)
        output_layout.addLayout(format_row)
        output_layout.addWidget(QLabel("输出文件"))
        output_path_row = QHBoxLayout()
        output_path_row.addWidget(self.output_edit, 1)
        output_path_row.addWidget(self._button("另存为", self.choose_output))
        output_layout.addLayout(output_path_row)
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
        self.output_format_group.buttonToggled.connect(self.on_output_format_changed)
        action_row.addWidget(self.generate_button)
        right_layout.addLayout(action_row)

        right_layout.addStretch()
        main_layout.addWidget(right_panel, stretch=1)

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
            IMAGE_FILE_FILTER,
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
            open_path(path)
        except OSError as exc:
            QMessageBox.warning(self, "无法打开文件", str(exc))

    def add_paths(self, paths: list[Path]) -> None:
        existing = {path_key(self.item_path(row)) for row in range(self.file_list.rowCount())}
        added = False
        for path in image_paths_from_inputs(paths):
            resolved = path.resolve()
            if path_key(resolved) in existing:
                continue
            existing.add(path_key(resolved))
            self.add_queue_row(resolved, generate=True)
            added = True
        if added:
            self.refresh_item_labels()
            self.update_count()

    def select_path(self, path: Path) -> int:
        key = path_key(path)
        for row in range(self.file_list.rowCount()):
            if path_key(self.item_path(row)) == key:
                self.file_list.selectRow(row)
                self.file_list.scrollToItem(self.file_list.item(row, COL_NAME))
                return row
        return -1

    def inspect_metadata_calibration(self, path: Path, force: bool = False) -> None:
        key = path_key(path)
        if not force and key in self.metadata_calibrations:
            return
        try:
            with Image.open(path) as image:
                calibration = parse_sem_metadata(image)
        except (OSError, ValueError) as exc:
            calibration = None
            self.calibration_errors[key] = str(exc)
        else:
            self.calibration_errors.pop(key, None)
        self.metadata_calibrations[key] = calibration

    def effective_calibration(self, path: Path) -> Calibration | None:
        key = path_key(path)
        override = self.calibration_overrides.get(key)
        if override is not None:
            return Calibration(
                override.nm_per_pixel,
                f"手动标定：{override.known_length_nm:g} nm / {override.pixel_length:.1f} px",
            )
        self.inspect_metadata_calibration(path)
        return self.metadata_calibrations.get(key)

    def missing_calibration_paths(self, paths: list[Path]) -> list[Path]:
        return [path for path in paths if self.effective_calibration(path) is None]

    def update_file_calibration_status(self, row: int) -> None:
        if row < 0 or row >= self.file_list.rowCount():
            return
        item = self.file_list.item(row, COL_NAME)
        if item is None:
            return
        path = self.item_path(row)
        key = path_key(path)
        override = self.calibration_overrides.get(key)
        calibration = self.effective_calibration(path)
        measurement_cell = self.measurement_cell(row)
        if measurement_cell is not None:
            measurement_cell.set_calibration_available(calibration is not None)
        if override is not None:
            status = (
                f"手动标定：{override.nm_per_pixel:.9g} nm/px\n"
                f"已知长度：{scale_input_from_nm(override.known_length_nm)}\n"
                f"标定线：{override.pixel_length:.3f} px"
            )
            item.setIcon(QIcon())
        elif calibration is not None:
            status = f"元数据标定：{calibration.nm_per_pixel:g} nm/px\n来源：{calibration.source}"
            item.setIcon(QIcon())
        else:
            error = self.calibration_errors.get(key)
            detail = f"\n读取错误：{error}" if error else ""
            status = f"未检测到像素标定。请右键此图片选择“手动标定…”{detail}"
            item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning))
        item.setToolTip(f"{path}\n\n{status}")

    def checkbox_item(self, checked: bool) -> QTableWidgetItem:
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
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
        self.inspect_metadata_calibration(path)
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
        self.update_file_calibration_status(row)

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
                self.update_file_calibration_status(row)

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
        missing_calibration = len(self.missing_calibration_paths(self.checked_paths()))
        missing_text = f"，缺标定 {missing_calibration}" if missing_calibration else ""
        self.count_label.setText(
            f"生成 {selected} / 共 {total}，已标 {measurement}，膜厚 {thickness}，测距 {distance}{missing_text}"
        )
        self.queue_changed.emit(total)

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
        manual_calibration_action = menu.addAction("手动标定…")
        restore_metadata_action = menu.addAction("恢复元数据标定")
        menu.addSeparator()
        clear_measurement_action = menu.addAction("清除选中测量标注")
        menu.addSeparator()
        remove_action = menu.addAction("删除选中")

        selected_rows = self.selected_rows()
        has_selection = bool(selected_rows)
        selected_key = path_key(self.item_path(selected_rows[0])) if len(selected_rows) == 1 else ""
        manual_calibration_action.setEnabled(len(selected_rows) == 1)
        restore_metadata_action.setEnabled(bool(selected_key) and selected_key in self.calibration_overrides)
        clear_measurement_action.setEnabled(has_selection)
        remove_action.setEnabled(has_selection)

        action = menu.exec(self.file_list.viewport().mapToGlobal(position))
        if action == manual_calibration_action:
            self.calibrate_selected()
        elif action == restore_metadata_action:
            self.clear_selected_manual_calibrations()
        elif action == clear_measurement_action:
            self.clear_selected_measurements()
        elif action == remove_action:
            self.remove_selected()

    def remove_selected(self) -> None:
        for row in self.selected_rows():
            key = path_key(self.item_path(row))
            self.measurement_overrides.pop(key, None)
            self.calibration_overrides.pop(key, None)
            self.metadata_calibrations.pop(key, None)
            self.calibration_errors.pop(key, None)
        for row in self.selected_rows():
            self.file_list.removeRow(row)
        self.refresh_item_labels()
        self.update_count()

    def sort_queue(self) -> None:
        rows = [(self.item_path(row), self.row_checked(row, COL_GENERATE)) for row in range(self.file_list.rowCount())]
        rows.sort(key=lambda row: natural_key(row[0]))
        self.file_list.setRowCount(0)
        for path, generate in rows:
            self.add_queue_row(path, generate=generate)
        self.refresh_item_labels()
        self.refresh_measurement_statuses()
        self.update_count()

    def reorder_queue_rows(self, rows: list[int], drop_row: int) -> None:
        row_count = self.file_list.rowCount()
        rows = sorted({row for row in rows if 0 <= row < row_count})
        if not rows:
            return

        queue_rows = [(self.item_path(row), self.row_checked(row, COL_GENERATE)) for row in range(row_count)]
        moved_rows = [queue_rows[row] for row in rows]
        remaining_rows = [row for index, row in enumerate(queue_rows) if index not in rows]
        insert_at = max(0, min(drop_row, row_count))
        insert_at -= sum(1 for row in rows if row < insert_at)
        insert_at = max(0, min(insert_at, len(remaining_rows)))

        reordered_rows = remaining_rows[:insert_at] + moved_rows + remaining_rows[insert_at:]
        if reordered_rows == queue_rows:
            return

        self.file_list.setRowCount(0)
        for path, generate in reordered_rows:
            self.add_queue_row(path, generate=generate)
        self.refresh_item_labels()
        self.refresh_measurement_statuses()
        self.file_list.clearSelection()
        for row in range(insert_at, insert_at + len(moved_rows)):
            for column in range(self.file_list.columnCount()):
                item = self.file_list.item(row, column)
                if item is not None:
                    item.setSelected(True)
        self.update_count()

    def clear_queue(self) -> None:
        self.file_list.setRowCount(0)
        self.measurement_overrides.clear()
        self.calibration_overrides.clear()
        self.metadata_calibrations.clear()
        self.calibration_errors.clear()
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

    def calibrate_selected(self) -> None:
        selected_rows = list(reversed(self.selected_rows()))
        if len(selected_rows) != 1:
            QMessageBox.warning(self, "选择图片", "请在队列中选择一张需要手动标定的图片。")
            return
        row = selected_rows[0]
        path = self.item_path(row)
        key = path_key(path)
        self.inspect_metadata_calibration(path)
        try:
            dialog = CalibrationDialog(
                path,
                self.calibration_overrides.get(key),
                self.metadata_calibrations.get(key),
                self,
            )
        except Exception as exc:
            QMessageBox.warning(self, "无法手动标定", str(exc))
            return
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.calibration_overrides[key] = dialog.result_calibration_override()
            self.update_file_calibration_status(row)
            self.update_count()

    def clear_selected_manual_calibrations(self) -> None:
        for row in self.selected_rows():
            key = path_key(self.item_path(row))
            self.calibration_overrides.pop(key, None)
            self.update_file_calibration_status(row)
        self.update_count()

    def measurement_overrides_for_generation(self) -> dict[Path, list[MeasurementOverride]]:
        overrides: dict[Path, list[MeasurementOverride]] = {}
        for path in self.marked_measurement_paths():
            annotations = self.measurement_overrides.get(path_key(path))
            if annotations:
                overrides[path] = list(annotations)
        return overrides

    def calibration_overrides_for_generation(self) -> dict[Path, CalibrationOverride]:
        overrides: dict[Path, CalibrationOverride] = {}
        for path in self.checked_paths():
            override = self.calibration_overrides.get(path_key(path))
            if override is not None:
                overrides[path] = override
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
        calibration = self.effective_calibration(path)
        if calibration is None:
            self.file_list.selectRow(row)
            self.calibrate_selected()
            return
        try:
            dialog = MeasurementPreviewDialog(
                path,
                self.measurement_overrides.get(key, []),
                parent=self,
                calibration=calibration,
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
        if self.thread is not None:
            return
        scale_fraction_warnings: list[str] = []
        try:
            input_paths = self.checked_paths()
            if not input_paths:
                raise ValueError("请至少添加并勾选一张 SEM 图片。")
            missing_calibration = self.missing_calibration_paths(input_paths)
            if missing_calibration:
                names = "\n".join(f"- {path.name}" for path in missing_calibration[:8])
                remaining = len(missing_calibration) - 8
                if remaining > 0:
                    names += f"\n- 另有 {remaining} 张"
                raise ValueError(
                    f"以下图片缺少像素标定，无法计算标尺：\n{names}\n\n请在队列中右键图片，选择“手动标定…”。"
                )
            measurement_overrides = self.measurement_overrides_for_generation()
            calibration_overrides = self.calibration_overrides_for_generation()
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
                "calibration_overrides": calibration_overrides,
            }
            validate_generation_options(
                options["auto_scale_preset"],
                options["position"],
                options["font_size"],
                options["margin_inches"],
                options["inset_x_percent"],
                options["inset_y_percent"],
                options["line_width_pt"],
            )
            if fixed_scale is not None:
                fixed_scale_nm = parse_scale_nm(fixed_scale)
                for path in input_paths:
                    calibration = self.effective_calibration(path)
                    if calibration is None:
                        continue
                    with Image.open(path) as image:
                        width_px = image.width
                    try:
                        choose_scale_nm(
                            width_px,
                            calibration.nm_per_pixel,
                            fixed_scale_nm,
                            options["auto_scale_preset"],
                        )
                    except ValueError as exc:
                        raise ValueError(f"{path.name}: {exc}") from exc
                    fraction = fixed_scale_nm / (width_px * calibration.nm_per_pixel)
                    if fraction < 0.02:
                        scale_fraction_warnings.append(f"- {path.name}: 占图宽 {fraction:.1%}，可能过短")
                    elif fraction > 0.80:
                        scale_fraction_warnings.append(f"- {path.name}: 占图宽 {fraction:.1%}，可能过长")
        except Exception as exc:
            QMessageBox.warning(self, "参数错误", str(exc))
            return

        if scale_fraction_warnings:
            shown_warnings = scale_fraction_warnings[:8]
            remaining = len(scale_fraction_warnings) - len(shown_warnings)
            if remaining > 0:
                shown_warnings.append(f"- 另有 {remaining} 张图片")
            answer = QMessageBox.question(
                self,
                "固定标尺比例提示",
                "以下图片的固定标尺比例可能不易阅读：\n" + "\n".join(shown_warnings) + "\n\n仍要继续生成吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.log_reset.emit()
        self.generate_button.setEnabled(False)
        self.busy_changed.emit(True)
        self.thread = QThread(self)
        self.worker = GeneratorWorker(options)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.append_log, Qt.QueuedConnection)
        self.worker.finished.connect(self.on_finished, Qt.QueuedConnection)
        self.worker.failed.connect(self.on_failed, Qt.QueuedConnection)
        self.worker.done.connect(self.thread.quit, Qt.DirectConnection)
        self.worker.done.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._cleanup_worker, Qt.QueuedConnection)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    @Slot(str)
    def append_log(self, message: str) -> None:
        self.log_message.emit(message)

    @Slot(list)
    def on_finished(self, output_paths: list[str]) -> None:
        output_count = len(output_paths)
        if output_count == 1:
            message = f"文件已生成:\n{output_paths[0]}"
        else:
            output_dir = Path(output_paths[0]).parent
            output_format = Path(output_paths[0]).suffix.upper().lstrip(".")
            message = f"已生成 {output_count} 个 {output_format}:\n{output_dir}"
        if not self.closing:
            QMessageBox.information(self, "完成", message)

    @Slot(str)
    def on_failed(self, message: str) -> None:
        self.append_log(f"错误: {message}")
        if not self.closing:
            self.log_requested.emit()
            QMessageBox.critical(self, "生成失败", message)

    @Slot()
    def _cleanup_worker(self) -> None:
        self.thread.wait()
        self.thread = None
        self.worker = None
        self.generate_button.setEnabled(True)
        self.busy_changed.emit(False)
