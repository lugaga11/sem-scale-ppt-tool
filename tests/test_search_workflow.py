from __future__ import annotations

import os
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QMimeData, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from sem_tool.annotation.core import CalibrationOverride, MeasurementOverride
from sem_tool.annotation.preview import CalibrationDialog, MeasurementPreviewDialog
from sem_tool.app import MainWindow
from sem_tool.files import image_paths_from_inputs, path_key
from sem_tool.imaging import image_to_display_rgb
from sem_tool.search.engine import SearchResult, extract_features
from sem_tool.search.index import ImageIndex
from sem_tool.search.worker import ProgressThrottler, SearchWorker
from sem_tool.settings import AppSettings, default_db_path


def texture(seed: int = 10) -> Image.Image:
    values = np.random.default_rng(seed).integers(15, 240, (360, 480), dtype=np.uint8)
    return Image.fromarray(cv2.GaussianBlur(values, (3, 3), 0))


class SearchCoreTests(unittest.TestCase):
    def test_cropped_query_finds_original_and_index_tracks_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gallery = root / "图库 空格+(1)"
            gallery.mkdir()
            original = gallery / "原图.tif"
            distractor = gallery / "其他.png"
            source = texture()
            source.save(original, tiffinfo={270: "PixelSize=2.5"})
            texture(20).save(distractor)
            query = root / "截图.png"
            source.crop((45, 35, 420, 320)).save(query)
            index = ImageIndex(root / "index.sqlite")
            self.assertEqual(index.update(gallery, max_workers=2).indexed, 2)
            self.assertFalse(index.status(gallery).needs_update)
            results = index.search(gallery, query)
            self.assertEqual(results[0].path, original)
            self.assertGreater(results[0].inliers, 20)
            self.assertTrue(results[0].thumbnail)
            self.assertEqual(index.update(gallery).skipped, 2)
            distractor.unlink()
            self.assertTrue(index.status(gallery).needs_update)
            self.assertEqual(len(index.search(gallery, query)), 1)
            self.assertEqual(index.update(gallery).removed, 1)
            index.vacuum()
            self.assertEqual(index.clear(gallery), 1)
            self.assertTrue(original.exists())

    def test_16_bit_sem_uses_display_pixels_for_search_without_rewriting_original(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "高位深.tif"
            values = np.asarray(texture()).astype(np.uint16) * 200 + 1000
            Image.fromarray(values).save(source)
            before = source.read_bytes()
            with closing(Image.open(source)) as image:
                image_to_display_rgb(image).save(root / "query.png")
            features = extract_features(source)
            self.assertGreater(features.keypoint_count, 20)
            index = ImageIndex(root / "index.sqlite")
            index.update(root, max_workers=1)
            results = index.search(root, root / "query.png")
            self.assertTrue(any(result.path == source and result.inliers > 20 for result in results))
            self.assertEqual(source.read_bytes(), before)

    def test_failed_files_are_retried_and_featureless_images_do_not_stay_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            texture().save(root / "retry.tif")
            index = ImageIndex(root / "index.sqlite")
            with patch("sem_tool.search.index.extract_features", side_effect=OSError("temporary read error")):
                self.assertEqual(index.update(root, max_workers=1).errors, 1)
            self.assertEqual(index.update(root, max_workers=1).indexed, 1)
            Image.new("L", (50, 50), 100).save(root / "blank.png")
            index.update(root, max_workers=1)
            self.assertFalse(index.status(root).needs_update)

    def test_input_discovery_preserves_queue_order_and_deduplicates_overlapping_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nested = root / "sub"
            nested.mkdir()
            ten, two = nested / "10.webp", nested / "2.webp"
            texture().save(ten)
            texture().save(two)
            self.assertEqual(image_paths_from_inputs([ten, two, root, nested]), [ten, two])
            self.assertEqual(image_paths_from_inputs([root, nested]), [two, ten])

    def test_settings_reuse_old_location_and_ignore_malformed_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {"LOCALAPPDATA": temp}, clear=True):
                self.assertEqual(default_db_path(), Path(temp) / "ImageOriginFinder" / "index.sqlite")
            with patch.dict(os.environ, {"SEM_TOOL_DATA_DIR": temp}):
                settings = Path(temp) / "settings.json"
                for malformed in ("[]", "null", "{", '{"last_gallery_dir": null}'):
                    settings.write_text(malformed, encoding="utf-8")
                    self.assertEqual(AppSettings.load().last_gallery_dir, "")
                AppSettings("图库 空格+(1)").save()
                self.assertEqual(AppSettings.load().last_gallery_dir, "图库 空格+(1)")

    def test_progress_is_throttled_and_finishes_at_total(self) -> None:
        events = []
        throttler = ProgressThrottler(lambda *args: events.append(args), interval_seconds=100, step_interval=50)
        for number in range(1, 2068):
            throttler.emit(number, 2067, "matching")
        self.assertLess(len(events), 50)
        self.assertEqual(events[-1][0], 2067)


class SearchAnnotationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"SEM_TOOL_DATA_DIR": str(self.root / "data")})
        self.environment.start()
        self.window = MainWindow()
        self.messages = []
        self.message_timer = QTimer(self.window)
        self.message_timer.timeout.connect(self.dismiss_message)
        self.message_timer.start(10)

    def dismiss_message(self) -> None:
        dialog = self.app.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            self.messages.append((dialog.windowTitle(), dialog.text()))
            dialog.accept()

    def tearDown(self) -> None:
        self.window.close()
        self.wait_until(lambda: self.window.search_panel.thread is None and self.window.annotation_panel.thread is None)
        self.message_timer.stop()
        self.app.clipboard().clear()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.environment.stop()
        self.temp.cleanup()

    def wait_until(self, predicate, timeout: float = 30.0) -> None:
        if predicate():
            return
        loop = QEventLoop()
        poll = QTimer()
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        poll.start(10)
        deadline.start(int(timeout * 1000))
        loop.exec()
        poll.stop()
        deadline.stop()
        self.assertTrue(predicate(), "Background operation did not finish")

    def make_original(self, name: str = "原始 SEM.tif") -> Path:
        path = self.root / name
        texture().save(path, tiffinfo={270: "PixelSize=2.5"})
        return path

    def test_search_result_adds_without_switching_then_annotation_exports_both_formats(self) -> None:
        panel = self.window.annotation_panel
        self.assertIs(self.window.tabs.widget(0), panel)
        self.assertIs(self.window.tabs.currentWidget(), panel)
        gallery = self.root / "图库"
        gallery.mkdir()
        original = gallery / "source.tif"
        source = texture()
        source.save(original, tiffinfo={270: "PixelSize=2.5"})
        query = self.root / "crop.png"
        source.crop((45, 35, 420, 320)).save(query)
        search = self.window.search_panel
        self.window.tabs.setCurrentWidget(search)
        search.root_edit.setText(str(gallery))
        search.query_edit.setText(str(query))
        search.start_index()
        self.wait_until(lambda: search.thread is None)
        search.start_search()
        self.wait_until(lambda: search.thread is None)
        self.assertEqual(search.results_list.count(), 1)

        annotation = MeasurementOverride("distance", 20, 70, 120, 70)

        def apply_dialog():
            dialog = self.app.activeModalWidget()
            self.assertIsInstance(dialog, MeasurementPreviewDialog)
            dialog.preview.annotations = [annotation]
            dialog.accept()

        widget = search.results_list.itemWidget(search.results_list.item(0))
        self.assertEqual(widget.add_button.text(), "加入列表")
        widget.add_button.click()
        self.assertIs(self.window.tabs.currentWidget(), search)
        self.assertIsNone(self.app.activeModalWidget())
        self.assertFalse(panel.measurement_overrides)
        self.assertEqual(self.window.tabs.tabText(0), "标注与导出 (1)")
        self.assertEqual(panel.item_path(0), original)
        self.assertEqual(panel.effective_calibration(original).nm_per_pixel, 2.5)
        self.window.tabs.setCurrentWidget(panel)
        QTimer.singleShot(0, apply_dialog)
        panel.measurement_cell(0).edit_button.click()
        self.assertEqual(panel.measurement_overrides[path_key(original)], [annotation])
        self.assertIs(self.window.tabs.currentWidget(), panel)
        # Reusing the same result must preserve the measurement and generation checkbox.
        panel.set_row_checked(0, 0, False)
        self.window.tabs.setCurrentWidget(search)
        widget.add_button.click()
        self.assertIs(self.window.tabs.currentWidget(), search)
        self.assertEqual(panel.file_list.rowCount(), 1)
        self.assertFalse(panel.row_checked(0, 0))
        self.assertEqual(panel.measurement_overrides[path_key(original)], [annotation])
        panel.set_row_checked(0, 0, True)
        self.window.tabs.setCurrentWidget(panel)

        for fmt in ("SVG", "PPTX", "SVG", "PPTX"):
            panel.output_format_buttons[fmt].setChecked(True)
            panel.output_edit.setText(str(self.root / f"export.{fmt.lower()}"))
            panel.generate()
            self.wait_until(lambda: panel.thread is None)
        self.assertEqual([title for title, _ in self.messages], ["完成"] * 4)
        svg = (self.root / "export_01_source.svg").read_text(encoding="utf-8")
        svg_tree = ElementTree.fromstring(svg)
        labels = [element.text for element in svg_tree.findall("{http://www.w3.org/2000/svg}text")]
        self.assertIn("0.250 μm", labels)
        self.assertEqual(svg.count("data:image/png;base64,"), 1)
        self.assertNotIn("<marker", svg)
        with ZipFile(self.root / "export.pptx") as archive:
            slide = archive.read("ppt/slides/slide1.xml").decode("utf-8")
            labels = [
                element.text
                for element in ElementTree.fromstring(slide).iter(
                    "{http://schemas.openxmlformats.org/drawingml/2006/main}t"
                )
            ]
            self.assertIn("0.250 μm", labels)
            self.assertIn("p:cxnSp", slide)

    def test_calibration_returns_to_queue_and_measurement_requires_another_click(self) -> None:
        path = self.root / "普通.png"
        texture().save(path)
        override = CalibrationOverride(1000, 0, 50, 100, 50)
        panel = self.window.annotation_panel
        calls = []

        def handle_dialog():
            dialog = self.app.activeModalWidget()
            if isinstance(dialog, CalibrationDialog):
                calls.append("calibrate")
                dialog.preview.annotations = [MeasurementOverride("distance", 0, 50, 100, 50)]
                dialog.known_length_edit.setText("1um")
                dialog.update_info()
                dialog.apply_button.click()
            elif isinstance(dialog, MeasurementPreviewDialog):
                calls.append("preview")
                dialog.reject()

        self.window.add_to_annotation_queue([path])
        self.assertEqual(panel.measurement_cell(0).edit_button.text(), "手动标定")
        dialog_timer = QTimer(self.window)
        dialog_timer.timeout.connect(handle_dialog)
        dialog_timer.start(10)
        try:
            panel.measurement_cell(0).edit_button.click()
        finally:
            dialog_timer.stop()
        self.assertEqual(calls, ["calibrate"])
        self.assertIsNone(self.app.activeModalWidget())
        self.assertIs(self.window.tabs.currentWidget(), panel)
        self.assertEqual(panel.calibration_overrides[path_key(path)], override)
        self.assertEqual(panel.effective_calibration(path).nm_per_pixel, 10)
        self.assertFalse(panel.measurement_overrides)
        self.assertEqual(panel.measurement_cell(0).edit_button.text(), "编辑")
        dialog_timer.start(10)
        try:
            panel.measurement_cell(0).edit_button.click()
        finally:
            dialog_timer.stop()
        self.assertEqual(calls, ["calibrate", "preview"])

    def test_missing_result_is_reported_without_creating_a_queue_entry(self) -> None:
        self.window.add_to_annotation_queue([self.root / "gone.tif"])
        self.assertEqual([title for title, _ in self.messages], ["原图不可用"])
        self.assertEqual(self.window.annotation_panel.file_list.rowCount(), 0)

    def test_multi_selection_keeps_result_order_and_same_name_files_remain_distinct(self) -> None:
        first = self.make_original()
        nested = self.root / "其他文件夹"
        nested.mkdir()
        second = nested / first.name
        texture(3).save(second, tiffinfo={270: "PixelSize=5"})
        search = self.window.search_panel
        self.window.tabs.setCurrentWidget(search)
        for rank, path in enumerate((second, first), 1):
            search._add_result(rank, SearchResult(path, 1, 20, 30, 2))
            search.results_list.item(rank - 1).setSelected(True)
        search.add_selected_button.click()
        self.assertIs(self.window.tabs.currentWidget(), search)
        panel = self.window.annotation_panel
        self.assertEqual(panel.file_list.columnCount(), 3)
        self.assertEqual(panel.checked_paths(), [second, first])
        self.assertIn("其他文件夹", panel.file_list.item(0, 1).text())
        override = MeasurementOverride("distance", 1, 1, 11, 1)
        panel.measurement_overrides[path_key(second)] = [override]
        panel.reorder_queue_rows([0], 2)
        self.assertEqual(panel.checked_paths(), [first, second])
        self.assertEqual(panel.measurement_overrides_for_generation()[second], [override])
        panel.clear_queue()
        self.assertFalse(panel.measurement_overrides)
        self.assertEqual(self.window.tabs.tabText(0), "标注与导出 (0)")

    def test_search_shortcut_is_scoped_to_search_panel(self) -> None:
        self.assertTrue(
            all(
                action.shortcutContext() == Qt.WidgetWithChildrenShortcut
                for action in self.window.search_panel.actions()
            )
        )
        mime = QMimeData()
        mime.setText("normal text")
        self.app.clipboard().setMimeData(mime)
        panel = self.window.annotation_panel
        self.window.tabs.setCurrentWidget(panel)
        self.window.show()
        panel.output_edit.setFocus()
        panel.output_edit.selectAll()
        QTest.keyClick(panel.output_edit, Qt.Key_V, Qt.ControlModifier)
        self.assertEqual(panel.output_edit.text(), "normal text")

    def test_closing_waits_for_search_worker_to_finish(self) -> None:
        started = threading.Event()

        def delayed_search(_index, _root, _query, **kwargs):
            started.set()
            kwargs["cancel_event"].wait(2)
            return []

        search = self.window.search_panel
        self.window.show()
        with patch.object(ImageIndex, "search", autospec=True, side_effect=delayed_search):
            worker = SearchWorker(self.root, search._new_cancel_event(), query=self.root / "query.png")
            search._start_worker(worker, search._on_search_finished)
            self.wait_until(started.is_set)
            self.assertFalse(self.window.close())
            self.assertTrue(search.cancel_event.is_set())
            self.wait_until(lambda: search.thread is None and not self.window.isVisible())

    def test_closing_waits_for_export_without_dropping_the_output(self) -> None:
        from sem_tool.annotation.core import generate_svg

        started = threading.Event()
        release = threading.Event()

        def delayed_export(**options):
            started.set()
            release.wait(2)
            return generate_svg(**options)

        panel = self.window.annotation_panel
        panel.add_paths([self.make_original("original.tif")])
        panel.output_format_buttons["SVG"].setChecked(True)
        panel.output_edit.setText(str(self.root / "closing.svg"))
        self.window.show()
        with patch("sem_tool.annotation.worker.generate_svg", new=delayed_export):
            panel.generate()
            self.wait_until(started.is_set)
            self.assertFalse(self.window.close())
            release.set()
            self.wait_until(lambda: panel.thread is None and not self.window.isVisible())
        self.assertTrue((self.root / "closing_01_original.svg").is_file())
        self.assertFalse(self.messages)

    def test_failed_search_reports_error_and_restores_controls(self) -> None:
        search = self.window.search_panel
        with patch.object(ImageIndex, "search", side_effect=ValueError("test read failure")):
            worker = SearchWorker(self.root, search._new_cancel_event(), query=self.root / "query.png")
            search._start_worker(worker, search._on_search_finished)
            self.wait_until(lambda: search.thread is None)
        self.assertEqual(self.messages, [("操作失败", "test read failure")])
        self.assertTrue(search.search_button.isEnabled())
        self.assertTrue(search.root_edit.isEnabled())
        self.assertFalse(search.cancel_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
