from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QCoreApplication, QMimeData, QObject, QPointF, QSettings, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import QDialog

from pixelkit.app import BatchWorker, ImageMagickStudio, PixelKitApplication
from pixelkit.presets import PresetStore
from pixelkit.quick_processing import ENABLED_KEY, FOLDER_KEY, publish_output, unique_outputs
from pixelkit.report import BatchReport, FileResult
from pixelkit.runtime import find_magick
from pixelkit.video import VideoWorker, find_ffmpeg
from PyQt6.QtTest import QTest


class ControlledWorker(QObject):
    progress = pyqtSignal(int, int, str)
    encoding_progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)

    def __init__(self, jobs, output_dir, settings=None, **kwargs):
        super().__init__()
        self.jobs = jobs
        self.output_dir = output_dir
        self.settings = settings if settings is not None else kwargs.get("retry_settings")
        self.overwrite = kwargs.get("overwrite", True)
        self.cancel_event = Event()
        self.running = False

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def wait(self):
        self.running = False

    def cancel(self):
        self.cancel_event.set()


class QuickProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = PixelKitApplication.instance() or PixelKitApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.settings.setValue("onboarding/dismissed", True)
        with patch("pixelkit.app.find_magick", return_value="magick"), patch("pixelkit.video_panel.find_ffmpeg", return_value="ffmpeg"), patch("pixelkit.video_panel.find_ffprobe", return_value="ffprobe"):
            self.window = ImageMagickStudio(PresetStore(self.settings))
        self.window.show()
        self.images, self.videos = [], []
        for target, workers in (("pixelkit.app.BatchWorker", self.images), ("pixelkit.app.VideoWorker", self.videos)):
            def create(*args, _workers=workers, **kwargs):
                worker = ControlledWorker(*args, **kwargs)
                _workers.append(worker)
                return worker
            context = patch(target, side_effect=create)
            context.start()
            self.addCleanup(context.stop)
        self.addCleanup(self.close_window)
        self.pump()

    def close_window(self):
        self.window.worker = None
        self.window.video_panel.worker = None
        self.window._quick_active = None
        self.window._quick_pending.clear()
        self.window._set_processing_state(False)
        self.window.video_panel._set_busy(False)
        self.window.close()
        self.pump()

    def pump(self):
        for _ in range(4):
            self.app.processEvents()

    def file(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original contents" * 32)
        return path

    def drop(self, files, *, finish=True, accepted=True):
        target = self.window
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in files])
        center = target.rect().center()
        for cls in (QDragEnterEvent, QDragMoveEvent):
            event = cls(center, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QCoreApplication.sendEvent(target, event)
            self.assertEqual(event.isAccepted(), accepted)
        if finish:
            event = QDropEvent(QPointF(center), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QCoreApplication.sendEvent(target, event)
            self.assertEqual(event.isAccepted(), accepted)

    def enable(self):
        self.window.quick_switch.setChecked(True)

    def complete(self, media="image", error=None, cancelled=False):
        worker = self.images[-1] if media == "image" else self.videos[-1]
        results = []
        for source, output in worker.jobs:
            source = Path(source[1]) if media == "image" else source
            results.append(FileResult(source, output, 512, None if error or cancelled else 128, error,
                                      "Cancelled" if cancelled else None, media_type=media))
        report = BatchReport(tuple(results), worker.output_dir, cancelled, worker.settings)
        worker.running = False
        worker.finished.emit(report)
        self.pump()

    def test_off_by_default_and_enabling_never_processes_existing_selection(self):
        source = self.file("manual.png")
        self.window._set_sources([source])
        self.assertFalse(self.window.quick_switch.isChecked())
        self.enable()
        self.pump()
        self.assertEqual(self.images, [])
        self.assertEqual(list(self.window._quick_pending), [])

    def test_drag_hover_never_starts_and_drop_starts_only_new_files(self):
        previous, incoming = self.file("manual.png"), self.file("incoming.png")
        self.window._set_sources([previous])
        self.enable()
        self.drop([incoming], finish=False)
        self.pump()
        self.assertEqual(self.images, [])
        self.drop([incoming])
        self.assertEqual(self.images, [])  # Native drop returns before starting a worker.
        self.pump()
        self.assertEqual([Path(job[0][1]) for job in self.images[0].jobs], [incoming])
        self.assertFalse(self.images[0].overwrite)
        self.assertIn(previous, self.window.sources)

    def test_turning_off_restores_manual_drop_behavior(self):
        self.enable()
        self.window.quick_switch.setChecked(False)
        self.drop([self.file("manual.png")])
        self.pump()
        self.assertFalse(self.images)
        self.assertEqual(len(self.window.sources), 1)

    def test_setting_and_shared_destination_survive_restart(self):
        folder = self.root / "results"
        self.enable()
        self.window._quick_destination_changed(str(folder))
        self.assertEqual(self.window.video_panel.quick_destination.folder, str(folder))
        self.assertTrue(self.settings.value(ENABLED_KEY, False, type=bool))
        self.assertEqual(self.settings.value(FOLDER_KEY), str(folder))
        with patch("pixelkit.app.find_magick", return_value="magick"):
            reopened = ImageMagickStudio(PresetStore(self.settings))
        try:
            self.assertTrue(reopened.quick_switch.isChecked())
            self.assertEqual(reopened.quick_folder, str(folder))
        finally:
            reopened.close()

    def test_invalid_saved_mode_stays_off(self):
        self.settings.setValue(ENABLED_KEY, "damaged preference")
        with patch("pixelkit.app.find_magick", return_value="magick"):
            reopened = ImageMagickStudio(PresetStore(self.settings))
        try:
            self.assertFalse(reopened.quick_enabled)
            self.assertFalse(reopened.quick_switch.isChecked())
        finally:
            reopened.close()

    def test_switch_and_cards_fit_default_and_large_windows_in_both_states(self):
        from PyQt6.QtWidgets import QScrollArea
        for enabled in (True, False):
            self.window.quick_switch.setChecked(enabled)
            for size in ((1100,780),(1440,900)):
                self.window.resize(*size)
                self.pump()
                self.assertEqual(self.window.quick_switch.visibleRegion().boundingRect(), self.window.quick_switch.rect())
                self.assertEqual(self.window.findChild(QScrollArea, "imageScroll").verticalScrollBar().maximum(), 0)

    def test_completed_video_controls_fit_default_and_large_windows(self):
        from PyQt6.QtWidgets import QScrollArea
        self.enable()
        self.drop([self.file("recording.mov")])
        self.pump()
        self.complete("video")
        panel = self.window.video_panel
        scroll = panel.findChild(QScrollArea)
        for enabled in (True, False):
            self.window.quick_switch.setChecked(enabled)
            for limited in (False, True):
                panel.target_size_check.setChecked(limited)
                for size in ((1100, 780), (1440, 900)):
                    with self.subTest(quick=enabled, limited=limited, size=size):
                        self.window.resize(*size)
                        self.pump()
                        self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                        destination = panel.quick_destination if enabled else panel.manual_output_row
                        for control in (panel.source_list, panel.report_button, panel.open_output_button,
                                        panel.process_button, destination):
                            self.assertTrue(control.isVisible())
                            self.assertEqual(control.visibleRegion().boundingRect(), control.rect())

    def test_mixed_drop_is_sequential_and_uses_each_media_settings(self):
        image, video = self.file("image.png"), self.file("video.mov")
        self.window.quality_slider.setValue(65)
        self.window.video_panel.audio_combo.setCurrentIndex(self.window.video_panel.audio_combo.findData("remove"))
        self.enable()
        self.drop([image, video])
        self.pump()
        self.assertEqual(len(self.images), 1)
        self.assertFalse(self.videos)
        self.assertEqual(self.images[0].settings.quality, 65)
        self.complete()
        self.assertEqual(len(self.videos), 1)
        self.assertEqual(self.videos[0].settings.audio, "remove")
        self.assertEqual(self.window.media_stack.currentIndex(), 1)
        self.assertFalse(self.videos[0].overwrite)
        self.complete("video")
        self.assertIsNone(self.window._quick_active)

    def test_busy_drops_queue_once_and_keep_settings_from_drop_time(self):
        first, second = self.file("one.png"), self.file("two.png")
        self.enable()
        self.drop([first])
        self.pump()
        self.window.quality_slider.setValue(60)
        self.drop([first, second, second])
        self.window.quality_slider.setValue(90)
        self.pump()
        self.assertEqual(len(self.images), 1)
        self.assertEqual(len(self.window._quick_pending), 1)
        self.complete()
        self.assertEqual(len(self.images), 2)
        self.assertEqual(self.images[1].settings.quality, 60)
        self.assertEqual([Path(job[0][1]) for job in self.images[1].jobs], [second])
        self.complete()
        self.assertEqual(len(self.window.last_report.successful), 2)

    def test_switching_off_keeps_accepted_jobs_but_rejects_further_busy_drops(self):
        first, second, third = [self.file(name) for name in ("one.png", "two.png", "three.png")]
        self.enable()
        self.drop([first])
        self.pump()
        self.drop([second])
        self.window.quick_switch.setChecked(False)
        self.drop([third], accepted=False)
        self.complete()
        self.assertEqual(len(self.images), 2)
        self.complete()
        self.drop([third])
        self.pump()
        self.assertEqual(len(self.images), 2)

    def test_cancel_stops_active_worker_and_skips_every_queued_media(self):
        self.enable()
        self.drop([self.file("one.png")])
        self.pump()
        self.drop([self.file("two.png"), self.file("three.mov")])
        self.window._cancel_processing()
        self.assertTrue(self.images[0].cancel_event.is_set())
        self.assertFalse(self.window._quick_pending)
        self.complete(cancelled=True)
        self.assertEqual(len(self.images), 1)
        self.assertFalse(self.videos)
        self.assertEqual(self.window.video_panel.last_report.files[0].status, "Skipped")

    def test_no_reports_pop_up_and_failure_logs_survive_later_success(self):
        self.enable()
        with patch.object(self.window, "_show_last_report") as popup:
            self.drop([self.file("bad.png")])
            self.pump()
            self.drop([self.file("good.png")])
            self.complete(error="A precise decoder error")
            self.complete()
            popup.assert_not_called()
        self.assertEqual(len(self.window.last_report.failed), 1)
        self.assertEqual(self.window.last_report.failed[0].error, "A precise decoder error")
        self.assertEqual(len(self.window.last_report.successful), 1)
        self.assertIn("error details", self.window.status_label.full_text)
        self.assertTrue(self.window.report_button.isEnabled())

    def test_video_quick_completion_suppresses_modal_report(self):
        self.enable()
        with patch.object(self.window.video_panel, "show_report") as popup:
            self.drop([self.file("video.mov")])
            self.pump()
            self.complete("video")
            popup.assert_not_called()
        self.assertTrue(self.window.video_panel.report_button.isEnabled())

    def test_quick_drop_waits_for_manual_batch_and_does_not_process_it_twice(self):
        first, second = self.file("manual.png"), self.file("quick.png")
        self.window._set_sources([first])
        self.window._start_processing()
        self.enable()
        self.drop([first, second])
        self.pump()
        self.assertEqual(len(self.images), 1)
        with patch.object(self.window, "_show_last_report") as popup:
            self.complete()
            popup.assert_not_called()
        self.assertEqual(len(self.images), 2)
        self.assertEqual([Path(job[0][1]) for job in self.images[1].jobs], [second])

    def test_cancelling_manual_batch_also_stops_new_quick_jobs(self):
        first, second = self.file("manual.png"), self.file("quick.png")
        self.window._set_sources([first])
        self.window._start_processing()
        self.enable()
        self.drop([second])
        self.window._cancel_processing()
        self.complete(cancelled=True)
        self.assertEqual(len(self.images), 1)
        self.assertFalse(self.window._quick_pending)
        self.assertEqual({file.status for file in self.window.last_report.files}, {"Skipped", "Cancelled"})

    def test_folder_and_collision_names_apply_to_both_media(self):
        image, video = self.file("one/photo.png"), self.file("two/photo.mov")
        folder = self.root / "results"
        folder.mkdir()
        existing = folder / "photo_optimized.png"
        existing.write_bytes(b"keep existing output")
        self.window._quick_destination_changed(str(folder))
        self.enable()
        self.drop([image, video])
        self.pump()
        self.assertEqual(self.images[0].jobs[0][1], folder / "photo_optimized_2.png")
        self.complete()
        self.assertEqual(self.videos[0].jobs[0][1], folder / "photo_optimized.mp4")
        self.assertEqual(existing.read_bytes(), b"keep existing output")

    def test_validation_failure_is_reported_without_creating_output_or_opening_dialog(self):
        source = self.file("image.png")
        folder = self.root / "not-created"
        self.window._quick_destination_changed(str(folder))
        self.window.width_edit.setText("invalid")
        self.enable()
        with patch.object(self.window, "_show_message") as dialog:
            self.drop([source])
            self.pump()
            dialog.assert_not_called()
        self.assertFalse(self.images)
        self.assertFalse(folder.exists())
        self.assertIn("Could not start", self.window.last_report.failed[0].error)

    def test_output_preparation_failure_does_not_wedge_queue(self):
        bad_folder = self.file("not-a-folder")
        self.window._quick_destination_changed(str(bad_folder))
        self.enable()
        self.drop([self.file("one.png")])
        self.pump()
        self.assertIsNone(self.window._quick_active)
        self.assertFalse(self.window.processing)
        self.assertTrue(self.window.last_report.failed)
        self.window._quick_destination_changed("")
        self.drop([self.file("two.png")])
        self.pump()
        self.assertEqual(len(self.images), 1)

    def test_modal_dialog_blocks_imports_even_with_quick_processing_on(self):
        self.enable()
        dialog = QDialog(self.window)
        dialog.setModal(True)
        dialog.show()
        self.pump()
        try:
            self.drop([self.file("image.png")], accepted=False)
        finally:
            dialog.close()
        self.assertFalse(self.images)

    def test_cancel_video_also_clears_pending_images(self):
        self.enable()
        self.drop([self.file("video.mov"), self.file("image.png")])
        self.pump()
        self.window.video_panel.cancel_processing()
        self.assertTrue(self.videos[0].cancel_event.is_set())
        self.complete("video", cancelled=True)
        self.assertFalse(self.images)
        self.assertEqual(self.window.last_report.files[0].status, "Skipped")

    def test_real_mixed_drop_writes_both_results_without_touching_originals(self):
        magick, ffmpeg = find_magick(), find_ffmpeg()
        if not magick or not ffmpeg:
            self.skipTest("ImageMagick and FFmpeg are needed for the integration check")
        image, video = self.root / "real image.png", self.root / "real video.mp4"
        subprocess.run([magick, "-size", "120x80", "gradient:", str(image)], check=True, capture_output=True)
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15", "-t", "1", "-c:v", "libx264", "-crf", "0", str(video)], check=True, capture_output=True)
        originals = {path:path.read_bytes() for path in (image, video)}
        self.window.magick = magick
        self.enable()
        with patch("pixelkit.app.BatchWorker", new=BatchWorker), patch("pixelkit.app.VideoWorker", new=VideoWorker), patch.object(self.window, "_show_last_report") as image_popup, patch.object(self.window.video_panel, "show_report") as video_popup:
            self.drop([image, video])
            deadline = time.monotonic() + 20
            while self.window._quick_pending or self.window._quick_active:
                self.pump()
                if time.monotonic() > deadline:
                    self.window._cancel_processing()
                    self.window.video_panel.cancel_processing()
                    for worker in (self.window.worker, self.window.video_panel.worker):
                        if worker:
                            worker.wait()
                    self.fail("Quick processing did not finish within 20 seconds")
                QTest.qWait(10)
            image_popup.assert_not_called()
            video_popup.assert_not_called()
        self.assertEqual(len(self.window.last_report.successful), 1, self.window.last_report)
        self.assertEqual(len(self.window.video_panel.last_report.successful), 1, self.window.video_panel.last_report)
        for report in (self.window.last_report, self.window.video_panel.last_report):
            self.assertTrue(report.successful[0].output.is_file())
        self.assertEqual({path:path.read_bytes() for path in originals}, originals)


class QuickOutputSafetyTests(unittest.TestCase):
    def test_publication_race_never_replaces_an_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temporary, output = root / "complete.png", root / "result.png"
            temporary.write_bytes(b"complete conversion")
            output.write_bytes(b"created while processing")
            with self.assertRaises(FileExistsError):
                publish_output(temporary, output, overwrite=False)
            self.assertEqual(output.read_bytes(), b"created while processing")
            self.assertEqual(temporary.read_bytes(), b"complete conversion")

    def test_allocating_names_avoids_originals_and_dangling_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, protected = root / "photo.png", root / "photo_optimized.png"
            (root / "photo_optimized_2.png").symlink_to(root / "missing.png")
            outputs = unique_outputs([(source, "png"), (source, "png")], None, [source, protected])
            self.assertEqual(outputs, (root / "photo_optimized_3.png", root / "photo_optimized_4.png"))

    def test_image_worker_reports_collision_and_keeps_the_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source.png", root / "result.png"
            source.write_bytes(b"original contents")
            def convert(command, **kwargs):
                Path(command[-1]).write_bytes(b"encoded contents")
                output.write_bytes(b"another file appeared")
                return subprocess.CompletedProcess(command, 0, "", "")
            worker = BatchWorker([(["magick", str(source), str(output)], output)], root, overwrite=False)
            reports = []
            worker.finished.connect(reports.append)
            with patch("pixelkit.app.run_magick", side_effect=convert):
                worker.run()
            self.assertEqual(output.read_bytes(), b"another file appeared")
            self.assertEqual(source.read_bytes(), b"original contents")
            self.assertEqual(len(reports[0].failed), 1)
            self.assertIn("no existing file was replaced", reports[0].failed[0].error)
