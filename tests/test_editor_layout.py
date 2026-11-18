from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QEvent, QEventLoop, QPoint, QRect, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from sem_tool.annotation.core import Calibration, MeasurementOverride
from sem_tool.annotation.preview import CalibrationDialog, MeasurementPreviewDialog
from sem_tool.theme import COLORS, apply_theme


class CompactEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = cast(QApplication, QApplication.instance() or QApplication([]))
        cls.app.setQuitOnLastWindowClosed(False)
        apply_theme(cls.app)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "标定与标注.png"
        Image.new("RGB", (320, 240), "#707070").save(self.source)
        self.dialogs = []

    def tearDown(self) -> None:
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def show_compact(self, dialog) -> None:
        self.dialogs.append(dialog)
        self.assertEqual(dialog.size().height(), 760)
        dialog.resize(760, 560)
        dialog.show()
        self.app.processEvents()
        loop = QEventLoop()
        QTimer.singleShot(0, loop.quit)
        loop.exec()
        self.assertEqual(dialog.size().width(), 760)
        self.assertEqual(dialog.size().height(), 560)
        self.assertGreaterEqual(dialog.preview.height() / dialog.height(), 0.74)
        for widget in (dialog.toolbar, dialog.footer, dialog.apply_button,
                       dialog.cancel_button, dialog.reset_zoom_button, dialog.help_button):
            bounds = QRect(widget.mapTo(dialog, QPoint()), widget.size())
            self.assertTrue(dialog.rect().contains(bounds), bounds)
            self.assertTrue(widget.isVisible())

    def test_compact_calibration_draw_validate_and_apply_with_enter(self) -> None:
        dialog = CalibrationDialog(self.source, None, None)
        self.show_compact(dialog)
        self.assertFalse(dialog.apply_button.isEnabled())
        preview = dialog.preview
        start = preview.image_to_widget(50, 80).toPoint()
        end = preview.image_to_widget(150, 80).toPoint()
        QTest.mousePress(preview, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(preview, end)
        QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, pos=end)
        self.assertTrue(dialog.apply_button.isEnabled())
        self.assertTrue(dialog.clear_button.isEnabled())
        QTest.keyClick(preview, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertFalse(dialog.apply_button.isEnabled())
        QTest.keyClick(preview, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(dialog.apply_button.isEnabled())

        edit = dialog.known_length_edit
        edit.setFocus()
        edit.selectAll()
        QTest.keyClicks(edit, "0nm")
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertEqual(dialog.calibration_result_label.text(), "长度无效")
        self.assertEqual(edit.palette().text().color().name(), COLORS.error)
        self.assertEqual(dialog.calibration_result_label.palette().windowText().color().name(), COLORS.error)
        with self.assertRaises(ValueError):
            dialog.calculated_override()
        edit.selectAll()
        QTest.keyClicks(edit, "500nm")
        self.assertTrue(dialog.apply_button.isEnabled())
        self.assertNotEqual(edit.palette().text().color().name(), COLORS.error)
        self.assertNotEqual(dialog.calibration_result_label.palette().windowText().color().name(), COLORS.error)
        QTest.keyClick(edit, Qt.Key.Key_Return)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        result = dialog.result_calibration_override()
        self.assertEqual(result.known_length_nm, 500)
        self.assertAlmostEqual(result.pixel_length, 100, delta=1)

    def test_compact_measurement_toolbar_undo_escape_and_cancel_preserve_input(self) -> None:
        original = [MeasurementOverride("distance", 50, 80, 150, 80)]
        dialog = MeasurementPreviewDialog(
            self.source, original, calibration=Calibration(2.5, "test metadata " * 30),
        )
        self.show_compact(dialog)
        preview = dialog.preview
        preview.select_annotation(0)
        QTest.mouseClick(dialog.delete_selected_button, Qt.MouseButton.LeftButton)
        self.assertEqual(preview.annotations, [])
        self.assertEqual(len(original), 1)
        preview.setFocus()
        QTest.keyClick(preview, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(preview.annotations, original)

        start = preview.image_to_widget(60, 140).toPoint()
        end = preview.image_to_widget(160, 140).toPoint()
        QTest.mousePress(preview, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(preview, end)
        QTest.keyClick(preview, Qt.Key.Key_Escape)
        QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, pos=end)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(preview.annotations, original)
        QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(len(original), 1)


if __name__ == "__main__":
    unittest.main()
