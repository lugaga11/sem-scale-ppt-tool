from __future__ import annotations

import base64
import math
import os
import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sem_scale_ppt import (
    Calibration,
    CalibrationOverride,
    MeasurementOverride,
    choose_scale_nm,
    convert_image_to_png,
    dynamic_scale_candidates_nm,
    format_scale_label,
    generate_pptx,
    generate_svg,
    image_to_display_grayscale,
    parse_scale_nm,
    path_key,
    validate_generation_options,
)
from sem_scale_ppt_gui import (
    CalibrationDialog,
    MainWindow,
    MeasurementPreviewDialog,
    MeasurementPreviewWidget,
    qimage_from_pil,
)


class ManualCalibrationCoreTests(unittest.TestCase):
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
                    self.assertEqual(converted.mode, "I;16")
                    self.assertEqual(list(converted.getdata()), values)
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
                self.assertEqual(embedded.mode, "I;16")
                self.assertEqual(list(embedded.getdata()), values)

            pptx_path = root / "result.pptx"
            generate_pptx([source], pptx_path, calibration_overrides={source: override})
            with ZipFile(pptx_path) as archive:
                media_names = [name for name in archive.namelist() if name.startswith("ppt/media/")]
                self.assertEqual(len(media_names), 1)
                with Image.open(BytesIO(archive.read(media_names[0]))) as embedded:
                    self.assertEqual(embedded.mode, "I;16")
                    self.assertEqual(list(embedded.getdata()), values)

    def test_dynamic_auto_scale_covers_small_and_large_fields(self) -> None:
        for field_width_nm in (10.0, 1_000_000_000.0):
            scale_nm = choose_scale_nm(1000, field_width_nm / 1000, None, "medium")
            fraction = scale_nm / field_width_nm
            self.assertGreaterEqual(fraction, 0.12)
            self.assertLessEqual(fraction, 0.20)

    def test_invalid_or_oversized_fixed_scale_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            parse_scale_nm("0nm")
        with self.assertRaisesRegex(ValueError, "exceeds the image field width"):
            choose_scale_nm(100, 1.0, parse_scale_nm("1mm"), "medium")

    def test_invalid_render_parameters_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "margin"):
            validate_generation_options("medium", "bottom-right", 36, -0.1, 8, 15, 3)
        with self.assertRaisesRegex(ValueError, "Horizontal inset"):
            validate_generation_options("medium", "bottom-right", 36, 0, 101, 15, 3)
        with self.assertRaisesRegex(ValueError, "Line width"):
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
            self.assertIn("manual calibration", svg_items[0].calibration.source)

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
            with self.assertRaisesRegex(ValueError, "manual calibration"):
                generate_svg([source], root / "result.svg")


class ManualCalibrationGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

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
                self.assertFalse(name_item.icon().isNull())
                measurement_cell = window.measurement_cell(0)
                self.assertIsNotNone(measurement_cell)
                assert measurement_cell is not None
                self.assertEqual(
                    measurement_cell.edit_button.palette().color(QPalette.ColorRole.Button).name(),
                    "#f3dddd",
                )

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
                self.assertIn("手动标定", name_item.toolTip())
                self.assertEqual(
                    measurement_cell.edit_button.palette().color(QPalette.ColorRole.Button).name(),
                    measurement_cell.default_button_palette.color(QPalette.ColorRole.Button).name(),
                )

                window.file_list.selectRow(0)
                window.clear_selected_manual_calibrations()
                self.assertEqual(window.missing_calibration_paths([source]), [source.resolve()])
                self.assertIn("缺标定 1", window.count_label.text())
                self.assertIn("未检测到像素标定", name_item.toolTip())
                self.assertEqual(
                    measurement_cell.edit_button.palette().color(QPalette.ColorRole.Button).name(),
                    "#f3dddd",
                )
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
            with patch("sem_scale_ppt_gui.detect_thickness_measurement", return_value=None) as detect:
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
