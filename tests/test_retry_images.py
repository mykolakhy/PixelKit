from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QMessageBox

from pixelkit.app import BatchWorker, ImageMagickStudio, PixelKitApplication
from pixelkit.presets import Preset, PresetStore
from pixelkit.report import BatchReport, FileResult, ReportDialog
from pixelkit.video import VideoSettings


class ImageRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = PixelKitApplication.instance() or PixelKitApplication([])

    def setUp(self):
        temporary_root = "/private/tmp" if Path("/private/tmp").is_dir() else None
        self.directory = tempfile.TemporaryDirectory(prefix="pixelkit-retry-images-", dir=temporary_root)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.preferences = QSettings(str(self.root / "preferences.ini"), QSettings.Format.IniFormat)
        message_patch = patch("pixelkit.app.ImageMagickStudio._show_message", return_value=QMessageBox.StandardButton.Ok)
        self.message = message_patch.start()
        self.addCleanup(message_patch.stop)
        with patch("pixelkit.app.find_magick", return_value="magick"), patch("pixelkit.video_panel.find_ffmpeg", return_value="ffmpeg"), patch("pixelkit.video_panel.find_ffprobe", return_value="ffprobe"):
            self.window = ImageMagickStudio(PresetStore(self.preferences))
        self.addCleanup(self.close_window)
        self.saved_settings = Preset(
            resize_mode="dimensions", width=640, height=360, keep_ratio=False,
            quality=71, strip_metadata=False, background="#123456",
            output_format="WEBP", target_kib=640,
        )
        self.live_settings = Preset(
            resize_mode="longest_side", longest_side=1920, quality=94,
            strip_metadata=True, background="white", output_format="JPG",
        )
        self.configure(self.live_settings)
        self.message.reset_mock()

    def close_window(self):
        self.window.worker = None
        self.window.video_panel.worker = None
        self.window._set_processing_state(False)
        self.window.video_panel._set_busy(False)
        self.window.close()
        self.app.processEvents()

    def image(self, name: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"Original fixture: {name}".encode())
        return path

    def configure(self, settings: Preset):
        self.window.resize_mode.setCurrentIndex(1 if settings.resize_mode == "longest_side" else 0)
        self.window.width_edit.setText(str(settings.width or ""))
        self.window.height_edit.setText(str(settings.height or ""))
        self.window.long_side_edit.setText(str(settings.longest_side or ""))
        self.window.keep_ratio.setChecked(settings.keep_ratio)
        self.window.quality_slider.setValue(settings.quality)
        self.window.strip_metadata.setChecked(settings.strip_metadata)
        self.window.background_edit.setText(settings.background)
        self.window.format_combo.setCurrentText(settings.output_format)
        self.window.target_size_check.setChecked(settings.target_kib is not None)
        self.window.target_size_edit.setText(str(settings.target_kib or 500))

    def failure(self, name: str, *, output: Path | None = None) -> FileResult:
        source = self.image(name)
        return FileResult(
            source, output or self.root / "exports" / f"{source.stem}_optimized.webp",
            source.stat().st_size, None, "ImageMagick: improper image header",
            processing_settings=(("Maximum quality", "unrelated display text"),),
        )

    def success(self, name: str) -> FileResult:
        source = self.image(name)
        output = self.image(f"exports/{source.stem}_optimized.webp")
        return FileResult(source, output, source.stat().st_size, output.stat().st_size)

    def stopped(self, name: str, status: str) -> FileResult:
        source = self.image(name)
        return FileResult(source, self.root / "exports" / f"{source.stem}_optimized.webp", source.stat().st_size, None, "Processing stopped", stopped=status)

    def report(self, *files: FileResult, settings: Preset | VideoSettings | None = None, output_dir: Path | None = None, cancelled: bool = False) -> BatchReport:
        return BatchReport(tuple(files), output_dir or self.root / "exports", cancelled, settings or self.saved_settings)

    def queue(self, paths: list[Path], *, output: Path | None = None):
        self.window._set_sources(paths)
        if output is not None:
            self.window.output_edit.setText(str(output))
            self.window.default_output = False

    def state(self):
        return (
            tuple(self.window.sources),
            tuple(self.window.source_list.item(row).toolTip() for row in range(self.window.source_list.count())),
            self.window.source_list.currentRow(),
            self.window._current_preset(),
            self.window.preset_combo.currentData(),
            self.window.output_edit.text(),
            self.window.default_output,
            self.window.status_label.full_text,
            self.window.last_report,
        )

    def disk(self):
        return {str(path.relative_to(self.root)): path.read_bytes() if path.is_file() else None for path in self.root.rglob("*")}

    def assert_prepared(self, report: BatchReport, sources: list[Path], output: Path, settings: Preset | None = None):
        self.assertEqual(self.window.sources, sources)
        self.assertEqual(self.window.source_list.count(), len(sources))
        self.assertEqual([self.window.source_list.item(row).toolTip() for row in range(len(sources))], [str(source) for source in sources])
        self.assertEqual(self.window._current_preset(), settings or self.saved_settings)
        self.assertIsNone(self.window.preset_combo.currentData())
        self.assertEqual(self.window.preset_combo.currentText(), "Custom settings")
        self.assertEqual(self.window.output_edit.text(), str(output))
        self.assertFalse(self.window.default_output)
        self.assertFalse(self.window.processing)
        self.assertTrue(self.window.process_button.isEnabled())
        self.assertTrue(self.window.quality_slider.isEnabled())
        self.assertTrue(self.window.format_combo.isEnabled())
        self.assertTrue(self.window.output_edit.isEnabled())
        self.assertIs(self.window.last_report, report)

    def test_worker_snapshot_is_optional_and_keeps_legacy_positional_arguments(self):
        source = self.image("legacy.png")
        output = self.root / "legacy.webp"
        for settings in (None, self.saved_settings):
            with self.subTest(settings=settings):
                kwargs = {"retry_settings": settings} if settings is not None else {}
                worker = BatchWorker([(["magick", str(source), str(output)], output)], self.root, 1024, **kwargs)
                reports = []
                worker.finished.connect(reports.append)
                worker.cancel()
                with patch("pixelkit.app.run_magick") as convert, patch("pixelkit.app.compress_to_size") as compress:
                    worker.run()
                self.assertIs(reports[0].retry_settings, settings)
                self.assertEqual(reports[0].files[0].status, "Skipped")
                self.assertTrue(reports[0].cancelled)
                convert.assert_not_called()
                compress.assert_not_called()

    def test_actual_start_captures_run_settings_before_later_ui_changes(self):
        source = self.image("captured.png")
        self.queue([source], output=self.root / "captured.webp")
        self.configure(self.saved_settings)
        with patch.object(BatchWorker, "start") as start:
            self.window._start_processing()
        start.assert_called_once()
        worker = self.window.worker
        self.assertIsInstance(worker, BatchWorker)
        self.assertEqual(worker.target_bytes, self.saved_settings.target_kib * 1024)
        worker.finished.disconnect()
        reports = []
        worker.finished.connect(reports.append)
        self.window._set_processing_state(False)
        self.configure(self.live_settings)
        with patch("pixelkit.app.compress_to_size", side_effect=ValueError("Size limit not reached")):
            worker.run()
        report = reports[0]
        self.assertEqual(report.retry_settings, self.saved_settings)
        self.assertNotEqual(report.retry_settings, self.window._current_preset())
        self.assertEqual(len(report.failed), 1)
        with self.assertRaises(FrozenInstanceError):
            report.retry_settings.quality = 99
        self.window.last_report = report
        self.assertTrue(self.window._retry_failed(report))
        self.assert_prepared(report, [source], report.failed[0].output)

    def test_single_failure_from_mixed_cancelled_batch_keeps_exact_unique_output(self):
        failed = self.failure("second/photo.png", output=self.root / "chosen folder" / "photo_optimized_3.webp")
        good = self.success("first/photo.png")
        cancelled = self.stopped("cancelled.png", "Cancelled")
        skipped = self.stopped("skipped.png", "Skipped")
        report = self.report(good, cancelled, failed, skipped, cancelled=True, output_dir=failed.output.parent)
        self.queue([file.source for file in report.files], output=report.output_dir)
        self.window.last_report = report
        before = self.disk()
        with patch.object(self.window, "_confirm") as confirm, patch("pixelkit.app.BatchWorker") as worker, patch.object(Path, "mkdir") as mkdir, patch.object(Path, "write_bytes") as write, patch.object(self.window.preset_store, "save") as save:
            self.assertTrue(self.window._retry_failed(report))
        self.assert_prepared(report, [failed.source], failed.output)
        self.assertEqual(self.window.output_edit.placeholderText(), "Output file")
        self.assertEqual(report.files, (good, cancelled, failed, skipped))
        for action in (confirm, worker, mkdir, write, save):
            action.assert_not_called()
        self.assertEqual(self.disk(), before)
        self.assertFalse(failed.output.parent.exists())
        self.assertEqual(self.preferences.allKeys(), [])

    def test_multiple_failures_use_recorded_folder_and_restore_longest_side_settings(self):
        first, second = self.failure("one.png"), self.failure("two.jpg")
        good = self.success("done.png")
        settings = Preset(resize_mode="longest_side", longest_side=1234, keep_ratio=False, quality=68, strip_metadata=False, background="#ab1234", output_format="Automatic")
        folder = self.root / "not created" / "retry exports"
        report = self.report(good, first, second, settings=settings, output_dir=folder)
        self.queue([good.source, first.source, second.source])
        self.window.last_report = report
        before = self.disk()
        with patch.object(self.window, "_confirm") as confirm, patch("pixelkit.app.BatchWorker") as worker:
            self.assertTrue(self.window._retry_failed(report))
        self.assert_prepared(report, [first.source, second.source], folder, settings)
        self.assertEqual(self.window.output_edit.placeholderText(), "Output folder")
        self.assertFalse(self.window.target_size_check.isChecked())
        self.assertFalse(self.window.target_size_edit.isEnabled())
        self.assertFalse(folder.exists())
        self.assertEqual(self.disk(), before)
        confirm.assert_not_called()
        worker.assert_not_called()

    def test_existing_queue_subset_of_original_report_needs_no_confirmation(self):
        first, second = self.failure("first.png"), self.failure("second.png")
        good = self.success("good.png")
        report = self.report(first, good, second)
        self.window.last_report = report
        for queued in ([good.source], [second.source, first.source], []):
            with self.subTest(queued=queued):
                self.queue(queued)
                with patch.object(self.window, "_confirm") as confirm:
                    self.assertTrue(self.window._retry_failed(report))
                self.assert_prepared(report, [first.source, second.source], report.output_dir)
                confirm.assert_not_called()

    def test_declining_unrelated_queue_replacement_preserves_everything(self):
        failed = self.failure("failed.png")
        report = self.report(failed)
        self.queue([failed.source, self.image("unrelated.png")], output=self.root / "custom folder")
        self.window.source_list.setCurrentRow(1)
        self.window.last_report = report
        state, disk = self.state(), self.disk()
        dialog = Mock()
        with patch.object(self.window, "_confirm", return_value=False) as confirm, patch("pixelkit.app.BatchWorker") as worker:
            self.assertFalse(self.window._retry_failed(report, dialog))
        confirm.assert_called_once()
        worker.assert_not_called()
        dialog.accept.assert_not_called()
        self.assertEqual(self.state(), state)
        self.assertEqual(self.disk(), disk)

    def test_accepting_unrelated_queue_replacement_prepares_without_starting(self):
        failed = self.failure("failed.png")
        report = self.report(failed)
        self.queue([self.image("unrelated.png")])
        self.window.last_report = report
        before = self.disk()
        dialog = Mock()
        with patch.object(self.window, "_confirm", return_value=True) as confirm, patch("pixelkit.app.BatchWorker") as worker:
            self.assertTrue(self.window._retry_failed(report, dialog))
        confirm.assert_called_once()
        worker.assert_not_called()
        dialog.accept.assert_called_once_with()
        self.assert_prepared(report, [failed.source], failed.output)
        self.assertEqual(self.disk(), before)

    def test_any_missing_failed_original_aborts_whole_retry_before_confirmation(self):
        first, missing = self.failure("present.png"), self.failure("missing.png")
        report = self.report(first, missing)
        self.queue([self.image("unrelated.png")])
        self.window.last_report = report
        missing.source.unlink()
        state, disk = self.state(), self.disk()
        dialog = Mock()
        with patch.object(self.window, "_confirm") as confirm, patch("pixelkit.app.BatchWorker") as worker:
            self.assertFalse(self.window._retry_failed(report, dialog))
        self.message.assert_called_once()
        self.assertEqual(self.message.call_args.args[0], QMessageBox.Icon.Warning)
        self.assertIn(str(missing.source), self.message.call_args.args[2])
        confirm.assert_not_called()
        worker.assert_not_called()
        dialog.accept.assert_not_called()
        self.assertEqual(self.state(), state)
        self.assertEqual(self.disk(), disk)

    def test_missing_success_or_stopped_originals_do_not_block_failed_only_retry(self):
        failed = self.failure("failed.png")
        good = self.success("good.png")
        stopped = self.stopped("stopped.png", "Cancelled")
        report = self.report(good, stopped, failed, cancelled=True)
        self.queue([file.source for file in report.files])
        self.window.last_report = report
        good.source.unlink()
        stopped.source.unlink()
        with patch.object(self.window, "_confirm") as confirm:
            self.assertTrue(self.window._retry_failed(report))
        self.assert_prepared(report, [failed.source], failed.output)
        self.message.assert_not_called()
        confirm.assert_not_called()

    def test_missing_original_after_confirmation_leaves_queue_and_settings_intact(self):
        failed = self.failure("failed.png")
        report = self.report(failed)
        self.queue([self.image("unrelated.png")])
        self.window.last_report = report
        state = self.state()

        def confirm(*_args):
            failed.source.unlink()
            return True

        dialog = Mock()
        with patch.object(self.window, "_confirm", side_effect=confirm):
            self.assertFalse(self.window._retry_failed(report, dialog))
        self.assertEqual(self.state(), state)
        self.message.assert_called_once()
        self.assertEqual(self.message.call_args.args[0], QMessageBox.Icon.Warning)
        dialog.accept.assert_not_called()

    def test_final_queue_population_retains_original_that_disappears_after_validation(self):
        failed = self.failure("racy.png")
        report = self.report(failed)
        self.window.last_report = report
        populate = self.window._set_sources

        def disappear_then_populate(paths, **kwargs):
            failed.source.unlink()
            return populate(paths, **kwargs)

        with patch.object(self.window, "_set_sources", side_effect=disappear_then_populate) as set_sources:
            self.assertTrue(self.window._retry_failed(report))
        self.assertTrue(set_sources.call_args.kwargs.get("preserve_missing"))
        self.assert_prepared(report, [failed.source], failed.output)
        self.assertFalse(failed.source.exists())

    def test_busy_image_video_or_worker_blocks_retry_and_leaves_report_visible(self):
        failed = self.failure("failed.png")
        report = self.report(failed)
        self.queue([failed.source])
        self.window.last_report = report
        cases = ((self.window, "processing", True), (self.window.video_panel, "processing", True), (self.window, "worker", Mock()), (self.window.video_panel, "worker", Mock()))
        for owner, field, value in cases:
            with self.subTest(owner=type(owner).__name__, field=field):
                if isinstance(value, Mock):
                    value.isRunning.return_value = True
                setattr(owner, field, value)
                state = self.state()
                dialog = Mock()
                with patch.object(self.window, "_confirm") as confirm, patch("pixelkit.app.BatchWorker") as worker:
                    self.assertFalse(self.window._retry_failed(report, dialog))
                self.assertEqual(self.state(), state)
                confirm.assert_not_called()
                worker.assert_not_called()
                dialog.accept.assert_not_called()
                setattr(owner, field, False if field == "processing" else None)

    def test_worker_becoming_busy_during_confirmation_aborts_preparation(self):
        failed = self.failure("failed.png")
        report = self.report(failed)
        self.queue([self.image("unrelated.png")])
        self.window.last_report = report
        state = self.state()
        busy_worker = Mock()
        busy_worker.isRunning.return_value = True

        def confirm(*_args):
            self.window.video_panel.worker = busy_worker
            return True

        dialog = Mock()
        with patch.object(self.window, "_confirm", side_effect=confirm):
            self.assertFalse(self.window._retry_failed(report, dialog))
        self.assertEqual(self.state(), state)
        dialog.accept.assert_not_called()

    def test_no_real_failures_or_wrong_snapshot_cannot_prepare_retry(self):
        failed = self.failure("failed.png")
        good = self.success("good.png")
        cancelled, skipped = self.stopped("cancelled.png", "Cancelled"), self.stopped("skipped.png", "Skipped")
        video_failure = replace(failed, media_type="video")
        self.queue([self.image("unrelated.png")])
        reports = (
            BatchReport((failed,), self.root),
            self.report(failed, settings=VideoSettings()),
            self.report(video_failure),
            self.report(good),
            self.report(cancelled, skipped, cancelled=True),
            self.report(),
        )
        for report in reports:
            with self.subTest(report=report):
                self.window.last_report = report
                state = self.state()
                with patch.object(self.window, "_confirm") as confirm, patch("pixelkit.app.BatchWorker") as worker:
                    self.assertFalse(self.window._retry_failed(report))
                self.assertEqual(self.state(), state)
                confirm.assert_not_called()
                worker.assert_not_called()

    def test_retry_targets_cannot_replace_any_original_or_successful_result(self):
        failed = self.failure("failed.png")
        good = self.success("good.png")
        stopped = self.stopped("skipped.png", "Skipped")
        for protected in (failed.source, good.source, stopped.source, good.output):
            with self.subTest(protected=protected):
                report = self.report(good, replace(failed, output=protected), stopped)
                self.queue([file.source for file in report.files])
                self.window.last_report = report
                state, disk = self.state(), self.disk()
                self.message.reset_mock()
                with patch.object(self.window, "_confirm") as confirm:
                    self.assertFalse(self.window._retry_failed(report))
                self.assertEqual(self.state(), state)
                self.assertEqual(self.disk(), disk)
                self.message.assert_called_once()
                self.assertEqual(self.message.call_args.args[0], QMessageBox.Icon.Warning)
                confirm.assert_not_called()

    def test_symlink_and_hardlink_retry_targets_cannot_alias_protected_files(self):
        failed = self.failure("failed.png")
        good = self.success("good.png")
        for kind in ("symlink", "hardlink"):
            for protected in (failed.source, good.source, good.output):
                with self.subTest(kind=kind, protected=protected):
                    target = self.root / f"{kind}-{protected.parent.name}-{protected.name}.webp"
                    if kind == "symlink":
                        target.symlink_to(protected)
                    else:
                        target.hardlink_to(protected)
                    report = self.report(good, replace(failed, output=target))
                    self.queue([good.source, failed.source])
                    self.window.last_report = report
                    state, disk = self.state(), self.disk()
                    self.message.reset_mock()
                    with patch.object(self.window, "_confirm") as confirm:
                        self.assertFalse(self.window._retry_failed(report))
                    self.assertEqual(self.state(), state)
                    self.assertEqual(self.disk(), disk)
                    self.message.assert_called_once()
                    self.assertEqual(self.message.call_args.args[0], QMessageBox.Icon.Warning)
                    confirm.assert_not_called()

    def test_target_becoming_alias_during_confirmation_aborts_preparation(self):
        failed = self.failure("failed.png", output=self.root / "new-output.webp")
        good = self.success("good.png")
        report = self.report(good, failed)
        self.queue([self.image("unrelated.png")])
        self.window.last_report = report
        state = self.state()
        original_bytes = good.output.read_bytes()

        def confirm(*_args):
            failed.output.symlink_to(good.output)
            return True

        dialog = Mock()
        with patch.object(self.window, "_confirm", side_effect=confirm), patch("pixelkit.app.BatchWorker") as worker:
            self.assertFalse(self.window._retry_failed(report, dialog))
        self.assertEqual(self.state(), state)
        self.assertEqual(good.output.read_bytes(), original_bytes)
        self.message.assert_called_once()
        self.assertEqual(self.message.call_args.args[0], QMessageBox.Icon.Warning)
        worker.assert_not_called()
        dialog.accept.assert_not_called()

    def test_invalid_saved_output_location_aborts_without_creating_directories(self):
        failed = self.failure("failed.png")
        second = self.failure("second.png")
        occupied = self.image("occupied")
        for files, folder in (((replace(failed, output=self.root),), self.root), ((replace(failed, output=occupied / "result.webp"),), occupied), ((failed, second), occupied)):
            with self.subTest(files=files):
                report = self.report(*files, output_dir=folder)
                self.queue([file.source for file in files])
                self.window.last_report = report
                state, disk = self.state(), self.disk()
                self.message.reset_mock()
                with patch.object(Path, "mkdir") as mkdir:
                    self.assertFalse(self.window._retry_failed(report))
                self.assertEqual(self.state(), state)
                self.assertEqual(self.disk(), disk)
                self.message.assert_called_once()
                self.assertEqual(self.message.call_args.args[0], QMessageBox.Icon.Warning)
                mkdir.assert_not_called()

    def test_user_can_edit_prepared_settings_before_explicit_start(self):
        failed = self.failure("failed.png")
        report = self.report(failed)
        self.window.last_report = report
        with patch("pixelkit.app.BatchWorker") as worker:
            self.assertTrue(self.window._retry_failed(report))
            worker.assert_not_called()
            self.window.quality_slider.setValue(86)
            worker.assert_not_called()
            self.window._start_processing()
        self.assertEqual(worker.call_args.kwargs["retry_settings"], replace(self.saved_settings, quality=86))
        self.assertEqual(worker.call_args.args[0][0][1], failed.output)
        worker.return_value.start.assert_called_once()
        self.assertEqual(report.retry_settings, self.saved_settings)
        self.assertFalse(failed.output.exists())

    def test_existing_failed_output_is_kept_until_overwrite_confirmed_at_explicit_start(self):
        output = self.image("chosen/result.webp")
        failed = self.failure("failed.png", output=output)
        report = self.report(failed)
        self.window.last_report = report
        original_bytes = output.read_bytes()
        with patch.object(self.window, "_confirm") as confirm:
            self.assertTrue(self.window._retry_failed(report))
        confirm.assert_not_called()
        self.assertEqual(output.read_bytes(), original_bytes)
        state = self.state()
        with patch.object(self.window, "_confirm", return_value=False) as confirm, patch("pixelkit.app.BatchWorker") as worker:
            self.window._start_processing()
        confirm.assert_called_once()
        worker.assert_not_called()
        self.assertEqual(self.state(), state)
        self.assertEqual(output.read_bytes(), original_bytes)

    def test_batch_retry_explicit_start_omits_successes_and_preserves_collision_outputs(self):
        first = self.failure("failed/photo.png")
        second = self.failure("second.png")
        good = self.success("good/photo.png")
        report = self.report(first, good, second)
        self.queue([file.source for file in report.files])
        self.window.last_report = report
        original_bytes = {file.source: file.source.read_bytes() for file in report.files}
        successful_bytes = good.output.read_bytes()
        self.assertTrue(self.window._retry_failed(report))
        with patch.object(BatchWorker, "start") as start:
            self.window._start_processing()
        start.assert_called_once()
        jobs = self.window.worker.jobs
        self.assertEqual([Path(command[1]) for command, _output in jobs], [first.source, second.source])
        self.assertEqual(jobs[0][1], report.output_dir / "photo_optimized_2.webp")
        self.assertNotIn(good.output, [output for _command, output in jobs])
        self.assertEqual({source: source.read_bytes() for source in original_bytes}, original_bytes)
        self.assertEqual(good.output.read_bytes(), successful_bytes)
        self.assertTrue(all(not output.exists() for _command, output in jobs))

    def test_real_report_retry_button_is_wired_and_accepts_only_after_preparation(self):
        failed = self.failure("failed.png")
        report = self.report(failed)
        self.queue([self.image("unrelated.png")])
        self.window.last_report = report
        self.window.show()
        self.app.processEvents()
        results = []

        def click_retry(dialog):
            self.assertIsInstance(dialog, ReportDialog)
            self.assertIs(dialog.report, report)
            dialog.show()
            self.app.processEvents()
            state = self.state()
            with patch.object(self.window, "_confirm", return_value=False):
                QTest.mouseClick(dialog.retry_button, Qt.MouseButton.LeftButton)
            self.assertTrue(dialog.isVisible())
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
            self.assertEqual(self.state(), state)
            with patch.object(self.window, "_confirm", return_value=True):
                QTest.mouseClick(dialog.retry_button, Qt.MouseButton.LeftButton)
            self.assertFalse(dialog.isVisible())
            results.append(dialog.result())
            return dialog.result()

        with patch.object(ReportDialog, "exec", click_retry), patch("pixelkit.app.BatchWorker") as worker, patch("pixelkit.report.QApplication.clipboard") as clipboard, patch("pixelkit.report.QDesktopServices.openUrl") as browser:
            self.window._show_last_report()
        self.assertEqual(results, [QDialog.DialogCode.Accepted])
        self.assert_prepared(report, [failed.source], failed.output)
        worker.assert_not_called()
        clipboard.assert_not_called()
        browser.assert_not_called()

    def test_report_retry_button_is_disabled_if_a_video_worker_is_running(self):
        failed = self.failure("failed.png")
        report = self.report(failed)
        self.window.last_report = report
        worker = Mock()
        worker.isRunning.return_value = True
        self.window.video_panel.worker = worker
        viewed = []

        def inspect_dialog(dialog):
            viewed.append(dialog)
            dialog.show()
            self.app.processEvents()
            self.assertFalse(dialog.retry_button.isEnabled())
            dialog.retry_button.click()
            self.assertEqual(self.window.sources, [])
            dialog.reject()
            return dialog.result()

        with patch.object(ReportDialog, "exec", inspect_dialog):
            self.window._show_last_report()
        self.assertEqual(len(viewed), 1)


if __name__ == "__main__":
    unittest.main()
