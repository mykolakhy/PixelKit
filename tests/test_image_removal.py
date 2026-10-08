from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QAbstractItemView, QPushButton, QScrollArea

from pixelkit.app import ImageMagickStudio, PixelKitApplication
from pixelkit.presets import PresetStore
from pixelkit.report import BatchReport


class ImageRemovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = PixelKitApplication.instance() or PixelKitApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        settings = QSettings(str(self.root / "preferences.ini"), QSettings.Format.IniFormat)
        with patch("pixelkit.app.find_magick", return_value="magick"), patch(
            "pixelkit.video_panel.find_ffmpeg", return_value="ffmpeg"
        ), patch("pixelkit.video_panel.find_ffprobe", return_value="ffprobe"):
            self.window = ImageMagickStudio(PresetStore(settings))
        self.addCleanup(self.close_window)
        self.window.show()
        self.window.activateWindow()
        self.settle()
        self.empty_info = self.window.source_info.text()

    def close_window(self):
        self.window.worker = None
        self.window.video_panel.worker = None
        self.window._set_processing_state(False)
        self.window.video_panel._set_busy(False)
        self.window.close()
        self.settle()

    def settle(self):
        for _ in range(4):
            self.app.processEvents()

    def image(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"Original image contents: {name}".encode())
        return path

    def assert_queue(self, paths):
        self.assertEqual(self.window.sources, paths)
        self.assertEqual(self.window.source_list.count(), len(paths))
        self.assertEqual(
            [self.window.source_list.item(row).toolTip() for row in range(len(paths))],
            [str(path) for path in paths],
        )
        self.assertEqual(
            [self.window.source_list.item(row).text() for row in range(len(paths))],
            [path.name for path in paths],
        )

    def select_row(self, row):
        listing = self.window.source_list
        listing.scrollToItem(listing.item(row))
        self.settle()
        QTest.mouseClick(
            listing.viewport(), Qt.MouseButton.LeftButton,
            pos=listing.visualItemRect(listing.item(row)).center(),
        )
        self.settle()
        self.assertEqual(listing.currentRow(), row)
        self.assertTrue(listing.item(row).isSelected())

    def click_remove(self):
        QTest.mouseClick(self.window.remove_button, Qt.MouseButton.LeftButton)
        self.settle()

    def custom_output(self, path):
        self.window.output_edit.setText(str(path))
        self.window.output_edit.textEdited.emit(str(path))
        self.assertFalse(self.window.default_output)

    def queue_snapshot(self):
        return (
            list(self.window.sources),
            [self.window.source_list.item(row).toolTip() for row in range(self.window.source_list.count())],
            self.window.source_list.currentRow(),
            [self.window.source_list.row(item) for item in self.window.source_list.selectedItems()],
            self.window.output_edit.text(),
            self.window.default_output,
            self.window.source_info.text(),
            self.window.status_label.full_text,
        )

    def test_controls_offer_single_selection_and_local_delete_shortcuts(self):
        self.assertEqual(self.window.remove_button.text(), "Remove")
        self.assertEqual(self.window.clear_button.text(), "Clear all")
        self.assertEqual(
            self.window.source_list.selectionMode(), QAbstractItemView.SelectionMode.SingleSelection
        )
        action = self.window.remove_source_action
        self.assertIn(action, self.window.source_list.actions())
        self.assertEqual(action.shortcutContext(), Qt.ShortcutContext.WidgetShortcut)
        self.assertCountEqual(
            action.shortcuts(), [QKeySequence(Qt.Key.Key_Delete), QKeySequence(Qt.Key.Key_Backspace)]
        )
        self.assertFalse(self.window.remove_button.isEnabled())
        self.assertFalse(action.isEnabled())

    def test_initial_add_has_no_selected_row_and_cannot_remove(self):
        first, second = self.image("first.png"), self.image("second.jpg")
        self.window._append_sources([first, second])
        self.settle()
        self.assertEqual(self.window.source_list.selectedItems(), [])
        self.assertFalse(self.window.remove_button.isEnabled())
        self.assertFalse(self.window.remove_source_action.isEnabled())
        before = self.queue_snapshot()
        self.click_remove()
        self.window._remove_selected_source()
        self.assertEqual(self.queue_snapshot(), before)
        self.assert_queue([first, second])
        self.assertTrue(self.window.clear_button.isEnabled())

    def test_current_but_unselected_row_is_a_noop(self):
        files = [self.image(name) for name in ("one.png", "two.jpg", "three.webp")]
        self.window._set_sources(files)
        self.select_row(1)
        self.window.source_list.clearSelection()
        self.assertEqual(self.window.source_list.currentRow(), 1)
        self.assertFalse(self.window.remove_button.isEnabled())
        self.assertFalse(self.window.remove_source_action.isEnabled())
        before = self.queue_snapshot()
        self.window._remove_selected_source()
        for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            QTest.keyClick(self.window.source_list, key)
            self.settle()
            self.assertEqual(self.queue_snapshot(), before)
        self.assert_queue(files)

    def test_button_removes_exact_selected_path_and_preserves_original_files(self):
        files = [self.image(name) for name in ("first/photo.png", "second/photo.png", "third/end.jpg")]
        originals = {path: path.read_bytes() for path in files}
        self.window._set_sources(files)
        self.select_row(1)
        self.assertTrue(self.window.remove_button.isEnabled())
        self.assertTrue(self.window.remove_source_action.isEnabled())
        self.click_remove()
        self.assert_queue([files[0], files[2]])
        self.assertEqual(self.window.source_list.currentRow(), 1)
        self.assertTrue(self.window.source_list.item(1).isSelected())
        self.assertTrue(self.window.source_list.hasFocus())
        self.assertIn("2 images", self.window.source_info.text())
        self.assertEqual({path: path.read_bytes() for path in files}, originals)

    def test_delete_and_backspace_keep_next_then_preceding_row_selected(self):
        files = [self.image(f"image{index}.png") for index in range(4)]
        self.window._set_sources(files)
        self.select_row(1)
        QTest.keyClick(self.window.source_list, Qt.Key.Key_Delete)
        self.settle()
        self.assert_queue([files[0], files[2], files[3]])
        self.assertEqual(self.window.source_list.currentRow(), 1)
        self.assertTrue(self.window.source_list.item(1).isSelected())
        self.assertTrue(self.window.source_list.hasFocus())
        QTest.keyClick(self.window.source_list, Qt.Key.Key_End)
        self.assertEqual(self.window.source_list.currentRow(), 2)
        QTest.keyClick(self.window.source_list, Qt.Key.Key_Backspace)
        self.settle()
        self.assert_queue([files[0], files[2]])
        self.assertEqual(self.window.source_list.currentRow(), 1)
        self.assertTrue(self.window.source_list.item(1).isSelected())
        self.assertTrue(self.window.source_list.hasFocus())

    def test_removing_last_image_restores_empty_queue_and_action_state(self):
        source = self.image("only.png")
        original = source.read_bytes()
        self.window._set_sources([source])
        self.custom_output(self.root / "chosen output.webp")
        self.select_row(0)
        self.click_remove()
        self.assert_queue([])
        self.assertTrue(self.window.source_list.placeholder.isVisible())
        self.assertEqual(self.window.source_info.text(), self.empty_info)
        self.assertEqual(self.window.output_edit.text(), "")
        self.assertEqual(self.window.output_edit.placeholderText(), "Output file")
        self.assertEqual(self.window.output_button.text(), "Choose…")
        self.assertEqual(self.window.status_label.full_text, "Add images to get started")
        for control in (self.window.remove_button, self.window.remove_source_action,
                        self.window.clear_button, self.window.process_button):
            self.assertFalse(control.isEnabled())
        self.assertEqual(self.window.source_list.selectedItems(), [])
        self.assertTrue(self.window.source_list.hasFocus())
        before = self.queue_snapshot()
        QTest.keyClick(self.window.source_list, Qt.Key.Key_Delete)
        self.window._remove_selected_source()
        self.assertEqual(self.queue_snapshot(), before)
        self.assertEqual(source.read_bytes(), original)
        self.window._append_sources([source])
        self.assertTrue(self.window.default_output)
        self.assertTrue(self.window.process_button.isEnabled())
        self.assertEqual(self.window.source_list.selectedItems(), [])

    def test_custom_batch_folder_is_kept_until_single_file_with_selected_extension(self):
        files = [self.image(name) for name in ("one.png", "remaining.JPG", "third.webp")]
        self.window._set_sources(files)
        self.window.format_combo.setCurrentText("WEBP")
        folder = self.root / "not created" / "chosen batch folder"
        self.custom_output(folder)
        self.select_row(0)
        self.click_remove()
        self.assert_queue(files[1:])
        self.assertEqual(Path(self.window.output_edit.text()), folder)
        self.assertEqual(self.window.output_edit.placeholderText(), "Output folder")
        self.assertEqual(self.window.output_button.text(), "Choose folder…")
        self.assertFalse(self.window.default_output)
        self.select_row(1)
        self.click_remove()
        self.assert_queue([files[1]])
        self.assertEqual(Path(self.window.output_edit.text()), folder / "remaining_optimized.webp")
        self.assertEqual(self.window.output_edit.placeholderText(), "Output file")
        self.assertEqual(self.window.output_button.text(), "Choose…")
        self.assertFalse(self.window.default_output)
        self.assertFalse(folder.exists())

    def test_custom_batch_to_single_automatic_format_uses_remaining_image_suffix(self):
        files = [self.image("remove.png"), self.image("keep/original.JPEG")]
        self.window._set_sources(files)
        self.window.format_combo.setCurrentText("Automatic")
        folder = self.root / "chosen folder"
        self.custom_output(folder)
        self.select_row(0)
        QTest.keyClick(self.window.source_list, Qt.Key.Key_Delete)
        self.settle()
        self.assert_queue([files[1]])
        self.assertEqual(Path(self.window.output_edit.text()), folder / "original_optimized.jpeg")
        self.assertFalse(self.window.default_output)
        self.assertFalse(folder.exists())

    def test_removed_image_is_absent_from_processing_job_in_chosen_folder(self):
        removed, remaining = self.image("remove.png"), self.image("keep/remaining image.jpg")
        originals = {path: path.read_bytes() for path in (removed, remaining)}
        self.window._set_sources([removed, remaining])
        self.window.format_combo.setCurrentText("WEBP")
        folder = self.root / "chosen output folder"
        self.custom_output(folder)
        self.select_row(0)
        self.click_remove()
        destination = folder / "remaining image_optimized.webp"
        self.assertEqual(Path(self.window.output_edit.text()), destination)
        worker = Mock()
        worker.isRunning.return_value = False
        with patch("pixelkit.app.BatchWorker", return_value=worker) as constructor:
            self.window._start_processing()
        constructor.assert_called_once()
        jobs, output_dir, _target_bytes = constructor.call_args.args
        self.assertEqual(len(jobs), 1)
        command, output = jobs[0]
        self.assertEqual(command[1], str(remaining))
        self.assertNotIn(str(removed), command)
        self.assertEqual(output, destination)
        self.assertEqual(output.parent, folder)
        self.assertEqual(output_dir, folder)
        worker.start.assert_called_once()
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        self.assertFalse(destination.exists())

    def test_automatic_output_tracks_first_remaining_source_and_batch_to_single(self):
        files = [self.image(name) for name in ("one/first.png", "two/second.JPG", "three/last.webp")]
        self.window._set_sources(files)
        self.window.format_combo.setCurrentText("Automatic")
        self.assertEqual(Path(self.window.output_edit.text()), files[0].parent / "optimized")
        self.select_row(0)
        self.click_remove()
        self.assertEqual(Path(self.window.output_edit.text()), files[1].parent / "optimized")
        self.assertTrue(self.window.default_output)
        self.select_row(0)
        self.click_remove()
        self.assert_queue([files[2]])
        self.assertEqual(Path(self.window.output_edit.text()), files[2].with_name("last_optimized.webp"))
        self.assertTrue(self.window.default_output)
        self.window.format_combo.setCurrentText("JPG")
        self.assertEqual(Path(self.window.output_edit.text()), files[2].with_name("last_optimized.jpg"))

    def test_missing_other_queued_file_is_not_removed_during_selected_removal(self):
        files = [self.image(name) for name in ("missing.png", "remove.jpg", "keep.webp")]
        self.window._set_sources(files)
        files[0].unlink()
        self.select_row(1)
        self.click_remove()
        self.assert_queue([files[0], files[2]])
        self.assertIn("2 images", self.window.source_info.text())
        self.assertIn("unavailable", self.window.source_info.text())
        self.assertTrue(files[1].exists())
        self.assertTrue(files[2].exists())
        self.assertTrue(self.window.remove_button.isEnabled())

    def test_processing_and_running_worker_guard_button_action_and_handler(self):
        files = [self.image("first.png"), self.image("second.jpg")]
        self.window._set_sources(files)
        self.select_row(0)
        for busy in ("processing", "running worker"):
            with self.subTest(busy=busy):
                if busy == "processing":
                    self.window._set_processing_state(True)
                else:
                    worker = Mock()
                    worker.isRunning.return_value = True
                    self.window.worker = worker
                    self.window._update_action_state()
                self.assertFalse(self.window.remove_button.isEnabled())
                self.assertFalse(self.window.remove_source_action.isEnabled())
                before = self.queue_snapshot()
                self.click_remove()
                self.window.remove_source_action.trigger()
                self.window._remove_selected_source()
                QTest.keyClick(self.window.source_list, Qt.Key.Key_Delete)
                self.settle()
                self.assertEqual(self.queue_snapshot(), before)
                self.assert_queue(files)
                self.window.worker = None
                self.window._set_processing_state(False)
                self.assertTrue(self.window.remove_button.isEnabled())
                self.assertTrue(self.window.remove_source_action.isEnabled())

    def test_removal_controls_are_restored_when_processing_finishes(self):
        files = [self.image("first.png"), self.image("second.jpg")]
        self.window._set_sources(files)
        self.select_row(1)
        self.window._set_processing_state(True)
        report = BatchReport((), self.root)
        with patch("pixelkit.app.ReportDialog") as dialog:
            self.window._processing_finished(report)
            dialog.assert_called_once_with(report, self.window)
        for control in (self.window.source_list, self.window.remove_button, self.window.remove_source_action,
                        self.window.clear_button, self.window.process_button):
            self.assertTrue(control.isEnabled())
        self.assertEqual(self.window.source_list.currentRow(), 1)
        self.click_remove()
        self.assert_queue(files[:1])
        self.assertIs(self.window.last_report, report)

    def test_delete_and_backspace_in_edits_do_not_remove_selected_source(self):
        files = [self.image("first.png"), self.image("second.jpg")]
        self.window._set_sources(files)
        self.select_row(0)
        for name, edit, text, after_delete, after_backspace in (
            ("output", self.window.output_edit, "ABCDE", "ABDE", "ADE"),
            ("width", self.window.width_edit, "12345", "1245", "145"),
        ):
            with self.subTest(edit=name):
                edit.setFocus()
                edit.setText(text)
                edit.setCursorPosition(2)
                QTest.keyClick(edit, Qt.Key.Key_Delete)
                self.assertEqual(edit.text(), after_delete)
                self.assert_queue(files)
                QTest.keyClick(edit, Qt.Key.Key_Backspace)
                self.assertEqual(edit.text(), after_backspace)
                self.assert_queue(files)
                self.assertEqual(self.window.source_list.currentRow(), 0)
                self.assertTrue(self.window.source_list.item(0).isSelected())
                self.assertTrue(edit.hasFocus())

    def test_clear_all_still_clears_every_row_without_a_selection(self):
        files = [self.image("first.png"), self.image("second.jpg")]
        self.window._set_sources(files)
        self.assertEqual(self.window.source_list.selectedItems(), [])
        QTest.mouseClick(self.window.clear_button, Qt.MouseButton.LeftButton)
        self.settle()
        self.assert_queue([])
        self.assertFalse(self.window.remove_button.isEnabled())
        self.assertFalse(self.window.clear_button.isEnabled())
        self.assertTrue(all(path.exists() for path in files))

    def test_queue_controls_fit_minimum_and_default_sizes_with_long_selected_name(self):
        files = [self.image("a very long source filename " * 7 + ".png"), self.image("second.jpg")]
        self.window._set_sources(files)
        self.select_row(0)
        page = self.window.media_stack.widget(0)
        columns = page.findChildren(QScrollArea)
        queue_buttons = self.window.source_card.findChildren(QPushButton)
        self.assertIn(self.window.remove_button, queue_buttons)
        self.assertIn(self.window.clear_button, queue_buttons)
        for width, height in ((1040, 620), (1100, 780)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.settle()
                self.assertEqual((self.window.width(), self.window.height()), (width, height))
                for control in [*queue_buttons, self.window.source_list, self.window.process_button]:
                    self.assertEqual(control.visibleRegion().boundingRect(), control.rect(), control.objectName() or control.accessibleName())
                for column in columns:
                    self.assertEqual(column.horizontalScrollBar().maximum(), 0)
                    if height >= 780:
                        self.assertEqual(column.verticalScrollBar().maximum(), 0)
                self.assertEqual(self.window.source_list.horizontalScrollBar().maximum(), 0)
                self.assertTrue(self.window.source_list.item(0).isSelected())


if __name__ == "__main__":
    unittest.main()
