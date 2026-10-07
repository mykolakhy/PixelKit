from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPoint, QSettings
from PyQt6.QtWidgets import QApplication

from pixelkit.app import BatchWorker, ImageMagickStudio
from pixelkit.bug_report import BugReportContext, diagnostics
from pixelkit.bug_report_dialog import BugReportDialog
from pixelkit.presets import PresetStore
from pixelkit.report import BatchReport, FileResult, ReportDialog
from pixelkit.video import VideoSettings, VideoWorker


class BugReportIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        store = PresetStore(QSettings(str(self.root / "presets.ini"), QSettings.Format.IniFormat))
        with patch("pixelkit.app.find_magick", return_value="magick"):
            self.window = ImageMagickStudio(store)
        self.addCleanup(self.window.close)

    @staticmethod
    def fill(dialog):
        dialog.title_edit.setText("Unexpected output")
        dialog.steps_edit.setPlainText("Add a file and process it")
        dialog.actual_edit.setPlainText("The result is wrong")

    def test_help_and_shared_header_open_general_drafts_without_previous_errors(self):
        private = FileResult(self.root / "private.png", self.root / "private.webp", 100, None, "Previous failure must stay out of the general report")
        self.window.last_report = BatchReport((private,), self.root)
        with patch.object(BugReportDialog, "exec", return_value=0):
            self.window.report_bug_button.click()
            images = self.window._bug_report_dialogs[("general", "Images")]
            self.assertIsNone(images.context.file)
            self.assertNotIn(private.error, images.report_text())
            self.fill(images)
            self.window.report_bug_action.trigger()
            self.assertIs(images, self.window._bug_report_dialogs[("general", "Images")])
            self.assertEqual(images.title_edit.text(), "Unexpected output")
            self.window.mode_buttons[1].click()
            self.window.report_bug_action.trigger()
            video = self.window._bug_report_dialogs[("general", "Video")]
            self.assertIsNone(video.context.file)
            self.assertIn("Mode: Video", video.technical_edit.toPlainText())
            self.assertNotIn(private.error, video.report_text())
        self.assertIn("Help", [action.text() for action in self.window.menuBar().actions()])

    def test_help_exists_in_windows_branch_without_macos_file_menu(self):
        store = PresetStore(QSettings(str(self.root / "windows.ini"), QSettings.Format.IniFormat))
        with patch("pixelkit.app.sys.platform", "win32"), patch("pixelkit.app.find_magick", return_value="magick"), patch("pixelkit.video_panel.find_ffmpeg", return_value=None), patch("pixelkit.video_panel.find_ffprobe", return_value=None):
            window = ImageMagickStudio(store)
        self.addCleanup(window.close)
        self.assertEqual([action.text() for action in window.menuBar().actions()], ["Help"])
        with patch.object(BugReportDialog, "exec", return_value=0):
            window.report_bug_action.trigger()
        self.assertIn(("general", "Images"), window._bug_report_dialogs)

    def test_selected_failure_reuses_draft_after_report_reopens_and_protects_new_queue(self):
        first = FileResult(self.root / "first.png", self.root / "first.webp", 100, None, "First error")
        second = FileResult(self.root / "private second.mov", self.root / "private second.mp4", 200, None, "Second error", media_type="video", processing_settings=(("Quality preset", "high"),))
        report = BatchReport((first, second), self.root)
        with patch.object(BugReportDialog, "exec", return_value=0):
            results = ReportDialog(report, self.window)
            self.addCleanup(results.close)
            results.table.selectRow(1)
            results.report_bug_button.click()
            draft = self.window._bug_report_dialogs[("failure", id(second))]
            self.assertIs(draft.context.file, second)
            self.assertIn("Second error", draft.technical_edit.toPlainText())
            self.assertNotIn("First error", draft.technical_edit.toPlainText())
            self.assertNotIn(second.source.name, draft.report_text())
            self.fill(draft)
            draft.technical_edit.appendPlainText("User edited details")
            before = draft.report_text()
            draft.close()
            results.close()
            new_source = self.root / "new.png"
            new_source.write_bytes(b"original")
            self.window._set_sources([new_source])
            reopened = ReportDialog(report, self.window)
            self.addCleanup(reopened.close)
            reopened.table.selectRow(1)
            reopened.report_bug_button.click()
            self.assertIs(draft, self.window._bug_report_dialogs[("failure", id(second))])
            self.assertEqual(draft.report_text(), before)
            self.assertIn(new_source.resolve(), draft.context.protected_paths)
        with patch("pixelkit.bug_report_dialog.QFileDialog.getSaveFileName", return_value=(str(new_source), "")):
            draft._save_report()
        self.assertEqual(new_source.read_bytes(), b"original")
        self.assertIn("cannot replace", draft.feedback.text())

    def test_general_save_guards_active_image_video_and_late_completed_outputs(self):
        source = self.root / "source.png"
        video_source = self.root / "source.mov"
        image_output = self.root / "image.webp"
        video_output = self.root / "video.mp4"
        for path in (source, video_source, image_output, video_output):
            path.write_bytes(b"original media")
        self.window.sources = [source]
        self.window.video_panel.sources = [video_source]
        self.window.worker = BatchWorker([(["magick", str(source), str(image_output)], image_output)], self.root)
        self.window.video_panel.worker = VideoWorker([(video_source, video_output)], self.root, VideoSettings())
        self.window.output_edit.setText(str(self.root))
        self.window.video_panel.output_edit.setText(str(self.root))
        with patch.object(BugReportDialog, "exec", return_value=0):
            self.window._show_bug_report()
        draft = self.window._bug_report_dialogs[("general", "Images")]
        self.fill(draft)
        before = draft.report_text()
        for destination in (image_output, video_output):
            with self.subTest(destination=destination), patch("pixelkit.bug_report_dialog.QFileDialog.getSaveFileName", return_value=(str(destination), "")):
                draft._save_report()
            self.assertEqual(destination.read_bytes(), b"original media")
            self.assertIn("cannot replace", draft.feedback.text())
            self.assertEqual(draft.report_text(), before)
        late = self.root / "completed-after-opening.webp"
        def complete_during_chooser(*args):
            late.write_bytes(b"completed output")
            self.window.last_report = BatchReport((FileResult(source, late, 100, 50),), self.root)
            return str(late), ""
        with patch("pixelkit.bug_report_dialog.QFileDialog.getSaveFileName", side_effect=complete_during_chooser):
            draft._save_report()
        self.assertEqual(late.read_bytes(), b"completed output")
        self.assertEqual(draft.report_text(), before)

    def test_image_failure_keeps_executed_settings_after_controls_change(self):
        source, output = self.root / "image.png", self.root / "image.webp"
        source.write_bytes(b"original")
        self.window.resize_mode.setCurrentIndex(1)
        self.window.long_side_edit.setText("1920")
        self.window.quality_slider.setValue(72)
        self.window.strip_metadata.setChecked(True)
        command = self.window._build_command(source, output)
        worker = BatchWorker([(command, output)], self.root, target_bytes=512000)
        reports = []
        worker.finished.connect(reports.append)
        with patch("pixelkit.app.compress_to_size", side_effect=ValueError("Cannot encode this image")):
            worker.run()
        self.window.resize_mode.setCurrentIndex(0)
        self.window.width_edit.setText("321")
        self.window.quality_slider.setValue(92)
        self.window.strip_metadata.setChecked(False)
        text = diagnostics(BugReportContext(file=reports[0].files[0]))
        for value in ("Resize geometry: 1920x1920", "Maximum quality: 72", "Remove metadata: Yes", "File-size limit: 512000 bytes"):
            self.assertIn(value, text)
        self.assertNotIn("Maximum quality: 92", text)
        self.assertEqual(source.read_bytes(), b"original")

    def test_video_failure_captures_quality_resolution_audio_and_target(self):
        source, output = self.root / "video.mov", self.root / "video.mp4"
        source.write_bytes(b"original")
        settings = VideoSettings(preset="high", max_height=720, audio="remove", target_bytes=123000)
        worker = VideoWorker([(source, output)], self.root, settings)
        reports = []
        worker.finished.connect(reports.append)
        with patch("pixelkit.video.find_ffmpeg", return_value="ffmpeg"), patch("pixelkit.video.find_ffprobe", return_value="ffprobe"), patch("pixelkit.video._run_captured", return_value=subprocess.CompletedProcess([], 0, " V....D libx264 encoder", "")), patch("pixelkit.video.probe_video", side_effect=ValueError("Cannot read this video")):
            worker.run()
        text = diagnostics(BugReportContext(mode="Video", file=reports[0].files[0]))
        for value in ("Quality preset: high", "Resolution: Up to 720p", "Audio: remove", "File-size limit: 123000 bytes"):
            self.assertIn(value, text)
        self.assertEqual(source.read_bytes(), b"original")
        self.assertFalse(output.exists())

    def test_stopped_successful_and_empty_error_rows_cannot_open_failure_form(self):
        files = (
            FileResult(self.root / "good.png", self.root / "good.webp", 100, 20),
            FileResult(self.root / "cancelled.png", self.root / "cancelled.webp", 100, None, "Cancelled", stopped="Cancelled"),
            FileResult(self.root / "unknown.png", self.root / "unknown.webp", 100, None),
        )
        dialog = ReportDialog(BatchReport(files, self.root), self.window)
        self.addCleanup(dialog.close)
        with patch.object(self.window, "_show_bug_report") as show:
            for row in range(len(files)):
                dialog.table.selectRow(row)
                self.assertTrue(dialog.report_bug_button.isHidden())
                self.assertFalse(dialog.report_bug_button.isEnabled())
                dialog._report_bug()
            dialog.table.clearSelection()
            dialog._report_bug()
            show.assert_not_called()

    def test_report_actions_fit_minimum_window_with_friendly_error_and_export_feedback(self):
        failed = FileResult(self.root / "bad.png", self.root / "bad.webp", 100, None, "magick: improper image header")
        dialog = ReportDialog(BatchReport((failed,), self.root), self.window)
        self.addCleanup(dialog.close)
        dialog.resize(660, 380)
        dialog.export_status.setText("Copied")
        dialog.show()
        for _ in range(4):
            self.app.processEvents()
        self.assertEqual((dialog.width(), dialog.height()), (660, 380))
        for widget in (dialog.copy_error_button, dialog.save_error_button, dialog.technical_details_button, dialog.report_bug_button, dialog.compare_button, dialog.open_folder):
            self.assertFalse(widget.visibleRegion().isEmpty())
            self.assertTrue(dialog.rect().contains(widget.mapTo(dialog, QPoint(0, 0))))
            self.assertTrue(dialog.rect().contains(widget.mapTo(dialog, QPoint(widget.width() - 1, widget.height() - 1))))


if __name__ == "__main__":
    unittest.main()
