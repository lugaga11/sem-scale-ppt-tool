from __future__ import annotations

import base64
import math
import os
import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from typing import cast
from unittest.mock import patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sem_tool.annotation.core import (
    Calibration,
    CalibrationOverride,
    MeasurementOverride,
    choose_scale_nm,
    detect_sem_content_bottom,
    detect_thickness_measurement,
    dynamic_scale_candidates_nm,
    format_scale_label,
    generate_pptx,
    generate_svg,
    parse_scale_nm,
    path_key,
    validate_generation_options,
)
from sem_tool.annotation.preview import (
    CalibrationDialog,
    MeasurementPreviewDialog,
    MeasurementPreviewWidget,
    qimage_from_pil,
)
from sem_tool.annotation.ui import AnnotationPanel as MainWindow
from sem_tool.imaging import convert_image_to_png, image_to_display_grayscale
from sem_tool.theme import apply_theme


def synthetic_cross_section(bits: int = 8) -> Image.Image:
    """A dark background, a film between two bright interfaces at rows 120 and 260, and a data bar from row 440."""
    rows = np.full(480, 30.0)
    rows[120:260] = 120
    rows[260:440] = 70
    for edge in (120, 260):
        rows[edge - 2 : edge + 3] += 90
    rows[440:] = 245
    noise = np.random.default_rng(3).normal(0, 6, (480, 640))
    data = (rows[:, None] + noise).clip(0, 255)
    if bits == 16:
        return Image.fromarray((data * 200 + 1000).astype(np.uint16))
    return Image.fromarray(data.astype(np.uint8))


class ManualCalibrationCoreTests(unittest.TestCase):
    def test_invalid_calibrations_and_measurement_kinds_fail_at_construction(self) -> None:
        for value in (0, -1, math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    Calibration(value, "invalid")
                with self.assertRaises(ValueError):
                    CalibrationOverride(value, 0, 0, 100, 0)
        for endpoint in ((0, 0), (math.inf, 0), (math.nan, 0), (1e-320, 0)):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                CalibrationOverride(1000, 0, 0, *endpoint)
        with self.assertRaisesRegex(ValueError, "测量类型"):
            MeasurementOverride("unknown", 0, 0, 100, 0)

    def test_thickness_detection_handles_small_and_narrow_images(self) -> None:
        rng = np.random.default_rng(8)
        for height in (1, 39, 59, 60, 61, 80, 480):
            for width in (1, 19, 20, 21, 40, 80):
                with self.subTest(size=(width, height)):
                    image = Image.fromarray(rng.integers(0, 256, (height, width), dtype=np.uint8))
                    measurement = detect_thickness_measurement(image)
                    if measurement is not None:
                        self.assertTrue(0 <= measurement.top_y_px < measurement.bottom_y_px < height)
                        self.assertTrue(0 <= measurement.x_center_px < width)

    def test_thickness_detection_finds_the_film_between_two_bright_interfaces(self) -> None:
        for bits in (8, 16):
            measurement = detect_thickness_measurement(synthetic_cross_section(bits))
            assert measurement is not None
            self.assertAlmostEqual(measurement.top_y_px, 120, delta=2)
            self.assertAlmostEqual(measurement.bottom_y_px, 260, delta=2)
            self.assertTrue(0 < measurement.x_center_px < 640)

    def test_thickness_detection_stops_at_the_data_bar_and_rejects_featureless_images(self) -> None:
        self.assertEqual(detect_sem_content_bottom(np.asarray(synthetic_cross_section())), 440)
        self.assertEqual(detect_sem_content_bottom(np.full((480, 640), 90, np.uint8)), 480)
        self.assertIsNone(detect_thickness_measurement(Image.new("L", (640, 480), 90)))
        self.assertIsNone(detect_thickness_measurement(Image.new("I;16", (640, 480), 20000)))

    def test_16_bit_display_mapping_stretches_the_central_range_and_keeps_extremes_in_bounds(self) -> None:
        ramp = Image.fromarray(np.tile(np.linspace(1000, 54000, 400, dtype=np.uint16), (300, 1)))
        display = np.asarray(image_to_display_grayscale(ramp))
        self.assertEqual((int(display.min()), int(display.max())), (0, 255))
        self.assertTrue(np.all(np.diff(display[0].astype(int)) >= 0))
        self.assertAlmostEqual(int(display[0, 200]), 128, delta=2)
        flat = image_to_display_grayscale(Image.new("I;16", (30, 20), 65535))
        self.assertEqual(flat.getextrema(), (255, 255))
        self.assertEqual(image_to_display_grayscale(Image.new("I;16", (3, 2), 0)).getextrema(), (0, 0))

    def test_dynamic_scale_candidates_cover_extreme_calibrations(self) -> None:
        width_px = 1024
        for nm_per_pixel in (1e-9, 1.0, 1e6):
            chosen_nm = choose_scale_nm(width_px, nm_per_pixel, None, "medium")
            fraction = chosen_nm / nm_per_pixel / width_px
            self.assertGreaterEqual(fraction, 0.12)
            self.assertLessEqual(fraction, 0.20)
            exponent = math.floor(math.log10(chosen_nm))
            mantissa = chosen_nm / (10.0**exponent)
            self.assertTrue(any(math.isclose(mantissa, value) for value in (1.0, 2.0, 5.0)))

    def test_dynamic_scale_candidates_and_labels_use_engineering_units(self) -> None:
        candidates = dynamic_scale_candidates_nm(163.84)
        self.assertIn(100.0, candidates)
        self.assertIn(200.0, candidates)
        self.assertEqual(format_scale_label(500), "500 nm")
        self.assertEqual(format_scale_label(2000), "2 μm")
        self.assertEqual(format_scale_label(2_000_000), "2 mm")
        self.assertEqual(format_scale_label(500_000_000_000), "500 m")

    def test_16_bit_grayscale_is_preserved_in_png_and_mapped_for_display(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            values = [1000 + index * 100 for index in range(256)]
            source = Image.new("I;16", (256, 1))
            source.putdata(values)
            endian_sources = {
                "native": source,
                "big": Image.frombytes("I;16B", source.size, source.tobytes("raw", "I;16B")),
                "little": Image.frombytes("I;16L", source.size, source.tobytes()),
            }

            for name, endian_source in endian_sources.items():
                output = Path(temp) / f"converted_{name}.png"
                convert_image_to_png(endian_source, output)
                with Image.open(output) as converted:
                    # Pillow before 10.3 reports a 16-bit grayscale PNG as mode "I"; the samples are what matters.
                    self.assertIn(converted.mode, ("I;16", "I"))
                    self.assertEqual(np.asarray(converted).ravel().tolist(), values)
            display = image_to_display_grayscale(source)
            self.assertEqual(display.mode, "L")
            self.assertEqual(display.getextrema(), (0, 255))

    def test_both_exports_embed_16_bit_png_without_clipping_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "high_depth.tif"
            values = [index * 257 for index in range(256)] * 2
            image = Image.new("I;16", (256, 2))
            image.putdata(values)
            image.save(source)
            override = CalibrationOverride(1000, 0, 0, 100, 0)

            generate_svg([source], root / "result.svg", calibration_overrides={source: override})

            svg_text = (root / "result_01_high_depth.svg").read_text(encoding="utf-8")
            match = re.search(r"data:image/png;base64,([^\"]+)", svg_text)
            self.assertIsNotNone(match)
            assert match is not None
            embedded_path = root / "embedded.png"
            embedded_path.write_bytes(base64.b64decode(match.group(1)))
            with Image.open(embedded_path) as embedded:
                self.assertIn(embedded.mode, ("I;16", "I"))
                self.assertEqual(np.asarray(embedded).ravel().tolist(), values)

            pptx_path = root / "result.pptx"
            generate_pptx([source], pptx_path, calibration_overrides={source: override})
            with ZipFile(pptx_path) as archive:
                media_names = [name for name in archive.namelist() if name.startswith("ppt/media/")]
                self.assertEqual(len(media_names), 1)
                with Image.open(BytesIO(archive.read(media_names[0]))) as embedded:
                    self.assertIn(embedded.mode, ("I;16", "I"))
                    self.assertEqual(np.asarray(embedded).ravel().tolist(), values)

    def test_dynamic_auto_scale_covers_small_and_large_fields(self) -> None:
        for field_width_nm in (10.0, 1_000_000_000.0):
            scale_nm = choose_scale_nm(1000, field_width_nm / 1000, None, "medium")
            fraction = scale_nm / field_width_nm
            self.assertGreaterEqual(fraction, 0.12)
            self.assertLessEqual(fraction, 0.20)

    def test_invalid_or_oversized_fixed_scale_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须大于零"):
            parse_scale_nm("0nm")
        with self.assertRaisesRegex(ValueError, "无法识别长度"):
            parse_scale_nm("1 micron")
        with self.assertRaisesRegex(ValueError, "超出了图像视野宽度"):
            choose_scale_nm(100, 1.0, parse_scale_nm("1mm"), "medium")

    def test_invalid_render_parameters_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "页边距"):
            validate_generation_options("medium", "bottom-right", 36, -0.1, 8, 15, 3)
        with self.assertRaisesRegex(ValueError, "横向内缩"):
            validate_generation_options("medium", "bottom-right", 36, 0, 101, 15, 3)
        with self.assertRaisesRegex(ValueError, "线宽"):
            validate_generation_options("medium", "bottom-right", 36, 0, 8, 15, 0)

    def test_manual_calibration_drives_both_exports_and_svg_embeds_image_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "plain.png"
            Image.new("RGB", (200, 100), (40, 40, 40)).save(source)
            override = CalibrationOverride(
                known_length_nm=1000,
                x1_px=10,
                y1_px=50,
                x2_px=110,
                y2_px=50,
            )

            svg_items = generate_svg(
                [source],
                root / "result.svg",
                calibration_overrides={source: override},
            )
            svg_path = root / "result_01_plain.svg"
            svg_text = svg_path.read_text(encoding="utf-8")
            self.assertEqual(svg_text.count("data:image/png;base64,"), 1)
            self.assertEqual(svg_text.count("xlink:href="), 1)
            self.assertAlmostEqual(svg_items[0].calibration.nm_per_pixel, 10.0)
            self.assertIn("手动标定", svg_items[0].calibration.source)

            pptx_path = root / "result.pptx"
            pptx_items = generate_pptx(
                [source],
                pptx_path,
                calibration_overrides={source: override},
            )
            self.assertTrue(pptx_path.exists())
            self.assertAlmostEqual(pptx_items[0].calibration.nm_per_pixel, 10.0)

    def test_missing_calibration_is_rejected_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "plain.png"
            Image.new("RGB", (100, 50)).save(source)
            with self.assertRaisesRegex(ValueError, "没有像素标定"):
                generate_svg([source], root / "result.svg")


class ManualCalibrationGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = cast(QApplication, QApplication.instance() or QApplication([]))
        # Style the application before any widget exists: replacing the style later re-polishes every
        # widget still alive, and a window collected by Python in the middle of that crashes Qt.
        apply_theme(cls.app)

    def test_queue_preflight_and_manual_override_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "plain.png"
            Image.new("RGB", (200, 100)).save(source)
            window = MainWindow()
            try:
                window.add_paths([source])
                self.assertEqual(window.file_list.columnCount(), 3)
                self.assertEqual(window.missing_calibration_paths([source]), [source.resolve()])
                self.assertIn("缺标定 1", window.count_label.text())
                name_item = window.file_list.item(0, 1)
                self.assertIsNotNone(name_item)
                assert name_item is not None
                measurement_cell = window.measurement_cell(0)
                self.assertIsNotNone(measurement_cell)
                assert measurement_cell is not None
                self.assertEqual(measurement_cell.edit_button.text(), "手动标定")
                self.assertEqual(measurement_cell.edit_button.property("status"), "missing")

                window.calibration_overrides[path_key(source)] = CalibrationOverride(
                    known_length_nm=1000,
                    x1_px=10,
                    y1_px=50,
                    x2_px=110,
                    y2_px=50,
                )
                window.update_file_calibration_status(0)
                window.update_count()

                self.assertEqual(window.missing_calibration_paths([source]), [])
                self.assertNotIn("缺标定", window.count_label.text())
                self.assertEqual(measurement_cell.edit_button.text(), "编辑")
                self.assertEqual(measurement_cell.edit_button.property("status"), "ready")
                self.assertEqual(name_item.toolTip(), "")
                self.assertIn("手动标定", name_item.data(Qt.ItemDataRole.WhatsThisRole))

                window.file_list.selectRow(0)
                window.clear_selected_manual_calibrations()
                self.assertEqual(measurement_cell.edit_button.text(), "手动标定")
                self.assertEqual(measurement_cell.edit_button.property("status"), "missing")
                self.assertEqual(window.missing_calibration_paths([source]), [source.resolve()])
                self.assertIn("缺标定 1", window.count_label.text())
                self.assertEqual(name_item.toolTip(), "")
                self.assertIn("未检测到像素标定", name_item.data(Qt.ItemDataRole.WhatsThisRole))
            finally:
                window.close()

    def test_color_swatch_shows_the_exported_color_and_invalid_colors_stop_before_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "plain.tif"
            Image.new("L", (200, 100), 60).save(source, tiffinfo={270: "PixelSize=2.5"})
            window = MainWindow()
            try:
                window.add_paths([source])
                window.output_edit.setText(str(Path(temp) / "out.svg"))
                for text, exported in (("green", "#00AA00"), ("blue", "#0066CC"), ("ffffff", "#FFFFFF"), ("#12ab34", "#12AB34")):
                    window.color_edit.setText(text)
                    self.assertEqual(window.bar_color(), exported)
                    self.assertEqual(window.color_button.color, exported)
                    self.assertEqual(window.color_button.text(), "")
                for text in ("orange", "#fff", "#FF000080", ""):
                    window.color_edit.setText(text)
                    self.assertIsNone(window.bar_color())
                    self.assertIsNone(window.color_button.color)
                    self.assertEqual(window.color_button.text(), "?")
                with patch("sem_tool.annotation.ui.QMessageBox.warning") as warning:
                    window.generate()
                self.assertIn("颜色", warning.call_args.args[2])
                self.assertIsNone(window.thread)
            finally:
                window.close()

    def test_calibration_dialog_reuses_saved_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "plain.png"
            Image.new("RGB", (200, 100)).save(source)
            existing = CalibrationOverride(
                known_length_nm=500,
                x1_px=10,
                y1_px=50,
                x2_px=110,
                y2_px=50,
            )
            dialog = CalibrationDialog(source, existing, None)
            try:
                result = dialog.calculated_override()
                self.assertAlmostEqual(result.pixel_length, 100.0)
                self.assertAlmostEqual(result.nm_per_pixel, 5.0)
                self.assertTrue(dialog.apply_button.isEnabled())
            finally:
                dialog.close()

    def test_thickness_detection_is_lazy_and_runs_only_on_first_click(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "plain.png"
            Image.new("RGB", (200, 100), (60, 60, 60)).save(source)
            with patch("sem_tool.annotation.preview.detect_thickness_measurement", return_value=None) as detect:
                dialog = MeasurementPreviewDialog(
                    source,
                    [],
                    calibration=Calibration(1.0, "test calibration"),
                )
                try:
                    detect.assert_not_called()
                    self.assertFalse(dialog.auto_detection_attempted)

                    dialog.on_thickness_tool_clicked()
                    detect.assert_called_once()
                    self.assertTrue(dialog.auto_detection_attempted)
                    self.assertGreaterEqual(dialog.preview.thickness_index(), 0)

                    dialog.on_thickness_tool_clicked()
                    detect.assert_called_once()
                finally:
                    dialog.close()

    def test_zoomed_preview_pans_with_middle_mouse_drag(self) -> None:
        image = QImage(400, 300, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.black)
        preview = MeasurementPreviewWidget(image, 1.0, [], None)
        try:
            preview.resize(640, 420)
            preview.show()
            self.app.processEvents()
            preview.zoom_factor = 2.0
            preview.clamp_pan()

            start = QPoint(320, 210)
            end = QPoint(390, 250)
            QTest.mousePress(preview, Qt.MouseButton.MiddleButton, pos=start)
            QTest.mouseMove(preview, end)
            QTest.mouseRelease(preview, Qt.MouseButton.MiddleButton, pos=end)

            self.assertAlmostEqual(preview.pan_x, 70.0)
            self.assertAlmostEqual(preview.pan_y, 40.0)
            self.assertFalse(preview.panning)
            self.assertIsNone(preview.pan_last_pos)
        finally:
            preview.close()

    def test_annotation_body_drag_moves_whole_segment_and_supports_history(self) -> None:
        image = QImage(400, 300, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.black)
        original = MeasurementOverride("distance", 50, 50, 150, 50)
        preview = MeasurementPreviewWidget(image, 1.0, [original], None)
        try:
            preview.resize(640, 420)
            preview.show()
            self.app.processEvents()

            QTest.mousePress(preview, Qt.MouseButton.LeftButton, pos=QPoint(180, 70))
            QTest.mouseMove(preview, QPoint(250, 112))
            QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, pos=QPoint(250, 112))

            moved = preview.annotations[0]
            self.assertAlmostEqual(moved.x1_px, 100.0)
            self.assertAlmostEqual(moved.y1_px, 80.0)
            self.assertAlmostEqual(moved.x2_px, 200.0)
            self.assertAlmostEqual(moved.y2_px, 80.0)

            preview.undo()
            self.assertEqual(preview.annotations, [original])
            preview.redo()
            self.assertEqual(preview.annotations, [moved])
        finally:
            preview.close()

    def test_measurement_keyboard_shortcuts_delete_undo_redo_and_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "plain.png"
            Image.new("RGB", (200, 100), (60, 60, 60)).save(source)
            annotation = MeasurementOverride("distance", 20, 20, 100, 20)
            dialog = MeasurementPreviewDialog(
                source,
                [annotation],
                calibration=Calibration(1.0, "test calibration"),
            )
            try:
                dialog.show()
                self.app.processEvents()
                dialog.preview.select_annotation(0)
                dialog.preview.setFocus()

                QTest.keyClick(dialog.preview, Qt.Key.Key_Delete)
                self.assertEqual(dialog.preview.annotations, [])
                QTest.keyClick(dialog.preview, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(dialog.preview.annotations, [annotation])
                QTest.keyClick(
                    dialog.preview,
                    Qt.Key.Key_Z,
                    Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
                )
                self.assertEqual(dialog.preview.annotations, [])
                QTest.keyClick(dialog.preview, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
                QTest.keyClick(dialog.preview, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(dialog.preview.annotations, [])

                dialog.preview.creating_start = (20.0, 20.0)
                dialog.preview.creating_end = (80.0, 40.0)
                QTest.keyClick(dialog.preview, Qt.Key.Key_Escape)
                self.assertIsNone(dialog.preview.creating_start)
                self.assertIsNone(dialog.preview.creating_end)
                self.assertTrue(dialog.isVisible())
            finally:
                dialog.close()

    def test_16_bit_preview_keeps_gray_level_contrast(self) -> None:
        image = Image.new("I;16", (256, 1))
        image.putdata([1000 + index * 100 for index in range(256)])

        preview = qimage_from_pil(image)

        self.assertLess(preview.pixelColor(0, 0).red(), preview.pixelColor(255, 0).red())


if __name__ == "__main__":
    unittest.main()
