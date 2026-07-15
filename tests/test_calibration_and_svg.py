from __future__ import annotations

import os
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from sem_scale_ppt import (
    Calibration,
    CalibrationOverride,
    choose_scale_nm,
    dynamic_scale_candidates_nm,
    format_scale_label,
    generate_pptx,
    generate_svg,
    path_key,
)
from sem_scale_ppt_gui import CalibrationDialog, MainWindow, MeasurementPreviewDialog


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
                self.assertFalse(window.file_list.item(0, 1).icon().isNull())
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
                self.assertIn("手动标定", window.file_list.item(0, 1).toolTip())
                self.assertEqual(
                    measurement_cell.edit_button.palette().color(QPalette.ColorRole.Button).name(),
                    measurement_cell.default_button_palette.color(QPalette.ColorRole.Button).name(),
                )

                window.file_list.selectRow(0)
                window.clear_selected_manual_calibrations()
                self.assertEqual(window.missing_calibration_paths([source]), [source.resolve()])
                self.assertIn("缺标定 1", window.count_label.text())
                self.assertIn("未检测到像素标定", window.file_list.item(0, 1).toolTip())
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


if __name__ == "__main__":
    unittest.main()
