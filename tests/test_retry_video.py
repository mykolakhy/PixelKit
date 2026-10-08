from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QMessageBox

from pixelkit.app import ImageMagickStudio, PixelKitApplication
from pixelkit.presets import PresetStore
from pixelkit.report import BatchReport, FileResult, ReportDialog
from pixelkit.video import VideoSettings, VideoWorker


class RetryVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = PixelKitApplication.instance() or PixelKitApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        store = PresetStore(QSettings(str(self.root / 'presets.ini'), QSettings.Format.IniFormat))
        message_patch = patch('pixelkit.app.ImageMagickStudio._show_message')
        self.message = message_patch.start()
        self.addCleanup(message_patch.stop)
        self.message.return_value = QMessageBox.StandardButton.Yes
        with patch('pixelkit.app.find_magick', return_value='magick'), patch('pixelkit.video_panel.find_ffmpeg', return_value='ffmpeg'), patch('pixelkit.video_panel.find_ffprobe', return_value='ffprobe'):
            self.window = ImageMagickStudio(store)
        self.panel = self.window.video_panel
        self.addCleanup(self.close_window)

    def close_window(self):
        for worker in (self.window.worker, self.panel.worker):
            if isinstance(worker, Mock):
                worker.isRunning.return_value = False
        self.panel.processing = self.window.processing = False
        self.window.close()

    def video(self, name='original.mov', contents=b'original video'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def failed(self, source, output=None, **kwargs):
        return FileResult(source, output or self.root / 'exports' / f'{source.stem}_optimized.mp4', 100, None, 'Could not compress this video', media_type='video', **kwargs)

    def report(self, *files, settings=None, output_dir=None):
        return BatchReport(tuple(files), output_dir or self.root / 'exports', retry_settings=settings or VideoSettings('small', 720, 'remove', 1_001_000))

    def state(self):
        return (list(self.panel.sources), self.panel.output_edit.text(), self.panel._current_settings(), self.panel.status.text(), self.panel.last_report)

    def configure(self, settings):
        self.panel._apply_settings(settings)

    def mock_worker(self, running=False):
        worker = Mock()
        worker.isRunning.return_value = running
        return worker

    def test_worker_report_restores_actual_run_snapshot_after_controls_are_edited(self):
        source = self.video()
        settings = VideoSettings('high', 1080, 'keep', 2_501_000)
        self.configure(settings)
        self.panel.set_sources([source])
        output = self.root / 'chosen.mp4'
        worker = VideoWorker([(source, output)], self.root, self.panel._current_settings())
        reports = []
        worker.finished.connect(reports.append)
        with patch('pixelkit.video.find_ffmpeg', return_value=None), patch('pixelkit.video.find_ffprobe', return_value=None):
            worker.run()
        report = reports[0]
        self.assertEqual(report.retry_settings, settings)
        self.assertIs(report.retry_settings, worker.settings)
        self.configure(VideoSettings('balanced', 0, 'compress'))
        self.panel.last_report = report
        self.panel.report_button.show()
        with patch('pixelkit.video_panel.VideoWorker') as construct:
            self.assertTrue(self.panel._retry_failed(report))
        self.assertEqual(self.panel._current_settings(), settings)
        self.assertEqual(self.panel.sources, [source])
        self.assertEqual(Path(self.panel.output_edit.text()), output)
        self.assertIs(self.panel.last_report, report)
        self.assertFalse(self.panel.report_button.isHidden())
        self.assertFalse(self.panel.processing)
        construct.assert_not_called()

    def test_mixed_report_queues_only_failed_originals_in_report_order(self):
        good, failed_b, cancelled, failed_a, skipped = [self.video(name) for name in ('good.mov', 'b.mov', 'cancelled.mov', 'a.mov', 'skipped.mov')]
        success = FileResult(good, self.root / 'done.mp4', 100, 30, media_type='video')
        stopped = FileResult(cancelled, self.root / 'cancelled.mp4', 100, None, 'Cancelled', 'Cancelled', media_type='video')
        not_started = FileResult(skipped, self.root / 'skipped.mp4', None, None, 'Skipped', 'Skipped', media_type='video')
        report = self.report(success, self.failed(failed_b), stopped, self.failed(failed_a), not_started)
        self.panel.set_sources([good, failed_b, cancelled, failed_a, skipped])
        self.panel.last_report = report
        dialog = Mock()
        with patch('pixelkit.video_panel.VideoWorker') as construct:
            self.assertTrue(self.panel._retry_failed(report, dialog))
        self.assertEqual(self.panel.sources, [failed_b, failed_a])
        self.assertEqual(self.panel.source_list.count(), 2)
        self.assertEqual(Path(self.panel.output_edit.text()), report.output_dir)
        self.assertIn('2 failed', self.panel.status.text())
        self.message.assert_not_called()
        dialog.accept.assert_called_once()
        construct.assert_not_called()
        self.assertIs(self.panel.last_report, report)

    def test_retry_restores_every_audio_choice_and_exact_target_and_resets_preset_selection(self):
        source = self.video()
        saved = VideoSettings('balanced', 0, 'compress')
        self.panel.custom_presets = {'Edited later': saved}
        self.panel._refresh_presets('Edited later')
        for audio, target in (('keep', None), ('compress', 1_001_000), ('remove', 25_000_000)):
            with self.subTest(audio=audio):
                expected = VideoSettings('high', 1080, audio, target)
                self.panel.saved_preset_combo.setCurrentIndex(self.panel.saved_preset_combo.findData('Edited later'))
                self.assertTrue(self.panel._retry_failed(self.report(self.failed(source), settings=expected)))
                self.assertEqual(self.panel._current_settings(), expected)
                self.assertEqual(self.panel.saved_preset_combo.currentIndex(), 0)
                self.assertTrue(self.panel.audio_combo.isEnabled())
                self.assertEqual(self.panel.target_size_edit.isEnabled(), target is not None)

    def test_single_failure_from_batch_preserves_reported_output_in_chosen_folder(self):
        good, bad = self.video('good.mov'), self.video('bad.mov')
        chosen = self.root / 'chosen folder' / 'bad_optimized_2.mp4'
        report = self.report(FileResult(good, chosen.parent / 'good.mp4', 100, 30, media_type='video'), self.failed(bad, chosen), output_dir=chosen.parent)
        self.assertTrue(self.panel._retry_failed(report))
        self.assertEqual(self.panel.sources, [bad])
        self.assertEqual(Path(self.panel.output_edit.text()), chosen)
        self.assertFalse(chosen.parent.exists())
        self.assertEqual(self.panel.output_edit.placeholderText(), 'Output file')

    def test_declining_queue_replacement_keeps_all_controls_and_report_open(self):
        queued, failed = self.video('queued.mov'), self.video('failed.mov')
        self.panel.set_sources([queued])
        self.panel.output_edit.setText(str(self.root / 'new choice.mp4'))
        self.configure(VideoSettings('balanced', 0, 'keep'))
        report = self.report(self.failed(failed))
        self.panel.last_report = report
        before = self.state()
        self.message.return_value = QMessageBox.StandardButton.No
        dialog = Mock()
        self.assertFalse(self.panel._retry_failed(report, dialog))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.message.call_args.args[1], 'Replace queued videos?')
        dialog.accept.assert_not_called()

    def test_same_queue_does_not_require_replacement_confirmation(self):
        source = self.video()
        self.panel.set_sources([source])
        self.assertTrue(self.panel._retry_failed(self.report(self.failed(source))))
        self.message.assert_not_called()

    def test_successful_retry_returns_to_video_mode_and_selects_first_original(self):
        source = self.video()
        self.window.media_stack.setCurrentIndex(0)
        self.window.mode_buttons[0].setChecked(True)
        with patch.object(self.panel.source_list, 'setFocus') as focus:
            self.assertTrue(self.panel._retry_failed(self.report(self.failed(source))))
        self.assertEqual(self.window.media_stack.currentIndex(), 1)
        self.assertTrue(self.window.mode_buttons[1].isChecked())
        self.assertFalse(self.window.mode_buttons[0].isChecked())
        self.assertEqual(self.panel.source_list.currentRow(), 0)
        focus.assert_called_once()

    def test_retry_with_no_containing_main_window_still_selects_first_original(self):
        source = self.video()
        self.panel.setParent(None)
        try:
            self.assertTrue(self.panel._retry_failed(self.report(self.failed(source))))
            self.assertEqual(self.panel.source_list.currentRow(), 0)
        finally:
            self.window.media_stack.addWidget(self.panel)

    def test_mixed_failed_media_report_is_rejected_without_changing_controls(self):
        source, image = self.video(), self.video('image.png')
        mixed = self.report(self.failed(source), FileResult(image, self.root / 'image.webp', 100, None, 'Failed'))
        before = self.state()
        dialog = Mock()
        self.assertFalse(self.panel._retry_failed(mixed, dialog))
        self.assertEqual(self.state(), before)
        self.message.assert_not_called()
        dialog.accept.assert_not_called()

    def test_image_and_video_busy_flags_and_running_workers_block_preparation(self):
        source = self.video()
        self.panel.set_sources([source])
        report = self.report(self.failed(source))
        before = self.state()
        for owner, attribute, value in ((self.window, 'processing', True), (self.panel, 'processing', True), (self.window, 'worker', self.mock_worker(True)), (self.panel, 'worker', self.mock_worker(True))):
            with self.subTest(owner=owner, attribute=attribute):
                previous = getattr(owner, attribute)
                setattr(owner, attribute, value)
                dialog = Mock()
                self.assertFalse(self.panel._retry_failed(report, dialog))
                self.assertEqual(self.state(), before)
                dialog.accept.assert_not_called()
                setattr(owner, attribute, previous)

    def test_busy_recheck_after_confirmation_prevents_queue_mutation(self):
        queued, failed = self.video('queued.mov'), self.video('failed.mov')
        self.panel.set_sources([queued])
        before = self.state()
        def confirm(*_args):
            self.window.processing = True
            return QMessageBox.StandardButton.Yes
        self.message.side_effect = confirm
        self.assertFalse(self.panel._retry_failed(self.report(self.failed(failed))))
        self.assertEqual(self.state(), before)

    def test_destination_alias_created_during_confirmation_preserves_ui_and_successful_output(self):
        queued, failed, good = self.video('queued.mov'), self.video('failed.mov'), self.video('good.mov')
        successful_output = self.video('done.mp4', b'keep earlier successful result')
        target = self.root / 'retry.mp4'
        success = FileResult(good, successful_output, 100, 30, media_type='video')
        report = self.report(success, self.failed(failed, target), output_dir=self.root)
        self.panel.set_sources([queued])
        self.panel.last_report = report
        before = self.state()
        def confirm(*args):
            if args[1] == 'Replace queued videos?':
                target.symlink_to(successful_output)
            return QMessageBox.StandardButton.Yes
        self.message.side_effect = confirm
        dialog = Mock()
        self.assertFalse(self.panel._retry_failed(report, dialog))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.message.call_args.args[1], 'Could not prepare retry')
        self.assertIn('earlier successful result', self.message.call_args.args[2])
        self.assertEqual(successful_output.read_bytes(), b'keep earlier successful result')
        dialog.accept.assert_not_called()

    def test_output_folder_becoming_file_during_confirmation_preserves_ui(self):
        queued, first, second = self.video('queued.mov'), self.video('first.mov'), self.video('second.mov')
        folder = self.root / 'chosen exports'
        report = self.report(self.failed(first), self.failed(second), output_dir=folder)
        self.panel.set_sources([queued])
        self.panel.last_report = report
        before = self.state()
        def confirm(*args):
            if args[1] == 'Replace queued videos?':
                folder.write_bytes(b'folder now occupied by a file')
            return QMessageBox.StandardButton.Yes
        self.message.side_effect = confirm
        dialog = Mock()
        self.assertFalse(self.panel._retry_failed(report, dialog))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.message.call_args.args[1], 'Could not prepare retry')
        self.assertIn('occupied by a file', self.message.call_args.args[2])
        dialog.accept.assert_not_called()

    def test_missing_original_aborts_whole_preparation_and_names_missing_path(self):
        queued, existing = self.video('queued.mov'), self.video('existing.mov')
        missing = self.root / 'missing.mov'
        self.panel.set_sources([queued])
        for results in ((self.failed(missing),), (self.failed(existing), self.failed(missing))):
            with self.subTest(results=results):
                report = self.report(*results)
                self.panel.last_report = report
                before = self.state()
                dialog = Mock()
                self.assertFalse(self.panel._retry_failed(report, dialog))
                self.assertEqual(self.state(), before)
                self.assertIn(str(missing), self.message.call_args.args[2])
                self.assertEqual(self.message.call_args.args[1], 'Original videos not found')
                dialog.accept.assert_not_called()

    def test_original_disappearing_during_confirmation_is_reported_without_dropping_it(self):
        queued, failed = self.video('queued.mov'), self.video('failed.mov')
        self.panel.set_sources([queued])
        before = self.state()
        def confirm(*args):
            if args[1] == 'Replace queued videos?':
                failed.unlink()
            return QMessageBox.StandardButton.Yes
        self.message.side_effect = confirm
        self.assertFalse(self.panel._retry_failed(self.report(self.failed(failed))))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.message.call_args.args[1], 'Original videos not found')

    def test_queue_populate_keeps_validated_original_that_disappears_during_metadata_read(self):
        source = self.video()
        source.unlink()
        self.panel._replace_sources([source])
        self.assertEqual(self.panel.sources, [source])
        self.assertEqual(self.panel.source_list.count(), 1)
        self.assertIn('1 original(s) unavailable', self.panel.source_info.text())

    def test_retry_preparation_preserves_original_and_existing_output_and_overwrite_is_confirmed_at_start(self):
        source = self.video(contents=b'keep original')
        output = self.video('chosen.mp4', b'keep existing result')
        report = self.report(self.failed(source, output), output_dir=self.root)
        with patch('pixelkit.video_panel.VideoWorker') as construct:
            self.assertTrue(self.panel._retry_failed(report))
            construct.assert_not_called()
            self.message.assert_not_called()
            self.message.return_value = QMessageBox.StandardButton.No
            self.panel.start_processing()
            construct.assert_not_called()
        self.assertEqual(self.message.call_args.args[1], 'File already exists')
        self.assertEqual(source.read_bytes(), b'keep original')
        self.assertEqual(output.read_bytes(), b'keep existing result')
        self.assertFalse(self.panel.processing)

    def test_single_retry_rejects_original_and_success_output_aliases(self):
        source, good = self.video('bad.mp4'), self.video('good.mp4')
        successful_output = self.video('done.mp4', b'keep successful result')
        original_alias = self.root / 'original alias.mp4'
        original_alias.hardlink_to(good)
        result_alias = self.root / 'result alias.mp4'
        result_alias.symlink_to(successful_output)
        success = FileResult(good, successful_output, 100, 30, media_type='video')
        before = self.state()
        for unsafe in (source, good, original_alias, successful_output, result_alias):
            with self.subTest(unsafe=unsafe):
                report = self.report(success, self.failed(source, unsafe))
                self.assertFalse(self.panel._retry_failed(report))
                self.assertEqual(self.state(), before)
                self.assertIn('replace an original file or an earlier successful result', self.message.call_args.args[2])
        self.assertEqual(successful_output.read_bytes(), b'keep successful result')

    def test_batch_retry_keeps_successful_output_and_uses_collision_safe_names_at_start(self):
        good = self.video('good.mov')
        bad_a, bad_b = self.video('one/clip.mov'), self.video('two/clip.mov')
        folder = self.root / 'exports'
        folder.mkdir()
        successful_output = self.video('exports/clip_optimized.mp4', b'successful compressed video')
        report = self.report(FileResult(good, successful_output, 100, 30, media_type='video'), self.failed(bad_a), self.failed(bad_b))
        self.assertTrue(self.panel._retry_failed(report))
        self.assertEqual(list(folder.iterdir()), [successful_output])
        worker = self.mock_worker()
        with patch('pixelkit.video_panel.VideoWorker', return_value=worker) as construct:
            self.panel.start_processing()
        self.assertEqual([target.name for _, target in construct.call_args.args[0]], ['clip_optimized_2.mp4', 'clip_optimized_3.mp4'])
        self.assertEqual(successful_output.read_bytes(), b'successful compressed video')

    def test_invalid_output_location_keeps_current_queue_and_creates_nothing(self):
        queued, bad = self.video('queued.mov'), self.video('bad.mov')
        blocker = self.video('occupied')
        self.panel.set_sources([queued])
        before = self.state()
        report = self.report(self.failed(bad, blocker / 'result.mp4'), output_dir=blocker)
        self.assertFalse(self.panel._retry_failed(report))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.message.call_args.args[1], 'Could not prepare retry')
        self.assertEqual(blocker.read_bytes(), b'original video')

    def test_historical_report_without_snapshot_and_cancelled_only_report_do_nothing(self):
        source = self.video()
        cancelled = FileResult(source, self.root / 'out.mp4', 100, None, 'Cancelled', 'Cancelled', media_type='video')
        reports = (BatchReport((self.failed(source),), self.root), self.report(cancelled))
        before = self.state()
        for report in reports:
            self.assertFalse(self.panel._retry_failed(report))
            self.assertEqual(self.state(), before)
        self.message.assert_not_called()

    def test_report_signal_is_connected_before_exec_and_accepts_only_on_preparation(self):
        source = self.video()
        report = self.report(self.failed(source))
        dialog = Mock()
        def execute():
            callback = dialog.retry_requested.connect.call_args.args[0]
            callback(report)
        dialog.exec.side_effect = execute
        with patch('pixelkit.video_panel.ReportDialog', return_value=dialog):
            self.panel._show_report(report)
        dialog.set_retry_enabled.assert_called_once_with(True)
        dialog.accept.assert_called_once()
        dialog.deleteLater.assert_called_once()
        self.assertEqual(self.panel.sources, [source])

    def test_report_retry_button_is_disabled_while_image_worker_is_running(self):
        source = self.video()
        report = self.report(self.failed(source))
        self.window.worker = self.mock_worker(True)
        dialog = ReportDialog(report, self.panel)
        self.addCleanup(dialog.close)
        with patch.object(dialog, 'exec'), patch('pixelkit.video_panel.ReportDialog', return_value=dialog):
            self.panel._show_report(report)
        self.assertFalse(dialog.retry_button.isEnabled())


if __name__ == '__main__':
    unittest.main()
