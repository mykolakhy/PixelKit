from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QMessageBox

from pixelkit.app import ImageMagickStudio, PixelKitApplication
from pixelkit.presets import PresetStore
from pixelkit.report import BatchReport, FileResult, ReportDialog


class VideoUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = PixelKitApplication.instance() or PixelKitApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        store = PresetStore(QSettings(str(self.root / "presets.ini"), QSettings.Format.IniFormat))
        message_patch = patch("pixelkit.app.ImageMagickStudio._show_message")
        self.message = message_patch.start()
        self.addCleanup(message_patch.stop)
        with patch("pixelkit.app.find_magick", return_value="magick"), patch("pixelkit.video_panel.find_ffmpeg", return_value="ffmpeg"), patch("pixelkit.video_panel.find_ffprobe", return_value="ffprobe"):
            self.window = ImageMagickStudio(store)
        self.panel = self.window.video_panel
        self.addCleanup(self.close_window)

    def close_window(self):
        for worker in (self.window.worker, self.panel.worker):
            if isinstance(worker, Mock):
                worker.isRunning.return_value = False
        self.panel.processing = False
        self.window.processing = False
        self.window.close()

    def video(self, name="input with spaces.mov"):
        source = self.root / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"video fixture")
        return source

    def mock_worker(self):
        worker = Mock()
        worker.isRunning.return_value = False
        return worker

    def test_video_queue_filters_unsupported_and_missing_files_and_deduplicates(self):
        first = self.video()
        second = self.video("SECOND.MP4")
        image = self.video("image.png")
        self.panel.set_sources([first, first, image, self.root / "missing.mp4", second])
        self.assertEqual(self.panel.sources, [first.resolve(), second.resolve()])
        self.assertEqual(self.panel.source_list.count(), 2)
        self.assertEqual(Path(self.panel.output_edit.text()), self.root / "optimized")
        self.panel.set_sources([first])
        self.assertEqual(Path(self.panel.output_edit.text()), first.with_name(first.stem + "_optimized.mp4"))

    def test_bad_tilde_output_does_not_break_appending_or_launch(self):
        first, second = self.video("first.mov"), self.video("second.mov")
        self.panel.set_sources([first])
        typed = "~pixelkit_nonexistent_qa_user/out.mp4"
        self.panel.output_edit.setText(typed)
        self.panel.add_sources([second])
        self.assertEqual(self.panel.sources, [first, second])
        self.assertEqual(self.panel.output_edit.text(), str(Path(typed).parent))
        with patch("pixelkit.video_panel.VideoWorker") as worker:
            self.panel.start_processing()
        worker.assert_not_called()
        self.assertEqual(self.message.call_args.args[1], "Check output location")
        self.assertFalse(self.panel.processing)

    def test_existing_folder_cannot_become_a_single_video_output_file(self):
        self.panel.set_sources([self.video()])
        folder = self.root / "chosen folder"
        folder.mkdir()
        self.panel.output_edit.setText(str(folder))
        with patch("pixelkit.video_panel.VideoWorker") as worker:
            self.panel.start_processing()
        worker.assert_not_called()
        self.assertEqual(self.message.call_args.args[1], "Check output location")
        self.assertTrue(folder.is_dir())

    def test_add_videos_and_drop_append_to_the_queue_in_order(self):
        first, second, third = (self.video(name) for name in ("first.mov", "second.mp4", "third.m4v"))
        self.panel.set_sources([first])
        with patch("pixelkit.video_panel.QFileDialog.getOpenFileNames", return_value=([str(first), str(second)], "")):
            self.panel.add_button.click()
        self.panel.source_list.files_dropped.emit([second, third, self.root / "missing.mp4"])
        self.assertEqual(self.panel.sources, [first, second, third])
        self.assertEqual(self.panel.source_list.count(), 3)
        self.assertEqual(Path(self.panel.output_edit.text()), self.root / "optimized")
        self.panel.clear_button.click()
        self.assertEqual(self.panel.sources, [])
        self.assertEqual(self.panel.output_edit.text(), "")

    def test_adding_a_folder_keeps_chosen_batch_output_and_last_report(self):
        first, second = self.video("first.mov"), self.video("second.mp4")
        third = self.video("incoming/third.mov")
        self.video("incoming/unsupported.png")
        self.panel.set_sources([first, second])
        output = self.root / "chosen output"
        self.panel.output_edit.setText(str(output))
        report = BatchReport((), self.root)
        self.panel.last_report = report
        self.panel.report_button.show()
        with patch("pixelkit.video_panel.QFileDialog.getExistingDirectory", return_value=str(third.parent)):
            self.panel.folder_button.click()
        self.assertEqual(self.panel.sources, [first, second, third])
        self.assertEqual(Path(self.panel.output_edit.text()), output)
        self.assertIs(self.panel.last_report, report)
        self.assertFalse(self.panel.report_button.isHidden())
        self.assertTrue(self.panel.report_button.isEnabled())

    def test_adding_a_second_video_uses_parent_of_a_chosen_output_file(self):
        first, second = self.video("first.mov"), self.video("second.mp4")
        self.panel.set_sources([first])
        chosen = self.root / "chosen output" / "share.mp4"
        self.panel.output_edit.setText(str(chosen))
        self.panel.add_sources([first])
        self.assertEqual(Path(self.panel.output_edit.text()), chosen)
        self.panel.add_sources([second])
        self.assertEqual(Path(self.panel.output_edit.text()), chosen.parent)
        worker = self.mock_worker()
        with patch("pixelkit.video_panel.VideoWorker", return_value=worker) as construct:
            self.panel.start_processing()
        self.assertEqual(construct.call_args.args[1], chosen.parent)
        self.assertTrue(all(output.parent == chosen.parent for _, output in construct.call_args.args[0]))
        self.assertFalse(chosen.exists())

    def test_append_is_ignored_while_processing(self):
        first, second = self.video("first.mov"), self.video("second.mp4")
        self.panel.set_sources([first])
        output = self.panel.output_edit.text()
        self.panel._set_busy(True)
        self.panel.source_list.files_dropped.emit([second])
        self.assertEqual(self.panel.sources, [first])
        self.assertEqual(self.panel.output_edit.text(), output)

    def test_video_processing_passes_preset_resolution_and_audio_for_each_file(self):
        files = [self.video("first.mov"), self.video("second.mp4")]
        self.panel.set_sources(files)
        output = self.root / "chosen video folder"
        self.panel.output_edit.setText(str(output))
        self.panel.preset_combo.setCurrentIndex(self.panel.preset_combo.findData("small"))
        self.panel.resolution_combo.setCurrentIndex(self.panel.resolution_combo.findData(720))
        self.panel.audio_combo.setCurrentIndex(self.panel.audio_combo.findData("remove"))
        worker = self.mock_worker()
        with patch("pixelkit.video_panel.VideoWorker", return_value=worker) as construct:
            self.panel.start_processing()
        jobs, output_dir, settings = construct.call_args.args[:3]
        self.assertEqual(jobs, [(file.resolve(), output / (file.stem + "_optimized.mp4")) for file in files])
        self.assertEqual(output_dir, output)
        self.assertEqual((settings.preset, settings.max_height, settings.audio), ("small", 720, "remove"))
        self.assertIsNone(settings.target_bytes)
        worker.start.assert_called_once()
        self.assertTrue(self.panel.processing)

    def test_encoding_progress_keeps_batch_position_and_cancellation_message(self):
        files = [self.video("one/clip.mov"), self.video("two/clip.mov"), self.video("last.mp4")]
        self.panel.set_sources(files)
        worker = self.mock_worker()
        with patch("pixelkit.video_panel.VideoWorker", return_value=worker):
            self.panel.start_processing()
        self.assertIn("File 1 of 3", self.panel.status.text())
        self.assertIn("Preparing", self.panel.status.text())
        self.panel._encoding_progress(0, "clip.mov")
        self.assertIn("File 1 of 3", self.panel.status.text())
        self.panel._file_progress(1, 3, "clip.mov")
        self.panel._encoding_progress(35, "clip.mov")
        self.assertIn("File 2 of 3", self.panel.status.text())
        self.assertIn("35%", self.panel.status.text())
        self.assertEqual(self.panel.progress.value(), 35)
        self.panel.cancel_processing()
        self.panel._file_progress(2, 3, "clip.mov")
        self.panel._encoding_progress(90, "clip.mov")
        self.assertEqual(self.panel.status.text(), "Cancelling… Completed videos will be kept.")

    def test_video_size_limit_defaults_off_and_toggling_preserves_entered_size(self):
        self.assertFalse(self.panel.target_size_check.isChecked())
        self.assertEqual(self.panel.target_size_edit.text(), "25")
        self.assertFalse(self.panel.target_size_edit.isEnabled())
        self.assertTrue(self.panel.target_size_hint.isHidden())
        self.panel.target_size_check.setChecked(True)
        self.assertTrue(self.panel.target_size_edit.isEnabled())
        self.assertFalse(self.panel.target_size_hint.isHidden())
        self.assertEqual(self.panel.preset_caption.text(), "Starting quality")
        self.panel.target_size_edit.setText("2.5")
        self.panel.target_size_check.setChecked(False)
        self.assertFalse(self.panel.target_size_edit.isEnabled())
        self.assertTrue(self.panel.target_size_hint.isHidden())
        self.assertEqual(self.panel.preset_caption.text(), "Quality")
        self.panel._set_busy(True)
        self.panel._set_busy(False)
        self.assertFalse(self.panel.target_size_edit.isEnabled())
        self.panel.target_size_check.setChecked(True)
        self.assertEqual(self.panel.target_size_edit.text(), "2.5")

    def test_video_batch_passes_exact_decimal_mb_limit_and_retains_resolution_and_audio(self):
        files = [self.video("first.mov"), self.video("second.mp4")]
        self.panel.set_sources(files)
        self.panel.target_size_check.setChecked(True)
        self.panel.target_size_edit.setText("1.001")
        self.panel.resolution_combo.setCurrentIndex(self.panel.resolution_combo.findData(1080))
        self.panel.audio_combo.setCurrentIndex(self.panel.audio_combo.findData("keep"))
        worker = self.mock_worker()
        with patch("pixelkit.video_panel.VideoWorker", return_value=worker) as construct:
            self.panel.start_processing()
        jobs, _, settings = construct.call_args.args[:3]
        self.assertEqual(len(jobs), 2)
        self.assertEqual(settings.target_bytes, 1_001_000)
        self.assertEqual((settings.max_height, settings.audio), (1080, "keep"))
        worker.start.assert_called_once()

    def test_invalid_video_size_limit_does_not_start_processing_or_create_output_folder(self):
        self.panel.set_sources([self.video("first.mov"), self.video("second.mp4")])
        folder = self.root / "new output folder"
        self.panel.output_edit.setText(str(folder))
        self.panel.target_size_check.setChecked(True)
        for value in ("", "0", "-1", "1,5", "0.0001", "1000001", "nan"):
            with self.subTest(value=value), patch("pixelkit.video_panel.VideoWorker") as worker:
                self.panel.target_size_edit.setText(value)
                self.panel.start_processing()
                worker.assert_not_called()
                self.assertFalse(self.panel.processing)
                self.assertFalse(folder.exists())
                self.assertEqual(self.message.call_args.args[1], "Check file size")

    def test_disabling_video_size_limit_ignores_an_unfinished_entry(self):
        self.panel.set_sources([self.video()])
        self.panel.target_size_check.setChecked(True)
        self.panel.target_size_edit.clear()
        self.panel.target_size_check.setChecked(False)
        worker = self.mock_worker()
        with patch("pixelkit.video_panel.VideoWorker", return_value=worker) as construct:
            self.panel.start_processing()
        self.assertIsNone(construct.call_args.args[2].target_bytes)
        worker.start.assert_called_once()
        self.message.assert_not_called()

    def test_video_cannot_overwrite_its_original(self):
        source = self.video("original.mp4")
        self.panel.set_sources([source])
        self.panel.output_edit.setText(str(source))
        with patch("pixelkit.video_panel.VideoWorker") as worker:
            self.panel.start_processing()
        worker.assert_not_called()
        self.assertIn("different from the original", self.message.call_args.args[2])
        self.assertFalse(self.panel.processing)

    def test_video_batch_assigns_distinct_output_names_and_preserves_existing_files(self):
        first = self.video("one/clip.mov")
        second = self.video("two/clip.mp4")
        self.panel.set_sources([first, second])
        output = self.root / "output"
        output.mkdir()
        existing = output / "clip_optimized.mp4"
        existing.write_bytes(b"existing output")
        self.panel.output_edit.setText(str(output))
        with patch("pixelkit.video_panel.VideoWorker", return_value=self.mock_worker()) as worker:
            self.panel.start_processing()
        jobs = worker.call_args.args[0]
        self.assertEqual([target.name for _, target in jobs], ["clip_optimized_2.mp4", "clip_optimized_3.mp4"])
        self.assertEqual(existing.read_bytes(), b"existing output")
        self.message.assert_not_called()

    def test_video_batch_outputs_are_unique_on_case_insensitive_filesystems(self):
        first = self.video("one/CLIP.mov")
        second = self.video("two/clip.mp4")
        self.panel.set_sources([first, second])
        self.panel.output_edit.setText(str(self.root / "output"))
        with patch("pixelkit.video_panel.VideoWorker", return_value=self.mock_worker()) as worker:
            self.panel.start_processing()
        outputs = [str(target).casefold() for _, target in worker.call_args.args[0]]
        self.assertEqual(len(set(outputs)), 2)

    def test_finder_open_selects_video_mode_and_mixed_files_preserve_queues(self):
        source = self.video()
        image = self.video("image.png")
        self.window.open_files([source])
        self.assertEqual(self.window.media_stack.currentIndex(), 1)
        self.assertEqual(self.panel.sources, [source.resolve()])
        self.window.open_files([image, source])
        self.message.assert_called_once()
        self.assertEqual(self.window.media_stack.currentIndex(), 1)
        self.assertEqual(self.panel.sources, [source.resolve()])
        self.assertEqual(self.window.sources, [])
        self.window.open_files([image])
        self.assertEqual(self.window.media_stack.currentIndex(), 0)
        self.assertEqual(self.window.sources, [image.resolve()])
        self.assertEqual(self.panel.sources, [source.resolve()])

    def test_video_report_does_not_offer_image_comparison(self):
        source = self.video()
        output = self.video("processed.mp4")
        file = FileResult(source, output, 100, 40, media_type="video", elapsed_seconds=1.5)
        dialog = ReportDialog(BatchReport((file,), self.root))
        self.addCleanup(dialog.close)
        dialog.table.selectRow(0)
        self.assertTrue(dialog.compare_button.isHidden())
        self.assertFalse(dialog.compare_button.isEnabled())
        with patch("pixelkit.report.ComparisonDialog") as comparison:
            dialog._compare_images()
        comparison.assert_not_called()
        self.assertEqual(dialog.table.item(0, 6).text(), "1.5 s")

    def test_video_cancel_locks_controls_until_finished_and_restores_report(self):
        source = self.video()
        self.panel.set_sources([source])
        self.panel.target_size_check.setChecked(True)
        self.panel.target_size_edit.setText("2.5")
        worker = self.mock_worker()
        with patch("pixelkit.video_panel.VideoWorker", return_value=worker):
            self.panel.start_processing()
        self.assertTrue(all(not button.isEnabled() for button in self.window.mode_buttons))
        for widget in (self.panel.source_list, self.panel.preset_combo, self.panel.resolution_combo, self.panel.audio_combo, self.panel.target_size_check, self.panel.target_size_edit, self.panel.output_edit):
            self.assertFalse(widget.isEnabled())
        self.panel.cancel_button.click()
        worker.cancel.assert_called_once()
        self.assertFalse(self.panel.cancel_button.isEnabled())
        self.assertTrue(self.panel.processing)
        report = BatchReport((FileResult(source, self.root / "out.mp4", 100, None, stopped="Cancelled", media_type="video"),), self.root, cancelled=True)
        with patch("pixelkit.video_panel.ReportDialog") as dialog:
            self.panel._finished(report)
        dialog.assert_called_once_with(report, self.panel)
        dialog.return_value.exec.assert_called_once()
        self.assertFalse(self.panel.processing)
        self.assertTrue(self.panel.cancel_button.isHidden())
        self.assertTrue(self.panel.process_button.isEnabled())
        self.assertTrue(self.panel.report_button.isEnabled())
        self.assertTrue(self.panel.target_size_check.isEnabled())
        self.assertTrue(self.panel.target_size_check.isChecked())
        self.assertTrue(self.panel.target_size_edit.isEnabled())
        self.assertEqual(self.panel.target_size_edit.text(), "2.5")
        self.assertIs(self.panel.last_report, report)
        self.assertTrue(all(button.isEnabled() for button in self.window.mode_buttons))
        self.assertIn("Cancelled", self.panel.status.text())

    def test_video_completion_surfaces_all_failed_and_mixed_results(self):
        good = FileResult(self.root / "good.mov", self.root / "good.mp4", 100, 40, media_type="video")
        bad = FileResult(self.root / "bad.mov", self.root / "bad.mp4", 100, None, "Could not compress this video", media_type="video")
        cases = (
            ((bad,), "Failed: 0 / 1 videos saved · 1 failed"),
            ((good, bad), "Completed: 1 / 2 videos saved · 1 failed"),
        )
        for files, expected in cases:
            with self.subTest(expected=expected), patch("pixelkit.video_panel.ReportDialog") as dialog:
                report = BatchReport(files, self.root)
                self.panel._finished(report)
                self.assertEqual(self.panel.status.text(), expected)
                self.assertIs(self.panel.last_report, report)
                dialog.assert_called_once_with(report, self.panel)

    def test_finder_cannot_replace_video_batch_and_close_is_blocked(self):
        source = self.video("first.mov")
        other = self.video("second.mp4")
        self.window.open_files([source])
        self.panel._set_busy(True)
        self.window.open_files([other])
        self.assertEqual(self.panel.sources, [source.resolve()])
        self.message.assert_called_once()
        self.message.reset_mock()
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.message.assert_called_once()
        self.panel._set_busy(False)

    def test_image_processing_locks_media_switch_and_blocks_finder_video_open(self):
        image = self.video("image.png")
        source = self.video()
        self.window._set_sources([image])
        self.window._set_processing_state(True)
        self.assertTrue(all(not button.isEnabled() for button in self.window.mode_buttons))
        self.window.mode_buttons[1].click()
        self.assertEqual(self.window.media_stack.currentIndex(), 0)
        self.window.open_files([source])
        self.assertEqual(self.panel.sources, [])
        self.assertEqual(self.window.sources, [image.resolve()])
        self.message.assert_called_once()
        self.window._set_processing_state(False)
        self.assertTrue(all(button.isEnabled() for button in self.window.mode_buttons))

    def test_open_video_picker_cancel_keeps_queue_and_selected_files_extend_it(self):
        source = self.video()
        other = self.video("second.mp4")
        self.panel.set_sources([source])
        with patch("pixelkit.video_panel.QFileDialog.getOpenFileNames", return_value=([], "")):
            self.panel.choose_many()
        self.assertEqual(self.panel.sources, [source.resolve()])
        with patch("pixelkit.video_panel.QFileDialog.getOpenFileNames", return_value=([str(other)], "")):
            self.panel.choose_many()
        self.assertEqual(self.panel.sources, [source.resolve(), other.resolve()])

    def test_existing_single_output_requires_confirmation(self):
        source = self.video()
        output = self.video("chosen.mp4")
        self.panel.set_sources([source])
        self.panel.output_edit.setText(str(output))
        self.message.return_value = QMessageBox.StandardButton.No
        with patch("pixelkit.video_panel.VideoWorker", return_value=self.mock_worker()) as worker:
            self.panel.start_processing()
            worker.assert_not_called()
            self.message.return_value = QMessageBox.StandardButton.Yes
            self.panel.start_processing()
            worker.assert_called_once()
        self.assertEqual(output.read_bytes(), b"video fixture")

    def test_missing_video_engine_disables_processing_and_does_not_start_worker(self):
        source = self.video()
        self.panel.ffmpeg = None
        self.panel.set_sources([source])
        self.assertFalse(self.panel.process_button.isEnabled())
        self.assertIn("FFmpeg", self.panel.status.text())
        with patch("pixelkit.video_panel.VideoWorker") as worker:
            self.panel.start_processing()
        worker.assert_not_called()
        self.assertFalse(self.panel.processing)

    def test_cancelled_encoding_progress_does_not_replace_cancelling_status(self):
        self.panel.set_sources([self.video()])
        self.panel.worker = self.mock_worker()
        self.panel._set_busy(True)
        self.panel.cancel_processing()
        status = self.panel.status.text()
        self.panel._encoding_progress(60, "late progress.mov")
        self.panel._file_progress(1, 2, "late progress.mov")
        self.assertEqual(self.panel.status.text(), status)


if __name__ == "__main__":
    unittest.main()
