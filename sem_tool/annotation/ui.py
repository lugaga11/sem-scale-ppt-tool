from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSignalBlocker, QSize, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..files import IMAGE_FILE_FILTER, image_paths_from_inputs, natural_key, path_key
from ..theme import COLORS, ICON_SIZE, SPACE, icon
from ..widgets import (
    ColorSwatchButton,
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
    PathLineEdit,
    SegmentedControl,
    ToggleSwitch,
    card,
    make_icon_button,
    open_file_or_warn,
    section_heading,
    set_semantic_state,
)
from .core import (
    DEFAULT_MARGIN_INCHES,
    DEFAULTS,
    Calibration,
    CalibrationOverride,
    MeasurementOverride,
    choose_scale_nm,
    hex_color,
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
# The 16:9 page is 7.5 inch high, so a margin has to stay clear of half of that on each side.
MAX_MARGIN_INCHES = 3.5


def label_for_value(options: dict[str, str], value: str) -> str:
    for label, option_value in options.items():
        if option_value == value:
            return label
    return next(iter(options))


class AnnotationPanel(QWidget):
    queue_changed = Signal(list)
    busy_changed = Signal(bool)
    log_message = Signal(str)
    log_reset = Signal()
    log_requested = Signal()
    export_finished = Signal(list)

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
        self.setObjectName("annotationPanel")
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(SPACE.lg, SPACE.md, SPACE.lg, SPACE.md)
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter.setHandleWidth(SPACE.md)
        self.content_splitter.setChildrenCollapsible(False)
        root_layout.addWidget(self.content_splitter)
        self.content_splitter.addWidget(self._build_queue_card())
        self.content_splitter.addWidget(self._build_inspector())
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 0)
        self.content_splitter.setSizes([794, 342])

    def _build_queue_card(self) -> QFrame:
        queue_box, queue_layout = card()
        title_row = section_heading("文件队列", "image")
        self.queue_total = QLabel("0 个文件")
        self.queue_total.setProperty("role", "badge")
        title_row.insertWidget(2, self.queue_total)
        self.select_all_button = self._button("全选", self.select_all_generate)
        self.select_all_button.setIcon(icon("check"))
        self.select_all_button.setToolTip("勾选所有文件用于生成")
        title_row.addWidget(self.select_all_button)
        self.unselect_all_button = self._button("取消全选", self.unselect_all_generate)
        self.unselect_all_button.setIcon(icon("square"))
        self.unselect_all_button.setToolTip("取消所有文件的生成勾选")
        title_row.addWidget(self.unselect_all_button)
        sort_button = self._button("", self.sort_queue)
        sort_button.setProperty("variant", "toolbar")
        make_icon_button(sort_button, "sort", "按文件名自然排序")
        title_row.addWidget(sort_button)
        self.clear_queue_button = self._button("", self.clear_queue)
        self.clear_queue_button.setProperty("variant", "toolbar")
        make_icon_button(self.clear_queue_button, "trash", "清空队列")
        self.clear_queue_button.setEnabled(False)
        title_row.addWidget(self.clear_queue_button)
        more_button = QToolButton()
        more_button.setObjectName("queueMoreButton")
        more_button.setProperty("variant", "toolbar")
        make_icon_button(more_button, "more", "添加文件或文件夹")
        more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.queue_menu = QMenu(more_button)
        self.queue_menu.addAction(icon("plus"), "添加文件…", self.choose_input_files)
        self.queue_menu.addAction(icon("folder"), "添加文件夹…", self.choose_input_folder)
        more_button.setMenu(self.queue_menu)
        title_row.addWidget(more_button)
        queue_layout.addLayout(title_row)

        self.file_list = FileQueueWidget()
        self.file_list.paths_dropped.connect(self.add_paths)
        self.file_list.row_reorder_requested.connect(self.reorder_queue_row)
        self.file_list.itemChanged.connect(self.on_queue_item_changed)
        self.file_list.itemDoubleClicked.connect(self.open_original_file)
        self.file_list.customContextMenuRequested.connect(self.show_queue_context_menu)
        queue_layout.addWidget(self.file_list, 1)
        self.count_label = QLabel("拖入图片或文件夹，开始标定与测量")
        self.count_label.setProperty("role", "muted")
        self.count_label.setWordWrap(True)
        queue_layout.addWidget(self.count_label)
        return queue_box

    def _build_inspector(self) -> QFrame:
        """The right-hand column: scrolling settings above the always visible generate button."""
        inspector = QFrame()
        inspector.setProperty("panel", True)
        inspector.setMinimumWidth(300)
        inspector.setMaximumWidth(390)
        layout = QVBoxLayout(inspector)
        layout.setContentsMargins(SPACE.xs, SPACE.sm, SPACE.xs, SPACE.xs)
        layout.setSpacing(0)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        settings = QWidget()
        settings.setObjectName("inspectorContents")
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(SPACE.md, SPACE.sm, SPACE.md, SPACE.sm)
        settings_layout.setSpacing(SPACE.md)
        settings_layout.addLayout(section_heading("标尺设置", "ruler"))
        settings_layout.addLayout(self._build_scale_form())
        settings_layout.addLayout(section_heading("导出设置", "document"))
        self._build_export_settings(settings_layout)
        settings_layout.addStretch()
        self.settings_scroll.setWidget(settings)
        layout.addWidget(self.settings_scroll, 1)

        actions = QVBoxLayout()
        actions.setContentsMargins(SPACE.md, SPACE.sm, SPACE.md, SPACE.md)
        actions.setSpacing(SPACE.sm)
        self.export_count = QLabel("已勾选 0 张")
        self.export_count.setProperty("role", "muted")
        actions.addWidget(self.export_count)
        self.generate_button = QPushButton("生成 PPTX")
        self.generate_button.setObjectName("generateButton")
        self.generate_button.setProperty("variant", "primary")
        self.generate_button.setIcon(icon("download", COLORS.surface))
        self.generate_button.setIconSize(QSize(18, 18))
        self.generate_button.clicked.connect(self.generate)
        actions.addWidget(self.generate_button)
        layout.addLayout(actions)
        return inspector

    def _build_scale_form(self) -> QFormLayout:
        """One form for the everyday and the advanced rows, so every field shares a left edge."""
        self.scale_form = QFormLayout()
        self.scale_form.setVerticalSpacing(SPACE.md)
        self.scale_form.setHorizontalSpacing(SPACE.md)
        self.scale_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.auto_scale_check = ToggleSwitch("自动选择标尺长度")
        self.auto_scale_check.setChecked(True)
        self.auto_scale_check.toggled.connect(self.on_auto_scale_toggled)
        self.auto_scale_choice = SegmentedControl(list(AUTO_SCALE_PRESETS))
        self.auto_scale_choice.setCurrentText(label_for_value(AUTO_SCALE_PRESETS, DEFAULTS["auto_scale_preset"]))
        self.fixed_scale_edit = QLineEdit(DEFAULTS["fixed_scale"])
        self.fixed_scale_edit.setPlaceholderText("例如 1um 或 500nm")
        self.fixed_scale_edit.textChanged.connect(self._update_fixed_scale_state)
        self.position_combo = NoWheelComboBox()
        self.position_combo.addItems(list(POSITIONS.keys()))
        self.position_combo.setCurrentText(label_for_value(POSITIONS, DEFAULTS["scale_position"]))
        self.line_width_spin = NoWheelDoubleSpinBox()
        self.line_width_spin.setRange(0.25, 20.0)
        self.line_width_spin.setDecimals(2)
        self.line_width_spin.setSuffix(" pt")
        self.line_width_spin.setSingleStep(0.25)
        self.line_width_spin.setValue(DEFAULTS["line_width_pt"])
        self.color_edit = QLineEdit(f"#{hex_color(DEFAULTS['bar_color'])}")
        self.color_button = ColorSwatchButton("选择标尺颜色")
        self.color_button.clicked.connect(self.choose_color)
        self.color_edit.textChanged.connect(self.update_color_swatch)
        self.update_color_swatch()
        color_row = QHBoxLayout()
        color_row.setSpacing(SPACE.sm)
        color_row.addWidget(self.color_button)
        color_row.addWidget(self.color_edit)
        automatic_row = QHBoxLayout()
        automatic_row.addStretch()
        automatic_row.addWidget(self.auto_scale_check)

        self.scale_form.addRow("自动长度", automatic_row)
        self.scale_form.addRow("标尺长度", self.auto_scale_choice)
        self.scale_form.addRow("固定长度", self.fixed_scale_edit)
        self.scale_form.addRow("位置", self.position_combo)
        self.scale_form.addRow("线宽", self.line_width_spin)
        self.scale_form.addRow("颜色", color_row)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setObjectName("advancedToggle")
        self.advanced_toggle.setText("更多设置")
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setIconSize(QSize(12, 12))
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.advanced_toggle.toggled.connect(self.toggle_advanced_options)
        self.scale_form.addRow(self.advanced_toggle)

        self.inset_x_spin = NoWheelSpinBox()
        self.inset_x_spin.setRange(0, 40)
        self.inset_x_spin.setValue(DEFAULTS["inset_x_percent"])
        self.inset_x_spin.setSuffix(" %")
        self.inset_y_spin = NoWheelSpinBox()
        self.inset_y_spin.setRange(0, 40)
        self.inset_y_spin.setValue(DEFAULTS["inset_y_percent"])
        self.inset_y_spin.setSuffix(" %")
        self.font_spin = NoWheelSpinBox()
        self.font_spin.setRange(0, 72)
        self.font_spin.setValue(DEFAULTS["font_size_pt"])
        self.font_spin.setSpecialValueText("自动")
        self.font_spin.setSuffix(" pt")
        self.font_bold_check = QCheckBox("加粗")
        self.font_bold_check.setChecked(bool(DEFAULTS["font_bold"]))
        self.margin_spin = NoWheelDoubleSpinBox()
        self.margin_spin.setRange(0.0, MAX_MARGIN_INCHES)
        self.margin_spin.setDecimals(2)
        self.margin_spin.setSingleStep(0.05)
        self.margin_spin.setSuffix(" inch")
        self.margin_spin.setValue(DEFAULT_MARGIN_INCHES)
        self.advanced_fields = (
            self.inset_x_spin, self.inset_y_spin, self.font_spin, self.font_bold_check, self.margin_spin,
        )
        for label, field in zip(("横向内缩", "纵向内缩", "字体大小", "字体样式", "页边距"), self.advanced_fields):
            self.scale_form.addRow(label, field)
        self.on_auto_scale_toggled(True)
        self.toggle_advanced_options(False)
        return self.scale_form

    def _build_export_settings(self, layout: QVBoxLayout) -> None:
        self.output_format_control = SegmentedControl(list(OUTPUT_FORMATS))
        self.output_format_control.group.buttonToggled.connect(self.on_output_format_changed)
        layout.addWidget(self.output_format_control)
        self.output_hint = QLabel("每张图片一页 · 标注可编辑")
        self.output_hint.setProperty("role", "muted")
        self.output_hint.setWordWrap(True)
        layout.addWidget(self.output_hint)
        save_label = QLabel("保存位置")
        save_label.setProperty("role", "muted")
        layout.addWidget(save_label)
        self.output_edit = PathLineEdit(str(Path.cwd() / DEFAULTS["output_filename"]))
        self.output_edit.setMinimumWidth(0)
        self.output_edit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        save_button = self._button("", self.choose_output)
        make_icon_button(save_button, "folder", "另存为…")
        output_path_row = QHBoxLayout()
        output_path_row.setSpacing(SPACE.sm)
        output_path_row.addWidget(self.output_edit, 1)
        output_path_row.addWidget(save_button)
        layout.addLayout(output_path_row)

    @Slot(bool)
    def toggle_advanced_options(self, expanded: bool) -> None:
        for field in self.advanced_fields:
            self.scale_form.setRowVisible(field, expanded)
        self.advanced_toggle.setIcon(icon("chevron-up" if expanded else "chevron-down", COLORS.muted))

    @Slot(str)
    def _update_fixed_scale_state(self, text: str) -> None:
        try:
            parse_scale_nm(text)
        except ValueError:
            set_semantic_state(self.fixed_scale_edit, "error")
        else:
            set_semantic_state(self.fixed_scale_edit, "")

    def bar_color(self) -> str | None:
        """The colour both exporters will use, as #RRGGBB; None while the text is not a colour they accept."""
        try:
            return f"#{hex_color(self.color_edit.text())}"
        except ValueError:
            return None

    @Slot()
    def update_color_swatch(self) -> None:
        color = self.bar_color()
        self.color_button.set_color(color)
        set_semantic_state(self.color_edit, "error" if color is None else "")

    def _button(self, text: str, callback) -> QPushButton:
        button = QPushButton(text)
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        button.clicked.connect(callback)
        return button

    def current_output_format(self) -> str:
        return OUTPUT_FORMATS[self.output_format_control.currentText()]

    def on_output_format_changed(self, _button: QPushButton, checked: bool) -> None:
        if not checked:
            return
        output_format = self.current_output_format()
        suffix = f".{output_format}"
        output_text = self.output_edit.text().strip()
        if output_text:
            self.output_edit.setText(str(Path(output_text).with_suffix(suffix)))
        self.generate_button.setText(f"生成 {output_format.upper()}")
        self.output_hint.setText(
            "每张图片一页 · 标注可编辑" if output_format == "pptx"
            else "每张图片一个文件 · 矢量标注"
        )

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
            suffix = f".{output_format}"
            if Path(path).suffix.lower() != suffix:
                path += suffix
            self.output_edit.setText(path)

    def choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.bar_color() or COLORS.surface), self, "选择标尺颜色")
        if color.isValid():
            self.color_edit.setText(color.name().upper())

    def open_original_file(self, item: QTableWidgetItem) -> None:
        if item.column() == COL_NAME:
            open_file_or_warn(self, self.item_path(item.row()))

    def add_paths(self, paths: list[Path]) -> None:
        existing = {path_key(self.item_path(row)) for row in range(self.file_list.rowCount())}
        added = False
        for path in image_paths_from_inputs(paths):
            key = path_key(path)
            if key in existing:
                continue
            existing.add(key)
            self.add_queue_row(path, generate=True)
            added = True
        if added:
            self.refresh_item_labels()
            self.update_count()
            self._notify_queue_changed()
            if not self.selected_rows():
                self.file_list.selectRow(0)

    def select_path(self, path: Path) -> int:
        key = path_key(path)
        for row in range(self.file_list.rowCount()):
            if path_key(self.item_path(row)) == key:
                self.file_list.selectRow(row)
                self.file_list.scrollTo(self.file_list.model().index(row, COL_NAME))
                return row
        return -1

    def inspect_metadata_calibration(self, path: Path) -> None:
        key = path_key(path)
        if key in self.metadata_calibrations:
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
            return override.to_calibration()
        self.inspect_metadata_calibration(path)
        return self.metadata_calibrations.get(key)

    def missing_calibration_paths(self, paths: list[Path]) -> list[Path]:
        return [path for path in paths if self.effective_calibration(path) is None]

    def update_file_calibration_status(self, row: int) -> None:
        item = self.file_list.item(row, COL_NAME)
        assert item is not None
        path = self.item_path(row)
        key = path_key(path)
        override = self.calibration_overrides.get(key)
        calibration = self.effective_calibration(path)
        measurement_cell = self.measurement_cell(row)
        measurement_cell.set_calibration_available(calibration is not None)
        if override is not None:
            status = (
                f"手动标定：{override.nm_per_pixel:.9g} nm/px\n"
                f"已知长度：{scale_input_from_nm(override.known_length_nm)}\n"
                f"标定线：{override.pixel_length:.3f} px"
            )
        elif calibration is not None:
            status = f"元数据标定：{calibration.nm_per_pixel:g} nm/px\n来源：{calibration.source}"
        else:
            error = self.calibration_errors.get(key)
            detail = f"\n读取错误：{error}" if error else ""
            status = f"未检测到像素标定。请右键此图片选择“手动标定…”{detail}"
        item.setData(Qt.ItemDataRole.WhatsThisRole, status)

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
        return item

    def add_queue_row(self, path: Path, generate: bool) -> None:
        self._insert_queue_row(self.file_list.rowCount(), self.checkbox_item(generate), self.text_item(path.name, path))

    def _insert_queue_row(self, row: int, checkbox: QTableWidgetItem, name: QTableWidgetItem) -> None:
        with QSignalBlocker(self.file_list):
            self.file_list.insertRow(row)
            self.file_list.setItem(row, COL_GENERATE, checkbox)
            self.file_list.setItem(row, COL_NAME, name)
            measurement_cell = MeasurementCellWidget()
            measurement_cell.edit_requested.connect(self.on_measurement_edit_requested)
            self.file_list.setCellWidget(row, COL_MEASUREMENT, measurement_cell)
        self.update_measurement_status(row)
        self.update_file_calibration_status(row)

    def item_path(self, row: int) -> Path:
        item = self.file_list.item(row, COL_NAME)
        if item is None:
            raise ValueError(f"队列第 {row + 1} 行缺少文件项。")
        return Path(item.data(Qt.ItemDataRole.UserRole))

    def measurement_cell(self, row: int) -> MeasurementCellWidget:
        widget = self.file_list.cellWidget(row, COL_MEASUREMENT)
        assert isinstance(widget, MeasurementCellWidget)
        return widget

    def row_for_measurement_cell(self, cell: MeasurementCellWidget) -> int:
        for row in range(self.file_list.rowCount()):
            if self.file_list.cellWidget(row, COL_MEASUREMENT) is cell:
                return row
        return -1

    def row_checked(self, row: int) -> bool:
        item = self.file_list.item(row, COL_GENERATE)
        assert item is not None
        return item.checkState() == Qt.CheckState.Checked

    def set_row_checked(self, row: int, checked: bool) -> None:
        item = self.file_list.item(row, COL_GENERATE)
        assert item is not None
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def update_measurement_status(self, row: int) -> None:
        cell = self.measurement_cell(row)
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

    def checked_paths(self) -> list[Path]:
        paths: list[Path] = []
        for row in range(self.file_list.rowCount()):
            if self.row_checked(row):
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
            assert name_item is not None
            display_name = path.name
            if name_counts[path.name.lower()] > 1:
                display_name = f"{path.name} [{path.parent.name or path.parent}]"
            name_item.setText(display_name)

    def update_count(self, *_args) -> None:
        total = self.file_list.rowCount()
        self.clear_queue_button.setEnabled(total > 0)
        checked = self.checked_paths()
        marked = [path for path in checked if self.measurement_overrides.get(path_key(path))]
        selected = len(checked)
        measurement = len(marked)
        marked_annotations = [
            annotation
            for path in marked
            for annotation in self.measurement_overrides.get(path_key(path), [])
        ]
        thickness = sum(1 for annotation in marked_annotations if annotation.kind == "thickness")
        distance = sum(1 for annotation in marked_annotations if annotation.kind == "distance")
        missing_calibration = len(self.missing_calibration_paths(checked))
        missing_text = f" · 缺标定 {missing_calibration}" if missing_calibration else ""
        self.queue_total.setText(f"{total} 个文件")
        self.count_label.setText(
            f"已标注 {measurement} 张 · 膜厚 {thickness} · 测距 {distance}{missing_text}"
            if total else "拖入图片或文件夹，开始标定与测量"
        )
        self.export_count.setText(f"已勾选 {selected} 张")
        self.file_list.resizeColumnToContents(COL_MEASUREMENT)

    def _notify_queue_changed(self) -> None:
        self.queue_changed.emit([self.item_path(row) for row in range(self.file_list.rowCount())])

    def on_queue_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == COL_GENERATE:
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
        self.remove_paths([self.item_path(row) for row in self.selected_rows()])

    @Slot(list)
    def remove_paths(self, paths: list[Path]) -> None:
        keys = {path_key(path) for path in paths}
        rows = [
            row for row in range(self.file_list.rowCount() - 1, -1, -1)
            if path_key(self.item_path(row)) in keys
        ]
        for row in rows:
            path = self.item_path(row)
            key = path_key(path)
            self.file_list.name_delegate.thumbnails.pop(str(path), None)
            self.file_list.name_delegate.pending.pop(str(path), None)
            self.measurement_overrides.pop(key, None)
            self.calibration_overrides.pop(key, None)
            self.metadata_calibrations.pop(key, None)
            self.calibration_errors.pop(key, None)
            self.file_list.removeRow(row)
        self.refresh_item_labels()
        self.update_count()
        self._notify_queue_changed()

    def sort_queue(self) -> None:
        selected = self.selected_rows()
        selected_path = self.item_path(selected[0]) if selected else None
        rows = [(self.item_path(row), self.row_checked(row)) for row in range(self.file_list.rowCount())]
        rows.sort(key=lambda row: natural_key(row[0]))
        self.file_list.setRowCount(0)
        for path, generate in rows:
            self.add_queue_row(path, generate=generate)
        self.refresh_item_labels()
        if selected_path is not None:
            self.select_path(selected_path)
        self.update_count()

    def reorder_queue_row(self, source_row: int, drop_row: int) -> None:
        table = self.file_list
        if not (0 <= source_row < table.rowCount() and 0 <= drop_row <= table.rowCount()):
            return
        insert_at = drop_row - int(source_row < drop_row)
        if insert_at == source_row:
            return
        horizontal, vertical = table.horizontalScrollBar(), table.verticalScrollBar()
        x, y = horizontal.value(), vertical.value()
        root = table.rootIndex()
        if not table.model().moveRow(root, source_row, root, drop_row):
            # Older QTableWidget models do not implement moveRow. Transfer only this row.
            with QSignalBlocker(table):
                checkbox = table.takeItem(source_row, COL_GENERATE)
                name = table.takeItem(source_row, COL_NAME)
                assert checkbox is not None and name is not None
                table.removeRow(source_row)
                self._insert_queue_row(insert_at, checkbox, name)
        if not table.selectionModel().isRowSelected(insert_at, root):
            table.selectRow(insert_at)
        horizontal.setValue(x)
        vertical.setValue(y)

    def clear_queue(self) -> None:
        self.file_list.setRowCount(0)
        self.file_list.name_delegate.timer.stop()
        self.file_list.name_delegate.pending.clear()
        self.file_list.name_delegate.thumbnails.clear()
        self.measurement_overrides.clear()
        self.calibration_overrides.clear()
        self.metadata_calibrations.clear()
        self.calibration_errors.clear()
        self.update_count()
        self.queue_changed.emit([])

    def select_all_generate(self) -> None:
        self._set_all_generate(True)

    def unselect_all_generate(self) -> None:
        self._set_all_generate(False)

    def _set_all_generate(self, checked: bool) -> None:
        with QSignalBlocker(self.file_list):
            for row in range(self.file_list.rowCount()):
                self.set_row_checked(row, checked)
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
        except (OSError, ValueError) as exc:
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
        for path in self.checked_paths():
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
        except (OSError, ValueError) as exc:
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
        self.scale_form.setRowVisible(self.auto_scale_choice, checked)
        self.scale_form.setRowVisible(self.fixed_scale_edit, not checked)

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

            bar_color = self.bar_color()
            if bar_color is None:
                raise ValueError("标尺颜色无效，请输入 white 这样的名称或 #FFFFFF 这样的十六进制值。")
            fixed_scale = None if self.auto_scale_check.isChecked() else self.fixed_scale_edit.text().strip()
            if fixed_scale == "":
                raise ValueError("请输入固定长度，例如 1um 或 500nm。")

            options: GenerateOptions = {
                "input_path": input_paths,
                "output_path": output_path,
                "output_format": output_format,
                "fixed_scale": fixed_scale,
                "auto_scale_preset": AUTO_SCALE_PRESETS[self.auto_scale_choice.currentText()],
                "position": POSITIONS[self.position_combo.currentText()],
                "bar_color": bar_color,
                "font_size": self.font_spin.value() or None,
                "font_bold": self.font_bold_check.isChecked(),
                "margin_inches": float(self.margin_spin.value()),
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
                        raise ValueError(f"{path.name}：{exc}") from exc
                    fraction = fixed_scale_nm / (width_px * calibration.nm_per_pixel)
                    if fraction < 0.02:
                        scale_fraction_warnings.append(f"- {path.name}：占图宽 {fraction:.1%}，可能过短")
                    elif fraction > 0.80:
                        scale_fraction_warnings.append(f"- {path.name}：占图宽 {fraction:.1%}，可能过长")
        except (OSError, ValueError) as exc:
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
        self.worker.log.connect(self.append_log, Qt.ConnectionType.QueuedConnection)
        self.worker.finished.connect(self.on_finished, Qt.ConnectionType.QueuedConnection)
        self.worker.failed.connect(self.on_failed, Qt.ConnectionType.QueuedConnection)
        self.worker.done.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.worker.done.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._cleanup_worker, Qt.ConnectionType.QueuedConnection)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    @Slot(str)
    def append_log(self, message: str) -> None:
        self.log_message.emit(message)

    @Slot(list)
    def on_finished(self, output_paths: list[str]) -> None:
        self.export_finished.emit(output_paths)
        output_count = len(output_paths)
        if output_count == 1:
            message = f"文件已生成：\n{output_paths[0]}"
        else:
            output_dir = Path(output_paths[0]).parent
            output_format = Path(output_paths[0]).suffix.upper().lstrip(".")
            message = f"已生成 {output_count} 个 {output_format}：\n{output_dir}"
        if not self.closing:
            QMessageBox.information(self, "完成", message)

    @Slot(str)
    def on_failed(self, message: str) -> None:
        self.append_log(f"错误：{message}")
        if not self.closing:
            self.log_requested.emit()
            QMessageBox.critical(self, "生成失败", message)

    @Slot()
    def _cleanup_worker(self) -> None:
        assert self.thread is not None
        self.thread.wait()
        self.thread = None
        self.worker = None
        self.generate_button.setEnabled(True)
        self.busy_changed.emit(False)
