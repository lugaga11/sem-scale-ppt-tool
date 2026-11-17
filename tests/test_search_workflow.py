from __future__ import annotations

import gc
import os
import tempfile
import threading
import unittest
import weakref
from contextlib import closing
from pathlib import Path
from typing import cast
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QMimeData, QPoint, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from sem_tool.annotation.core import CalibrationOverride, MeasurementOverride
from sem_tool.annotation.preview import CalibrationDialog, MeasurementPreviewDialog
from sem_tool.app import MainWindow, install_qt_translation
from sem_tool.files import image_paths_from_inputs, natural_key, path_key
from sem_tool.imaging import image_to_display_rgb
from sem_tool.search.engine import SearchResult, extract_features
from sem_tool.search.index import ImageIndex, IndexJob, IndexResult, IndexStatus, extract_index_results
from sem_tool.search.ui import CLIPBOARD_KEEP_DAYS, ResultWidget, prune_clipboard_images
from sem_tool.search.worker import ProgressThrottler, SearchWorker
from sem_tool.settings import AppSettings, default_db_path
from sem_tool.theme import apply_theme


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

    def test_search_reads_features_in_chunks_and_never_returns_a_moved_original(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gallery = root / "gallery"
            gallery.mkdir()
            for number in range(5):
                texture(number + 1).save(gallery / f"{number}.png")
            query = root / "query.png"
            texture(4).crop((45, 35, 420, 320)).save(query)
            index = ImageIndex(root / "index.sqlite")
            index.update(gallery, max_workers=2)
            progress = []
            with patch("sem_tool.search.index.FEATURE_CHUNK_ROWS", 2):
                results = index.search(gallery, query, top_n=3, progress=lambda current, total, _text: progress.append((current, total)))
                self.assertEqual(results[0].path, gallery / "3.png")
                self.assertEqual(len(results), 3)
                self.assertEqual(progress, [(number, 5) for number in range(1, 6)])
                (gallery / "3.png").unlink()
                remaining = index.search(gallery, query, top_n=5)
            self.assertEqual(len(remaining), 4)
            self.assertNotIn(gallery / "3.png", [result.path for result in remaining])

    def test_feature_extraction_releases_each_result_once_it_is_consumed(self) -> None:
        alive = weakref.WeakSet()

        def fake_job(job: IndexJob) -> IndexResult:
            result = IndexResult(job, 1, 1, 0, b"", None, None)
            alive.add(result)
            return result

        jobs = [IndexJob(Path(f"{number}.tif"), f"{number}.tif", ".tif", 1, 1.0) for number in range(200)]
        consumed = 0
        with patch("sem_tool.search.index.extract_index_job", new=fake_job):
            for result in extract_index_results(jobs, max_workers=2):
                consumed += 1
                del result
                if consumed == 150:
                    gc.collect()
                    self.assertLessEqual(len(alive), 4)  # the window of two jobs per worker
        self.assertEqual(consumed, 200)

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

    def test_natural_key_orders_numbers_and_accepts_superscript_digits(self) -> None:
        names = ["样品10.tif", "样品2.tif", "样品1²3.tif", "样品1.tif"]
        ordered = sorted(names, key=lambda name: natural_key(Path(name)))
        self.assertEqual(ordered, ["样品1.tif", "样品1²3.tif", "样品2.tif", "样品10.tif"])

    def test_old_pasted_screenshots_are_pruned_and_other_files_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            old, recent, unrelated = folder / "clipboard_old.png", folder / "clipboard_new.png", folder / "notes.png"
            for path in (old, recent, unrelated):
                path.write_bytes(b"png")
            stale = old.stat().st_mtime - (CLIPBOARD_KEEP_DAYS + 1) * 24 * 3600
            os.utime(old, (stale, stale))
            os.utime(unrelated, (stale, stale))
            prune_clipboard_images(folder)
            self.assertEqual(sorted(path.name for path in folder.iterdir()), ["clipboard_new.png", "notes.png"])

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
        cls.app = cast(QApplication, QApplication.instance() or QApplication([]))
        cls.app.setQuitOnLastWindowClosed(False)
        apply_theme(cls.app)

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
        self.window.annotation_panel.file_list.dismiss_hover()
        self.window.annotation_panel.file_list.name_delegate.timer.stop()
        self.app.clipboard().clear()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
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

    def test_queue_thumbnails_preserve_16_bit_display_and_sort_keeps_selection(self) -> None:
        first = self.make_original("10.tif")
        second = self.root / "2.tif"
        values = np.tile(np.linspace(1000, 54000, 320, dtype=np.uint16), (240, 1))
        Image.fromarray(values).save(second, tiffinfo={270: "PixelSize=5"})
        original_bytes = second.read_bytes()
        with closing(Image.open(second)) as image:
            expected = image_to_display_rgb(image)
        panel = self.window.annotation_panel
        panel.add_paths([first, second])
        self.window.show()
        panel.select_path(second)
        thumbnails = panel.file_list.name_delegate.thumbnails
        self.wait_until(lambda: str(second) in thumbnails)
        thumbnail = thumbnails[str(second)].toImage()
        self.assertFalse(thumbnail.isNull())
        self.assertLessEqual(thumbnail.width(), 64)
        self.assertLessEqual(thumbnail.height(), 44)
        expected_pixels = np.asarray(expected.resize((thumbnail.width(), thumbnail.height()), Image.Resampling.BILINEAR))
        for x in (5, 25, 50):
            self.assertAlmostEqual(thumbnail.pixelColor(x, 20).red(), int(expected_pixels[20, x, 0]), delta=3)
        self.assertEqual(second.read_bytes(), original_bytes)

        panel.set_row_checked(1, 0, False)
        panel.sort_queue()
        self.assertEqual(panel.item_path(0), second)
        self.assertEqual(panel.selected_rows(), [0])
        self.assertFalse(panel.row_checked(0, 0))
        panel.remove_selected()
        self.assertEqual(panel.file_list.rowCount(), 1)
        self.assertNotIn(str(second), thumbnails)
        panel.clear_queue()
        self.assertEqual(panel.file_list.rowCount(), 0)
        self.assertFalse(thumbnails)

    def test_unreadable_thumbnail_does_not_reuse_another_image(self) -> None:
        original = self.make_original()
        broken = self.root / "损坏图片.tif"
        broken.write_bytes(b"not a TIFF")
        panel = self.window.annotation_panel
        panel.add_paths([original, broken])
        self.window.show()
        thumbnails = panel.file_list.name_delegate.thumbnails
        self.wait_until(lambda: str(original) in thumbnails and str(broken) in thumbnails)
        self.assertFalse(thumbnails[str(original)].isNull())
        self.assertTrue(thumbnails[str(broken)].isNull())
        self.assertEqual(self.messages, [])

    def test_drag_import_and_toolbar_bulk_checks_preserve_measurements_and_selection(self) -> None:
        original = self.make_original()
        folder = self.root / "子目录"
        folder.mkdir()
        second = self.make_original("子目录/第二张.tif")
        panel = self.window.annotation_panel
        queue_changes = []
        panel.queue_changed.connect(queue_changes.append)
        self.window.resize(940, 620)
        self.window.show()
        self.app.processEvents()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(original)), QUrl.fromLocalFile(str(folder))])
        enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(panel.file_list.viewport(), enter)
        self.app.sendEvent(panel.file_list.viewport(), drop)
        self.assertTrue(drop.isAccepted())
        self.assertEqual(panel.checked_paths(), [original, second])
        panel.select_path(original)
        annotations = [MeasurementOverride("distance", 20, 70, 120, 70)]
        panel.measurement_overrides[path_key(original)] = annotations
        with patch.object(panel, "update_count", wraps=panel.update_count) as update_count:
            QTest.mouseClick(panel.unselect_all_button, Qt.MouseButton.LeftButton)
            update_count.assert_called_once()
        self.assertEqual(panel.checked_paths(), [])
        self.assertEqual(panel.selected_rows(), [0])
        self.assertEqual(panel.measurement_overrides[path_key(original)], annotations)

        self.assertEqual(queue_changes, [[original, second]])

        actions = {action.text(): action for action in panel.queue_menu.actions()}
        with patch("sem_tool.annotation.ui.QFileDialog.getOpenFileNames", return_value=([str(original)], "")):
            actions["添加文件…"].trigger()
        with patch("sem_tool.annotation.ui.QFileDialog.getExistingDirectory", return_value=str(folder)):
            actions["添加文件夹…"].trigger()
        self.assertEqual(panel.file_list.rowCount(), 2)
        self.assertEqual(panel.checked_paths(), [])
        QTest.mouseClick(panel.select_all_button, Qt.MouseButton.LeftButton)
        self.assertEqual(panel.checked_paths(), [original, second])
        self.assertEqual(panel.measurement_overrides[path_key(original)], annotations)

    def test_thumbnail_hover_enlarges_original_preserves_pixels_and_hides_on_leave_and_scroll(self) -> None:
        original = self.root / "16位悬停.tif"
        values = np.tile(np.linspace(1000, 54000, 320, dtype=np.uint16), (240, 1))
        Image.fromarray(values).save(original, tiffinfo={270: "PixelSize=2.5"})
        before = original.read_bytes()
        panel = self.window.annotation_panel
        panel.add_paths([original] + [self.make_original(f"{number}.tif") for number in range(12)])
        panel.select_path(panel.item_path(1))
        self.window.show()
        self.app.processEvents()
        queue = panel.file_list
        target = queue.name_delegate.thumbnail_rect(queue.visualRect(queue.model().index(0, 1))).center()
        QTest.mouseMove(queue.viewport(), QPoint(1, 1))
        QTest.mouseMove(queue.viewport(), target)
        self.wait_until(lambda: queue.hover_card.isVisible())
        card = queue.hover_card
        self.assertEqual(card.path, original)
        self.assertEqual(card.original_size.toTuple(), (320, 240))
        self.assertGreater(card.image.pixmap.width(), 64)
        self.assertGreater(card.image.pixmap.toImage().pixelColor(250, 50).red(), 180)
        self.assertIn("TIFF", card.metadata.text())
        self.assertIn("2.5 nm/px", card.details.text())
        self.assertEqual(panel.selected_rows(), [1])
        self.assertEqual(original.read_bytes(), before)
        self.assertTrue(card.screen().availableGeometry().contains(card.geometry()))

        QTest.mouseMove(queue.viewport(), target + QPoint(130, 0))
        self.assertFalse(card.isVisible())
        QTest.mouseMove(queue.viewport(), target)
        self.wait_until(lambda: card.isVisible())
        queue.verticalScrollBar().setValue(queue.verticalScrollBar().maximum())
        self.assertFalse(card.isVisible())
        self.assertFalse(queue.hover_timer.isActive())

    def test_hover_reloads_changed_files_clears_failed_image_and_closes_when_queue_is_cleared(self) -> None:
        original = self.make_original()
        panel = self.window.annotation_panel
        panel.add_paths([original])
        self.window.show()
        self.app.processEvents()
        queue = panel.file_list
        target = queue.name_delegate.thumbnail_rect(queue.visualRect(queue.model().index(0, 1))).center()
        QTest.mouseMove(queue.viewport(), QPoint(1, 1))
        QTest.mouseMove(queue.viewport(), target)
        self.wait_until(lambda: queue.hover_card.isVisible())
        QTest.mouseMove(queue.viewport(), target + QPoint(130, 0))
        Image.new("L", (200, 100), 73).save(original)
        QTest.mouseMove(queue.viewport(), target)
        self.wait_until(lambda: queue.hover_card.isVisible())
        self.assertEqual(queue.hover_card.original_size.toTuple(), (200, 100))
        self.assertEqual(queue.hover_card.image.pixmap.toImage().pixelColor(50, 50).red(), 73)
        QTest.mouseMove(queue.viewport(), target + QPoint(130, 0))
        original.write_bytes(b"corrupt TIFF")
        QTest.mouseMove(queue.viewport(), target)
        self.wait_until(lambda: queue.hover_card.isVisible())
        self.assertTrue(queue.hover_card.error)
        self.assertTrue(queue.hover_card.image.pixmap.isNull())
        panel.clear_queue()
        self.assertFalse(queue.hover_card.isVisible())
        self.assertFalse(queue.hover_timer.isActive())

    def test_queue_double_click_opens_original_only_in_file_column(self) -> None:
        original = self.root / "原图.tif"
        texture().save(original)
        panel = self.window.annotation_panel
        panel.add_paths([original])
        self.window.show()
        self.app.processEvents()
        queue = panel.file_list
        checkbox = queue.item(0, 0)
        assert checkbox is not None
        with patch("sem_tool.widgets.open_path") as open_original:
            for _ in range(2):
                position = queue.visualItemRect(checkbox).center()
                QTest.mouseClick(queue.viewport(), Qt.MouseButton.LeftButton, pos=position)
                QTest.mouseDClick(queue.viewport(), Qt.MouseButton.LeftButton, pos=position)
                QTest.mouseRelease(queue.viewport(), Qt.MouseButton.LeftButton, pos=position)
            open_original.assert_not_called()
            position = queue.visualRect(queue.model().index(0, 1)).center()
            QTest.mouseClick(queue.viewport(), Qt.MouseButton.LeftButton, pos=position)
            QTest.mouseDClick(queue.viewport(), Qt.MouseButton.LeftButton, pos=position)
            QTest.mouseRelease(queue.viewport(), Qt.MouseButton.LeftButton, pos=position)
            open_original.assert_called_once_with(original.resolve())

    def test_queue_checkboxes_and_scale_controls_respond_to_mouse_and_keyboard(self) -> None:
        panel = self.window.annotation_panel
        panel.add_paths([self.make_original()])
        self.window.show()
        self.app.processEvents()
        queue = panel.file_list
        checkbox = queue.item(0, 0)
        assert checkbox is not None
        QTest.mouseClick(queue.viewport(), Qt.MouseButton.LeftButton, pos=queue.visualItemRect(checkbox).center())
        self.assertFalse(panel.row_checked(0, 0))
        QTest.keyClick(queue, Qt.Key.Key_Space)
        self.assertTrue(panel.row_checked(0, 0))
        QTest.mouseClick(queue.viewport(), Qt.MouseButton.LeftButton, pos=queue.visualRect(queue.model().index(0, 1)).center())
        self.assertTrue(panel.row_checked(0, 0))
        for expected in (False, True):
            QTest.mouseClick(queue.viewport(), Qt.MouseButton.LeftButton, pos=queue.visualItemRect(checkbox).center())
            self.assertEqual(panel.row_checked(0, 0), expected)

        QTest.mouseClick(panel.auto_scale_choice.buttons["长"], Qt.MouseButton.LeftButton)
        self.assertEqual(panel.auto_scale_choice.currentText(), "长")
        self.assertFalse(panel.auto_scale_choice.buttons["中"].isChecked())
        QTest.mouseClick(panel.auto_scale_check, Qt.MouseButton.LeftButton)
        self.assertFalse(panel.auto_scale_check.isChecked())
        self.assertTrue(panel.fixed_scale_edit.isVisible())
        QTest.keyClick(panel.auto_scale_check, Qt.Key.Key_Space)
        self.assertTrue(panel.auto_scale_check.isChecked())
        self.assertTrue(panel.auto_scale_choice.isVisible())
        self.assertEqual(panel.auto_scale_choice.currentText(), "长")

    def test_compact_inspector_keeps_generate_accessible_and_output_controls_reachable(self) -> None:
        panel = self.window.annotation_panel
        self.window.resize(940, 620)
        self.window.show()
        self.app.processEvents()
        # Showing the status bar posts a second layout pass before its height settles.
        layout_loop = QEventLoop()
        QTimer.singleShot(0, layout_loop.quit)
        layout_loop.exec()
        button_position = panel.generate_button.mapTo(panel, panel.generate_button.rect().topLeft())
        scroll = panel.settings_scroll.verticalScrollBar()
        collapsed_maximum = scroll.maximum()
        QTest.mouseClick(panel.advanced_toggle, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: scroll.maximum() > collapsed_maximum)
        self.assertEqual(self.window.size().toTuple(), (940, 620))
        self.assertEqual(panel.generate_button.mapTo(panel, panel.generate_button.rect().topLeft()), button_position)
        self.assertGreater(button_position.x(), panel.width() // 2)
        self.assertGreater(button_position.y(), panel.height() * 0.75)
        self.assertTrue(
            panel.rect().contains(panel.generate_button.mapTo(panel, panel.generate_button.rect().bottomRight()))
        )
        scroll.setValue(scroll.maximum())
        self.app.processEvents()
        viewport = panel.settings_scroll.viewport()
        self.assertTrue(viewport.rect().contains(panel.output_edit.mapTo(viewport, panel.output_edit.rect().center())))
        panel.output_format_control.buttons["SVG"].click()
        self.assertEqual(panel.generate_button.text(), "生成 SVG")
        self.assertEqual(Path(panel.output_edit.text()).suffix, ".svg")
        panel.advanced_toggle.setChecked(False)
        panel.auto_scale_check.setChecked(False)
        self.app.processEvents()
        self.assertTrue(panel.fixed_scale_edit.isVisible())
        self.assertFalse(panel.auto_scale_choice.isVisible())

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
            assert isinstance(dialog, MeasurementPreviewDialog)
            dialog.preview.annotations = [annotation]
            dialog.accept()

        widget = search.results_list.itemWidget(search.results_list.item(0))
        assert isinstance(widget, ResultWidget)
        self.assertEqual(widget.add_button.text(), "加入队列")
        widget.add_button.click()
        self.assertIs(self.window.tabs.currentWidget(), search)
        self.assertIsNone(self.app.activeModalWidget())
        self.assertFalse(panel.measurement_overrides)
        self.assertEqual(self.window.tabs.tabText(0), "标注与导出 (1)")
        self.assertEqual(panel.item_path(0), original)
        calibration = panel.effective_calibration(original)
        assert calibration is not None
        self.assertEqual(calibration.nm_per_pixel, 2.5)
        self.window.tabs.setCurrentWidget(panel)
        QTimer.singleShot(0, apply_dialog)
        cell = panel.measurement_cell(0)
        assert cell is not None
        cell.edit_button.click()
        self.assertEqual(panel.measurement_overrides[path_key(original)], [annotation])
        self.assertIs(self.window.tabs.currentWidget(), panel)
        # Importing an existing path must preserve the measurement and generation checkbox.
        panel.set_row_checked(0, 0, False)
        self.window.tabs.setCurrentWidget(search)
        self.window.add_to_annotation_queue([original])
        self.assertIs(self.window.tabs.currentWidget(), search)
        self.assertEqual(panel.file_list.rowCount(), 1)
        self.assertFalse(panel.row_checked(0, 0))
        self.assertEqual(panel.measurement_overrides[path_key(original)], [annotation])
        panel.set_row_checked(0, 0, True)
        self.window.tabs.setCurrentWidget(panel)

        for fmt in ("SVG", "PPTX", "SVG", "PPTX"):
            panel.output_format_control.buttons[fmt].setChecked(True)
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
        cell = panel.measurement_cell(0)
        assert cell is not None
        self.assertEqual(cell.edit_button.text(), "手动标定")
        dialog_timer = QTimer(self.window)
        dialog_timer.timeout.connect(handle_dialog)
        dialog_timer.start(10)
        try:
            cell.edit_button.click()
        finally:
            dialog_timer.stop()
        self.assertEqual(calls, ["calibrate"])
        self.assertIsNone(self.app.activeModalWidget())
        self.assertIs(self.window.tabs.currentWidget(), panel)
        self.assertEqual(panel.calibration_overrides[path_key(path)], override)
        calibration = panel.effective_calibration(path)
        assert calibration is not None
        self.assertEqual(calibration.nm_per_pixel, 10)
        self.assertFalse(panel.measurement_overrides)
        self.assertEqual(cell.edit_button.text(), "编辑")
        dialog_timer.start(10)
        try:
            cell.edit_button.click()
        finally:
            dialog_timer.stop()
        self.assertEqual(calls, ["calibrate", "preview"])

    def test_search_added_state_tracks_queue_membership_and_new_results(self) -> None:
        original = self.make_original()
        search = self.window.search_panel
        panel = self.window.annotation_panel
        self.window.tabs.setCurrentWidget(search)
        search._add_result(1, SearchResult(original, 1, 20, 30, 2))
        widget = search.results_list.itemWidget(search.results_list.item(0))
        assert isinstance(widget, ResultWidget)
        widget.add_button.click()
        self.assertEqual(widget.add_button.text(), "已加入")
        self.assertTrue(widget.add_button.isEnabled())
        search._add_result(2, SearchResult(original, 1, 20, 30, 2))
        duplicate = search.results_list.itemWidget(search.results_list.item(1))
        assert isinstance(duplicate, ResultWidget)
        self.assertEqual(duplicate.add_button.text(), "已加入")
        other = self.root / "other" / original.name
        other.parent.mkdir()
        texture().save(other)
        panel.add_paths([other])
        panel.select_path(other)
        panel.set_row_checked(1, 0, False)
        annotation = MeasurementOverride("distance", 0, 20, 100, 20)
        calibration = CalibrationOverride(500, 0, 0, 100, 0)
        panel.measurement_overrides[path_key(other)] = [annotation]
        panel.calibration_overrides[path_key(other)] = calibration
        panel.measurement_overrides[path_key(original)] = [annotation]
        panel.calibration_overrides[path_key(original)] = calibration
        button_size = widget.add_button.size()
        duplicate.add_button.click()
        self.assertIs(self.window.tabs.currentWidget(), search)
        self.assertEqual(panel.file_list.rowCount(), 1)
        self.assertEqual(panel.item_path(0), other)
        self.assertFalse(panel.row_checked(0, 0))
        self.assertEqual(panel.measurement_overrides[path_key(other)], [annotation])
        self.assertEqual(panel.calibration_overrides[path_key(other)], calibration)
        for state in (panel.measurement_overrides, panel.calibration_overrides, panel.metadata_calibrations):
            self.assertNotIn(path_key(original), state)
        self.assertEqual(self.window.tabs.tabText(0), "标注与导出 (1)")
        for result in (widget, duplicate):
            self.assertEqual(result.add_button.text(), "加入队列")
            self.assertEqual(result.add_button.size(), button_size)
        widget.add_button.click()
        self.assertEqual(panel.file_list.rowCount(), 2)
        self.assertEqual(duplicate.add_button.text(), "已加入")
        panel.select_path(original)
        panel.remove_selected()
        for result in (widget, duplicate):
            self.assertEqual(result.add_button.text(), "加入队列")
            self.assertTrue(result.add_button.isEnabled())
        panel.add_paths([original])
        self.assertEqual(widget.add_button.text(), "已加入")
        missing = self.root / "gone.tif"
        search._add_result(3, SearchResult(missing, 1, 20, 30, 2))
        failed = search.results_list.itemWidget(search.results_list.item(2))
        assert isinstance(failed, ResultWidget)
        failed.add_button.click()
        self.assertEqual(failed.add_button.text(), "加入队列")
        self.assertTrue(failed.add_button.isEnabled())
        self.assertEqual([title for title, _ in self.messages], ["原图不可用"])

    def test_drops_on_the_path_fields_are_routed_by_content(self) -> None:
        gallery = self.root / "图库"
        gallery.mkdir()
        screenshot = self.root / "截图.png"
        texture().save(screenshot)
        search = self.window.search_panel
        self.window.tabs.setCurrentWidget(search)
        self.window.show()
        self.app.processEvents()

        def drop(target, path: Path) -> bool:
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(path))])
            enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(target, enter)
            event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(target, event)
            return event.isAccepted()

        self.assertTrue(drop(search.query_edit, gallery))
        self.assertEqual(search.root_edit.text(), str(gallery))
        self.assertEqual(search.query_edit.text(), "")
        self.assertEqual(search.settings.last_gallery_dir, str(gallery.resolve()))
        self.wait_until(lambda: "当前图库" in search.index_status_label.text())
        self.assertTrue(drop(search.root_edit, screenshot))
        self.assertEqual(search.query_edit.text(), str(screenshot))
        self.assertEqual(search.root_edit.text(), str(gallery))

    def test_search_result_actions_report_missing_files_instead_of_failing_silently(self) -> None:
        search = self.window.search_panel
        search._add_result(1, SearchResult(self.root / "已移走.tif", 1, 20, 30, 2))
        widget = search.results_list.itemWidget(search.results_list.item(0))
        assert isinstance(widget, ResultWidget)
        widget.open_file()
        self.assertEqual(self.messages[-1][0], "文件不存在")
        with patch("sem_tool.widgets.reveal_path", side_effect=OSError("no file manager")):
            widget.reveal_file()
        self.assertEqual(self.messages[-1], ("无法定位文件", "no file manager"))

    def test_editor_io_errors_are_reported_and_programming_errors_propagate(self) -> None:
        original = self.make_original()
        panel = self.window.annotation_panel
        panel.add_paths([original])
        for constructor, action in (
            ("CalibrationDialog", panel.calibrate_selected),
            ("MeasurementPreviewDialog", panel.preview_selected_measurement),
        ):
            with patch(f"sem_tool.annotation.ui.{constructor}", side_effect=OSError("cannot read image")):
                action()
                self.assertEqual(self.messages[-1][1], "cannot read image")
            with patch(f"sem_tool.annotation.ui.{constructor}", side_effect=RuntimeError("unexpected bug")):
                with self.assertRaisesRegex(RuntimeError, "unexpected bug"):
                    action()

    def test_missing_result_is_reported_without_creating_a_queue_entry(self) -> None:
        self.window.add_to_annotation_queue([self.root / "gone.tif"])
        self.assertEqual([title for title, _ in self.messages], ["原图不可用"])
        self.assertEqual(self.window.annotation_panel.file_list.rowCount(), 0)

    def test_individual_result_additions_keep_order_and_same_name_files_remain_distinct(self) -> None:
        first = self.make_original()
        nested = self.root / "其他文件夹"
        nested.mkdir()
        second = nested / first.name
        texture(3).save(second, tiffinfo={270: "PixelSize=5"})
        search = self.window.search_panel
        self.window.tabs.setCurrentWidget(search)
        for rank, path in enumerate((second, first), 1):
            search._add_result(rank, SearchResult(path, 1, 20, 30, 2))
            widget = search.results_list.itemWidget(search.results_list.item(rank - 1))
            assert isinstance(widget, ResultWidget)
            widget.add_button.click()
        self.assertIs(self.window.tabs.currentWidget(), search)
        panel = self.window.annotation_panel
        self.assertEqual(panel.file_list.columnCount(), 3)
        self.assertEqual(panel.checked_paths(), [second, first])
        item = panel.file_list.item(0, 1)
        assert item is not None
        self.assertIn("其他文件夹", item.text())
        override = MeasurementOverride("distance", 1, 1, 11, 1)
        panel.measurement_overrides[path_key(second)] = [override]
        panel.reorder_queue_rows([0], 2)
        self.assertEqual(panel.checked_paths(), [first, second])
        self.assertEqual(panel.measurement_overrides_for_generation()[second], [override])
        panel.clear_queue()
        self.assertFalse(panel.measurement_overrides)
        self.assertEqual(self.window.tabs.tabText(0), "标注与导出 (0)")

    def test_qt_dialog_buttons_and_field_menus_follow_the_interface_language(self) -> None:
        translator = install_qt_translation(self.app)
        if translator is None:
            self.skipTest("this Qt build ships without translations")
        try:
            box = QMessageBox(QMessageBox.Icon.Question, "标题", "内容", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            self.assertIn("是", box.button(QMessageBox.StandardButton.Yes).text())
            self.assertEqual(box.button(QMessageBox.StandardButton.Cancel).text(), "取消")
            menu = self.window.annotation_panel.color_edit.createStandardContextMenu()
            self.assertTrue(any("粘贴" in action.text() for action in menu.actions()))
            menu.deleteLater()
            box.deleteLater()
        finally:
            self.app.removeTranslator(translator)

    def test_page_margin_is_bounded_so_the_largest_value_still_exports(self) -> None:
        panel = self.window.annotation_panel
        panel.add_paths([self.make_original("margin.tif")])
        panel.margin_spin.setValue(99)
        self.assertEqual(panel.margin_spin.value(), panel.margin_spin.maximum())
        self.assertEqual(panel.margin_spin.suffix(), " inch")
        for fmt in ("SVG", "PPTX"):
            panel.output_format_control.buttons[fmt].setChecked(True)
            panel.output_edit.setText(str(self.root / f"margin.{fmt.lower()}"))
            panel.generate()
            self.wait_until(lambda: panel.thread is None)
        self.assertEqual([title for title, _ in self.messages], ["完成", "完成"])
        self.assertTrue((self.root / "margin_01_margin.svg").is_file())
        self.assertTrue((self.root / "margin.pptx").is_file())

    def test_search_shortcut_is_scoped_to_search_panel(self) -> None:
        self.assertTrue(
            all(
                action.shortcutContext() == Qt.ShortcutContext.WidgetWithChildrenShortcut
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
        QTest.keyClick(panel.output_edit, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(panel.output_edit.text(), "normal text")

    def answer_messages(self, button: QMessageBox.StandardButton) -> QTimer:
        """Click one button of every message box instead of the default dismissal."""
        self.message_timer.stop()

        def answer() -> None:
            box = self.app.activeModalWidget()
            if isinstance(box, QMessageBox):
                self.messages.append((box.windowTitle(), box.text()))
                box.button(button).click()

        timer = QTimer(self.window)
        timer.timeout.connect(answer)
        timer.start(10)
        return timer

    def test_start_up_leaves_the_gallery_alone_until_the_search_page_is_opened(self) -> None:
        gallery = self.root / "图库"
        gallery.mkdir()
        texture().save(gallery / "a.png")
        AppSettings(str(gallery)).save()
        scanning_threads = []
        real_status = ImageIndex.status

        def recording_status(index, root, **kwargs):
            scanning_threads.append(threading.current_thread())
            return real_status(index, root, **kwargs)

        with patch.object(ImageIndex, "status", autospec=True, side_effect=recording_status):
            window = MainWindow()
            try:
                search = window.search_panel
                window.show()
                self.app.processEvents()
                self.assertEqual(search.root_edit.text(), str(gallery))
                self.assertEqual(scanning_threads, [])
                self.assertIsNone(search.thread)
                window.tabs.setCurrentWidget(search)
                self.wait_until(lambda: "建议更新索引" in search.index_status_label.text())
                self.assertEqual(len(scanning_threads), 1)
                self.assertIsNot(scanning_threads[0], threading.main_thread())
                # The background check never locks the page.
                self.assertTrue(search.search_button.isEnabled())
                self.assertTrue(search.progress.isHidden())
                self.wait_until(lambda: search.thread is None)
            finally:
                window.close()
                self.wait_until(lambda: window.search_panel.thread is None)
                window.annotation_panel.file_list.name_delegate.timer.stop()
                window.deleteLater()

    def test_status_check_does_not_replace_a_cleared_or_invalid_gallery(self) -> None:
        search = self.window.search_panel
        for replacement, expected in (("", "未选择图库"), (str(self.root / "missing"), "图库目录不存在")):
            for fail in (False, True):
                with self.subTest(replacement=replacement, fail=fail):
                    started, release = threading.Event(), threading.Event()

                    def delayed_status(index, root, **kwargs):
                        started.set()
                        release.wait(5)
                        if fail:
                            raise OSError("old gallery unavailable")
                        return IndexStatus(1, 1, 0, 0, 0, 0, 1, None)

                    with patch.object(ImageIndex, "status", autospec=True, side_effect=delayed_status):
                        search.root_edit.setText(str(self.root))
                        search.refresh_index_status()
                        try:
                            self.wait_until(started.is_set)
                            search.root_edit.setText(replacement)
                            search.refresh_index_status()
                        finally:
                            release.set()
                            self.wait_until(lambda: search.thread is None)
                    self.assertIn(expected, search.index_status_label.text())

    def test_search_with_a_stale_index_asks_first_and_maintenance_runs_in_the_worker(self) -> None:
        gallery = self.root / "图库"
        gallery.mkdir()
        source = texture()
        source.save(gallery / "source.tif")
        query = self.root / "crop.png"
        source.crop((45, 35, 420, 320)).save(query)
        search = self.window.search_panel
        search.root_edit.setText(str(gallery))
        search.query_edit.setText(str(query))

        search.start_search()
        self.wait_until(lambda: search.thread is None and bool(self.messages))
        self.assertEqual(self.messages.pop()[0], "需要索引")
        self.assertEqual(search.results_list.count(), 0)

        search.start_index()
        self.wait_until(lambda: search.thread is None)
        self.assertIn("索引已是最新", search.index_status_label.text())
        texture(7).save(gallery / "later.png")

        search.start_search()  # the default dismissal answers "no"
        self.wait_until(lambda: search.thread is None and bool(self.messages))
        self.assertEqual(self.messages.pop()[0], "建议更新索引")
        self.assertEqual(search.results_list.count(), 0)
        self.assertTrue(search.search_button.isEnabled())
        self.assertIn("新增 1", search.index_status_label.text())

        timer = self.answer_messages(QMessageBox.StandardButton.Yes)
        try:
            search.start_search()
            self.wait_until(lambda: search.thread is None and search.results_list.count() == 1)
        finally:
            timer.stop()
        self.assertEqual(self.messages.pop()[0], "建议更新索引")

        (gallery / "source.tif").unlink()
        search.prune_missing_index()
        self.wait_until(lambda: search.thread is None)
        self.assertIn("删除 1 条", search.status_label.text())
        timer = self.answer_messages(QMessageBox.StandardButton.Yes)
        try:
            search.clear_current_index()
            self.wait_until(lambda: search.thread is None and "已清空" in search.status_label.text())
            search.vacuum_index_database()
            self.wait_until(lambda: search.thread is None and "压缩完成" in search.status_label.text())
        finally:
            timer.stop()
        self.assertIn("已索引 0/1", search.index_status_label.text())

    def test_closing_waits_for_search_worker_to_finish(self) -> None:
        started = threading.Event()

        def delayed_search(_index, _root, _query, **kwargs):
            started.set()
            kwargs["progress"](1, 4, "matching")
            kwargs["cancel_event"].wait(2)
            return []

        search = self.window.search_panel
        self.window.tabs.setCurrentWidget(search)
        self.window.show()
        self.assertTrue(search.progress.isHidden())
        with patch.object(ImageIndex, "search", autospec=True, side_effect=delayed_search):
            worker = SearchWorker(self.root, search._new_cancel_event(), "search", query=self.root / "query.png")
            search._start_worker(worker, search._on_search_finished)
            self.wait_until(lambda: started.is_set() and search.progress.value() == 25)
            self.assertTrue(search.progress.isVisible())
            self.assertEqual(search.progress.height(), 3)
            self.assertIn("25%", search.status_label.text())
            self.assertFalse(self.window.close())
            assert search.cancel_event is not None
            self.assertTrue(search.cancel_event.is_set())
            self.wait_until(lambda: search.thread is None and not self.window.isVisible())
            self.assertTrue(search.progress.isHidden())

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
        panel.output_format_control.buttons["SVG"].setChecked(True)
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
            worker = SearchWorker(self.root, search._new_cancel_event(), "search", query=self.root / "query.png")
            search._start_worker(worker, search._on_search_finished)
            self.wait_until(lambda: search.thread is None)
        self.assertEqual(self.messages, [("操作失败", "test read failure")])
        self.assertTrue(search.search_button.isEnabled())
        self.assertTrue(search.root_edit.isEnabled())
        self.assertFalse(search.cancel_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
