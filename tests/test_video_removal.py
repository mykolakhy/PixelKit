from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QAbstractItemView, QScrollArea

from pixelkit.app import ImageMagickStudio, PixelKitApplication
from pixelkit.presets import PresetStore
from pixelkit.report import BatchReport
from pixelkit.video import VideoSettings


class VideoRemovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = PixelKitApplication.instance() or PixelKitApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='pixelkit-video-removal-', dir='/private/tmp')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.settings_path = self.root / 'preferences.ini'
        settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        settings.setValue('onboarding/dismissed', True)
        message_patch = patch('pixelkit.app.ImageMagickStudio._show_message')
        self.message = message_patch.start()
        self.addCleanup(message_patch.stop)
        with patch('pixelkit.app.find_magick', return_value='magick'), patch(
            'pixelkit.video_panel.find_ffmpeg', return_value='ffmpeg'
        ), patch('pixelkit.video_panel.find_ffprobe', return_value='ffprobe'):
            self.window = ImageMagickStudio(PresetStore(settings))
        self.panel = self.window.video_panel
        self.addCleanup(self.close_window)
        self.window.media_stack.setCurrentIndex(1)
        self.window.show()
        self.window.activateWindow()
        self.settle()

    def close_window(self):
        self.window.worker = self.panel.worker = None
        self.window.processing = False
        self.panel._set_busy(False)
        self.window.close()
        self.settle()

    def settle(self):
        for _ in range(4):
            self.app.processEvents()

    def video(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f'Original video contents: {name}'.encode())
        return path

    def assert_queue(self, paths):
        self.assertEqual(self.panel.sources, paths)
        self.assertEqual(self.panel.source_list.count(), len(paths))
        self.assertEqual(
            [self.panel.source_list.item(row).toolTip() for row in range(len(paths))],
            [str(path) for path in paths],
        )
        self.assertEqual(
            [self.panel.source_list.item(row).text() for row in range(len(paths))],
            [path.name for path in paths],
        )

    def select_row(self, row):
        listing = self.panel.source_list
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
        QTest.mouseClick(self.panel.remove_button, Qt.MouseButton.LeftButton)
        self.settle()

    def queue_snapshot(self):
        return (
            list(self.panel.sources),
            [self.panel.source_list.item(row).toolTip() for row in range(self.panel.source_list.count())],
            self.panel.source_list.currentRow(),
            [self.panel.source_list.row(item) for item in self.panel.source_list.selectedItems()],
            self.panel.output_edit.text(), self.panel.source_info.text(), self.panel.status.text(),
            self.panel._current_settings(), self.panel.last_report,
        )

    def test_controls_offer_single_selection_and_local_delete_shortcuts(self):
        self.assertEqual(self.panel.remove_button.text(), 'Remove')
        self.assertEqual(self.panel.clear_button.text(), 'Clear all')
        self.assertEqual(self.panel.remove_button.accessibleName(), 'Remove selected input video')
        self.assertEqual(self.panel.clear_button.accessibleName(), 'Clear all input videos')
        self.assertIn('Select a video', self.panel.remove_button.toolTip())
        self.assertIn('Original files stay on disk', self.panel.clear_button.toolTip())
        self.assertEqual(
            self.panel.source_list.selectionMode(), QAbstractItemView.SelectionMode.SingleSelection
        )
        action = self.panel.remove_source_action
        self.assertIn(action, self.panel.source_list.actions())
        self.assertEqual(action.shortcutContext(), Qt.ShortcutContext.WidgetShortcut)
        self.assertCountEqual(
            action.shortcuts(), [QKeySequence(Qt.Key.Key_Delete), QKeySequence(Qt.Key.Key_Backspace)]
        )
        self.assertFalse(self.panel.remove_button.isEnabled())
        self.assertFalse(action.isEnabled())
        before = self.queue_snapshot()
        self.panel._remove_selected_source()
        self.assertEqual(self.queue_snapshot(), before)

    def test_initial_add_has_no_selected_row_and_cannot_remove(self):
        files = [self.video('first.mov'), self.video('second.mp4')]
        self.panel.add_sources(files)
        self.settle()
        self.assertEqual(self.panel.source_list.selectedItems(), [])
        self.assertFalse(self.panel.remove_button.isEnabled())
        self.assertFalse(self.panel.remove_source_action.isEnabled())
        before = self.queue_snapshot()
        self.click_remove()
        self.panel._remove_selected_source()
        self.assertEqual(self.queue_snapshot(), before)
        self.assert_queue(files)
        self.assertTrue(self.panel.clear_button.isEnabled())

    def test_current_but_unselected_row_is_a_noop(self):
        files = [self.video(name) for name in ('one.mov', 'two.mp4', 'three.m4v')]
        self.panel.set_sources(files)
        self.select_row(1)
        self.panel.source_list.clearSelection()
        self.assertEqual(self.panel.source_list.currentRow(), 1)
        self.assertFalse(self.panel.remove_button.isEnabled())
        self.assertFalse(self.panel.remove_source_action.isEnabled())
        before = self.queue_snapshot()
        self.panel._remove_selected_source()
        for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            QTest.keyClick(self.panel.source_list, key)
            self.settle()
            self.assertEqual(self.queue_snapshot(), before)
        self.assert_queue(files)

    def test_button_removes_exact_selected_path_and_preserves_original_files(self):
        files = [self.video(name) for name in ('first/clip.mov', 'second/clip.mov', 'third/end.mp4')]
        originals = {path: path.read_bytes() for path in files}
        self.panel.set_sources(files)
        self.select_row(1)
        self.assertTrue(self.panel.remove_button.isEnabled())
        self.assertTrue(self.panel.remove_source_action.isEnabled())
        self.assertIn('Delete / Backspace', self.panel.remove_button.toolTip())
        self.click_remove()
        self.assert_queue([files[0], files[2]])
        self.assertEqual(self.panel.source_list.currentRow(), 1)
        self.assertTrue(self.panel.source_list.item(1).isSelected())
        self.assertTrue(self.panel.source_list.hasFocus())
        self.assertIn('2 video(s)', self.panel.source_info.text())
        self.assertEqual({path: path.read_bytes() for path in files}, originals)

    def test_delete_and_backspace_keep_next_then_preceding_row_selected(self):
        files = [self.video(f'video{index}.mov') for index in range(4)]
        self.panel.set_sources(files)
        self.select_row(1)
        QTest.keyClick(self.panel.source_list, Qt.Key.Key_Delete)
        self.settle()
        self.assert_queue([files[0], files[2], files[3]])
        self.assertEqual(self.panel.source_list.currentRow(), 1)
        self.assertTrue(self.panel.source_list.item(1).isSelected())
        self.assertTrue(self.panel.source_list.hasFocus())
        QTest.keyClick(self.panel.source_list, Qt.Key.Key_End)
        self.assertEqual(self.panel.source_list.currentRow(), 2)
        QTest.keyClick(self.panel.source_list, Qt.Key.Key_Backspace)
        self.settle()
        self.assert_queue([files[0], files[2]])
        self.assertEqual(self.panel.source_list.currentRow(), 1)
        self.assertTrue(self.panel.source_list.item(1).isSelected())
        self.assertTrue(self.panel.source_list.hasFocus())

    def test_removing_last_video_restores_empty_queue_and_clears_custom_destination(self):
        source = self.video('only.mov')
        original = source.read_bytes()
        self.panel.set_sources([source])
        self.panel.output_edit.setText(str(self.root / 'chosen output.mp4'))
        self.select_row(0)
        self.click_remove()
        self.assert_queue([])
        self.assertTrue(self.panel.source_list.placeholder.isVisible())
        self.assertEqual(self.panel.source_info.text(), 'Supports MP4, MOV and M4V')
        self.assertEqual(self.panel.output_edit.text(), '')
        self.assertEqual(self.panel.output_edit.placeholderText(), 'Output file')
        self.assertEqual(self.panel.output_button.text(), 'Choose…')
        self.assertEqual(self.panel.status.text(), 'Add videos to get started')
        for control in (self.panel.remove_button, self.panel.remove_source_action,
                        self.panel.clear_button, self.panel.process_button):
            self.assertFalse(control.isEnabled())
        self.assertEqual(self.panel.source_list.selectedItems(), [])
        self.assertTrue(self.panel.source_list.hasFocus())
        before = self.queue_snapshot()
        QTest.keyClick(self.panel.source_list, Qt.Key.Key_Delete)
        self.panel._remove_selected_source()
        self.assertEqual(self.queue_snapshot(), before)
        self.assertEqual(source.read_bytes(), original)
        self.panel.add_sources([source])
        self.assertTrue(self.panel.process_button.isEnabled())
        self.assertEqual(Path(self.panel.output_edit.text()), source.with_name('only_optimized.mp4'))
        self.assertEqual(self.panel.source_list.selectedItems(), [])

    def test_custom_batch_folder_is_kept_and_becomes_remaining_video_output(self):
        files = [self.video(name) for name in ('one.mov', 'keep/remaining.M4V', 'third.mp4')]
        self.panel.set_sources(files)
        folder = self.root / 'not created' / 'chosen batch folder'
        self.panel.output_edit.setText(str(folder))
        self.select_row(0)
        self.click_remove()
        self.assert_queue(files[1:])
        self.assertEqual(Path(self.panel.output_edit.text()), folder)
        self.assertEqual(self.panel.output_edit.placeholderText(), 'Output folder')
        self.assertEqual(self.panel.output_button.text(), 'Choose folder…')
        self.select_row(1)
        self.click_remove()
        self.assert_queue([files[1]])
        self.assertEqual(Path(self.panel.output_edit.text()), folder / 'remaining_optimized.mp4')
        self.assertEqual(self.panel.output_edit.placeholderText(), 'Output file')
        self.assertEqual(self.panel.output_button.text(), 'Choose…')
        self.assertTrue(self.panel.process_button.isEnabled())
        self.assertFalse(folder.exists())

    def test_removed_video_is_absent_from_processing_job_in_chosen_folder(self):
        removed, remaining = self.video('remove.mov'), self.video('keep/remaining video.mp4')
        originals = {path: path.read_bytes() for path in (removed, remaining)}
        settings = VideoSettings('small', 720, 'remove', 1_001_000)
        self.panel._apply_settings(settings)
        self.panel.set_sources([removed, remaining])
        folder = self.root / 'chosen output folder'
        self.panel.output_edit.setText(str(folder))
        self.select_row(0)
        self.click_remove()
        destination = folder / 'remaining video_optimized.mp4'
        worker = Mock()
        worker.isRunning.return_value = False
        with patch('pixelkit.video_panel.VideoWorker', return_value=worker) as constructor:
            self.panel.start_processing()
        constructor.assert_called_once()
        jobs, output_dir, actual_settings = constructor.call_args.args
        self.assertEqual(jobs, [(remaining, destination)])
        self.assertEqual(output_dir, folder)
        self.assertEqual(actual_settings, settings)
        worker.start.assert_called_once()
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        self.assertFalse(destination.exists())

    def test_automatic_output_tracks_first_remaining_source_and_batch_to_single(self):
        files = [self.video(name) for name in ('one/first.mov', 'two/second.MP4', 'three/last.m4v')]
        self.panel.set_sources(files)
        self.assertEqual(Path(self.panel.output_edit.text()), files[0].parent / 'optimized')
        self.select_row(0)
        self.click_remove()
        self.assertEqual(Path(self.panel.output_edit.text()), files[1].parent / 'optimized')
        self.select_row(0)
        self.click_remove()
        self.assert_queue([files[2]])
        self.assertEqual(Path(self.panel.output_edit.text()), files[2].with_name('last_optimized.mp4'))

    def test_missing_other_queued_file_is_not_removed_during_selected_removal(self):
        files = [self.video(name) for name in ('missing.mov', 'remove.mp4', 'keep.m4v', 'also missing.mp4')]
        self.panel.set_sources(files)
        files[0].unlink()
        files[3].unlink()
        self.select_row(1)
        self.click_remove()
        self.assert_queue([files[0], files[2], files[3]])
        self.assertIn('3 video(s)', self.panel.source_info.text())
        self.assertIn('2 original(s) unavailable', self.panel.source_info.text())
        self.assertTrue(files[1].exists())
        self.assertTrue(files[2].exists())
        self.assertTrue(self.panel.remove_button.isEnabled())

    def test_busy_panel_and_parent_guard_button_action_and_direct_handler(self):
        files = [self.video('first.mov'), self.video('second.mp4')]
        self.panel.set_sources(files)
        self.select_row(0)
        for busy in ('video processing', 'video worker', 'image processing', 'image worker'):
            with self.subTest(busy=busy):
                if busy == 'video processing':
                    self.panel._set_busy(True)
                elif busy == 'image processing':
                    self.window._set_processing_state(True)
                else:
                    worker = Mock()
                    worker.isRunning.return_value = True
                    if busy == 'video worker':
                        self.panel.worker = worker
                    else:
                        self.window.worker = worker
                self.panel._update_state()
                self.assertFalse(self.panel.remove_button.isEnabled())
                self.assertFalse(self.panel.remove_source_action.isEnabled())
                self.assertFalse(self.panel.clear_button.isEnabled())
                before = self.queue_snapshot()
                self.click_remove()
                self.panel.remove_source_action.trigger()
                self.panel._remove_selected_source()
                self.panel._clear_sources()
                QTest.keyClick(self.panel.source_list, Qt.Key.Key_Delete)
                self.settle()
                self.assertEqual(self.queue_snapshot(), before)
                self.assert_queue(files)
                self.window.worker = self.panel.worker = None
                self.window._set_processing_state(False)
                self.panel._set_busy(False)
                self.assertTrue(self.panel.remove_button.isEnabled())
                self.assertTrue(self.panel.remove_source_action.isEnabled())

    def test_removal_controls_are_restored_when_processing_finishes(self):
        files = [self.video('first.mov'), self.video('second.mp4')]
        self.panel.set_sources(files)
        self.select_row(1)
        self.panel._set_busy(True)
        report = BatchReport((), self.root)
        with patch.object(self.panel, '_show_report') as dialog:
            self.panel._finished(report)
            dialog.assert_called_once_with(report)
        for control in (self.panel.source_list, self.panel.remove_button, self.panel.remove_source_action,
                        self.panel.clear_button, self.panel.process_button):
            self.assertTrue(control.isEnabled())
        self.assertEqual(self.panel.source_list.currentRow(), 1)
        self.click_remove()
        self.assert_queue(files[:1])
        self.assertIs(self.panel.last_report, report)

    def test_delete_and_backspace_in_video_edits_and_remove_button_do_not_remove(self):
        files = [self.video('first.mov'), self.video('second.mp4')]
        self.panel.set_sources(files)
        self.select_row(0)
        self.panel.target_size_check.setChecked(True)
        for name, edit, text, after_delete, after_backspace in (
            ('output', self.panel.output_edit, 'ABCDE', 'ABDE', 'ADE'),
            ('size limit', self.panel.target_size_edit, '12345', '1245', '145'),
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
                self.assertTrue(edit.hasFocus())
        self.panel.remove_button.setFocus()
        for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            QTest.keyClick(self.panel.remove_button, key)
            self.assert_queue(files)

    def test_image_list_and_image_edits_cannot_trigger_video_removal(self):
        videos = [self.video('first.mov'), self.video('second.mp4')]
        self.panel.set_sources(videos)
        self.select_row(0)
        images = [self.video('first.png'), self.video('second.jpg')]
        self.window._set_sources(images)
        self.window.media_stack.setCurrentIndex(0)
        self.settle()
        self.window.source_list.setCurrentRow(0)
        self.window.source_list.setFocus()
        QTest.keyClick(self.window.source_list, Qt.Key.Key_Delete)
        self.settle()
        self.assertEqual(self.window.sources, images[1:])
        self.assert_queue(videos)
        for edit in (self.window.output_edit, self.window.width_edit):
            edit.setFocus()
            edit.setText('12345')
            edit.setCursorPosition(2)
            QTest.keyClick(edit, Qt.Key.Key_Delete)
            QTest.keyClick(edit, Qt.Key.Key_Backspace)
            self.assertEqual(edit.text(), '145')
            self.assertEqual(self.window.sources, images[1:])
            self.assert_queue(videos)

    def test_removal_keeps_settings_saved_preset_last_report_and_preferences(self):
        files = [self.video(name) for name in ('first.mov', 'second.mp4', 'third.m4v')]
        settings = VideoSettings('high', 1080, 'keep', 2_501_000)
        self.panel.video_preset_store.save({'For sharing': settings})
        self.panel.custom_presets = {'For sharing': settings}
        self.panel._refresh_presets('For sharing')
        self.panel._apply_selected_preset(self.panel.saved_preset_combo.currentIndex())
        self.panel.set_sources(files)
        report = BatchReport((), self.root, retry_settings=settings)
        self.panel.last_report = report
        self.panel.report_button.show()
        self.panel._update_state()
        original_preferences = self.settings_path.read_bytes()
        self.select_row(1)
        self.click_remove()
        self.assert_queue([files[0], files[2]])
        self.assertEqual(self.panel._current_settings(), settings)
        self.assertEqual(self.panel.saved_preset_combo.currentData(), 'For sharing')
        self.assertEqual(self.panel.custom_presets, {'For sharing': settings})
        self.assertIs(self.panel.last_report, report)
        self.assertFalse(self.panel.report_button.isHidden())
        self.assertTrue(self.panel.report_button.isEnabled())
        self.assertEqual(self.settings_path.read_bytes(), original_preferences)
        self.assertFalse(list(self.root.glob('*.csv')))
        self.assertFalse(list(self.root.glob('*.txt')))
        self.message.assert_not_called()

    def test_clear_all_clears_without_selection_and_keeps_files_and_report(self):
        files = [self.video('first.mov'), self.video('second.mp4')]
        self.panel.set_sources(files)
        report = BatchReport((), self.root)
        self.panel.last_report = report
        self.panel.report_button.show()
        self.assertEqual(self.panel.source_list.selectedItems(), [])
        QTest.mouseClick(self.panel.clear_button, Qt.MouseButton.LeftButton)
        self.settle()
        self.assert_queue([])
        self.assertFalse(self.panel.remove_button.isEnabled())
        self.assertFalse(self.panel.clear_button.isEnabled())
        self.assertEqual(self.panel.output_edit.text(), '')
        self.assertIs(self.panel.last_report, report)
        self.assertTrue(self.panel.report_button.isEnabled())
        self.assertTrue(all(path.exists() for path in files))

    def test_queue_remains_removable_when_video_processing_is_unavailable(self):
        files = [self.video('first.mov'), self.video('second.mp4')]
        self.panel.ffmpeg = self.panel.ffprobe = None
        self.panel.set_sources(files)
        self.select_row(0)
        self.assertFalse(self.panel.process_button.isEnabled())
        self.assertTrue(self.panel.remove_button.isEnabled())
        self.click_remove()
        self.assert_queue(files[1:])
        self.assertIn('Video processing is unavailable', self.panel.status.text())
        self.assertFalse(self.panel.process_button.isEnabled())

    def test_queue_controls_fit_minimum_and_default_sizes_with_long_selected_name(self):
        files = [self.video('a very long source filename ' * 7 + '.mov'), self.video('second.mp4')]
        self.panel.set_sources(files)
        self.select_row(0)
        for width, height in ((1040, 620), (1100, 780)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.settle()
                self.assertEqual((self.window.width(), self.window.height()), (width, height))
                for control in (self.panel.add_button, self.panel.folder_button, self.panel.remove_button,
                                self.panel.clear_button, self.panel.source_list, self.panel.process_button):
                    self.assertEqual(control.visibleRegion().boundingRect(), control.rect(), control.accessibleName())
                for column in self.panel.findChildren(QScrollArea):
                    self.assertEqual(column.horizontalScrollBar().maximum(), 0)
                    if height >= 780:
                        self.assertEqual(column.verticalScrollBar().maximum(), 0)
                self.assertEqual(self.panel.source_list.horizontalScrollBar().maximum(), 0)
                self.assertTrue(self.panel.source_list.item(0).isSelected())


if __name__ == '__main__':
    unittest.main()
