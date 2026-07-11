from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from sem_scale_ppt import (
    CalibrationOverride,
    generate_pptx,
    generate_svg,
    path_key,
)
from sem_scale_ppt_gui import CalibrationDialog, MainWindow


class ManualCalibrationCoreTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
