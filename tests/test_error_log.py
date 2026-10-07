from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSaveFile, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from pixelkit import __version__
from pixelkit.report import BatchReport, FileResult, ReportDialog, error_log
from pixelkit.video import encode_video, probe_video


class ErrorLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.failed = FileResult(
            self.root / "Пошкоджене фото.png", self.root / "result.webp", 100, None,
            "magick: improper image header\nПричина помилки\n" + "Diagnostic detail\n" * 300,
        )

    def dialog(self, *files):
        dialog = ReportDialog(BatchReport(files or (self.failed,), self.root))
        self.addCleanup(dialog.close)
        return dialog

    def test_copy_keeps_complete_raw_error_while_friendly_details_are_collapsed(self):
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        self.assertNotIn(self.failed.error, dialog.details.toPlainText())
        with patch("pixelkit.report.QApplication.clipboard") as clipboard:
            dialog.copy_error_button.setFocus()
            QTest.keyClick(dialog.copy_error_button, Qt.Key.Key_Space)
            text = clipboard.return_value.setText.call_args.args[0]
            self.assertTrue(text.endswith("Error:\n" + self.failed.error))
            for context in (__version__, str(self.failed.source), str(self.failed.output), "Media: image", "Result: Failed"):
                self.assertIn(context, text)
        self.assertEqual(dialog.export_status.text(), "Copied")
        self.assertIs(self.app.focusWidget(), dialog.copy_error_button)
        self.assertFalse(dialog.technical_details_button.isChecked())
        self.assertEqual(self.failed.error.count("Diagnostic detail"), 300)

    def test_copy_uses_current_row_and_selection_disables_and_resets_actions(self):
        video = FileResult(self.root / "відео.mov", self.root / "відео.mp4", 1000, None, "Video failure\nПовний лог", media_type="video", target_bytes=50, elapsed_seconds=0.6)
        good = FileResult(self.root / "good.png", self.root / "good.webp", 100, 40)
        cancelled = FileResult(self.root / "cancelled.png", self.root / "cancelled.webp", 100, None, "Cancelled", stopped="Cancelled")
        skipped = FileResult(self.root / "skipped.png", self.root / "skipped.webp", 100, None, "Skipped", stopped="Skipped")
        no_error = FileResult(self.root / "unknown.png", self.root / "unknown.webp", 100, None)
        dialog = self.dialog(self.failed, video, good, cancelled, skipped, no_error)
        with patch("pixelkit.report.QApplication.clipboard") as clipboard, patch("pixelkit.report.QFileDialog.getSaveFileName") as save:
            dialog._copy_error_log()
            dialog.table.selectRow(1)
            self.assertEqual(dialog.export_status.text(), "")
            dialog.copy_error_button.click()
            text = clipboard.return_value.setText.call_args.args[0]
            for context in (video.error, "Media: video", "File-size limit: 50 bytes", "Processing time: 0.6 seconds"):
                self.assertIn(context, text)
            clipboard.reset_mock()
            for row in range(2, 6):
                dialog.table.selectRow(row)
                for button in (dialog.copy_error_button, dialog.save_error_button):
                    self.assertFalse(button.isEnabled())
                    self.assertTrue(button.isHidden())
                dialog._copy_error_log()
                dialog._save_error_log()
            dialog.table.selectRow(0)
            dialog.table.clearSelection()
            dialog._copy_error_log()
            dialog._save_error_log()
            clipboard.assert_not_called()
            save.assert_not_called()
            self.assertEqual(dialog.export_status.text(), "")
            self.assertEqual(dialog.export_status.toolTip(), "")

    def test_save_creates_utf8_log_and_replaces_an_existing_log_without_touching_media(self):
        self.failed.source.write_bytes(b"original image")
        self.failed.output.write_bytes(b"existing output")
        dialog = self.dialog()
        destination = self.root / "Звіт помилки.log"
        for contents in (None, b"previous log"):
            with self.subTest(existing=contents is not None):
                if contents is not None:
                    destination.write_bytes(contents)
                with patch("pixelkit.report.QFileDialog.getSaveFileName", return_value=(str(destination), "Log files (*.log)")):
                    dialog.save_error_button.click()
                self.assertEqual(destination.read_bytes(), error_log(self.failed).encode("utf-8"))
                self.assertEqual(dialog.export_status.text(), "Saved")
                self.assertEqual(dialog.export_status.toolTip(), str(destination))
                self.assertEqual(self.failed.source.read_bytes(), b"original image")
                self.assertEqual(self.failed.output.read_bytes(), b"existing output")
                self.assertFalse(dialog.technical_details_button.isChecked())
                self.assertEqual(sorted(path.name for path in self.root.iterdir()), sorted([self.failed.source.name, self.failed.output.name, destination.name]))

    def test_save_cancel_and_selection_change_do_not_write_or_clear_processing_details(self):
        dialog = self.dialog(self.failed, FileResult(self.root / "good.png", self.root / "good.webp", 100, 40))
        initial = dialog.details.toPlainText()
        with patch("pixelkit.report.QFileDialog.getSaveFileName", return_value=("", "")), patch("pixelkit.report.QSaveFile") as writer:
            dialog._save_error_log()
            writer.assert_not_called()
            self.assertEqual(dialog.details.toPlainText(), initial)
        destination = self.root / "cancelled.log"
        def switch_selection(*args):
            dialog.table.selectRow(1)
            return str(destination), ""
        with patch("pixelkit.report.QFileDialog.getSaveFileName", side_effect=switch_selection), patch("pixelkit.report.QSaveFile") as writer:
            dialog._save_error_log()
            writer.assert_not_called()
        self.assertFalse(destination.exists())

    def test_save_refuses_every_batch_input_and_output_and_aliases(self):
        other = FileResult(self.root / "other.mov", self.root / "other.mp4", 1000, 300, media_type="video")
        paths = (self.failed.source, self.failed.output, other.source, other.output)
        for path in paths:
            path.write_bytes(("Media " + path.name).encode("utf-8"))
        expected = {path: path.read_bytes() for path in paths}
        dialog = self.dialog(self.failed, other)
        hardlink = self.root / "hardlink.log"
        hardlink.hardlink_to(other.source)
        symlink = self.root / "symlink.log"
        symlink.symlink_to(other.output)
        directory_alias = self.root / "alias"
        directory_alias.symlink_to(self.root, target_is_directory=True)
        aliases = (*paths, hardlink, symlink, directory_alias / self.failed.source.name, self.failed.output.with_name("RESULT.WEBP"))
        for destination in aliases:
            with self.subTest(destination=destination), patch("pixelkit.report.QFileDialog.getSaveFileName", return_value=(str(destination), "")), patch("pixelkit.report.QMessageBox.warning") as warning:
                dialog._save_error_log()
                warning.assert_called_once()
                self.assertIn("different path", warning.call_args.args[2])
                for path, data in expected.items():
                    self.assertEqual(path.read_bytes(), data)
                self.assertEqual(dialog.export_status.text(), "")

    def test_open_failure_preserves_details_and_can_be_retried(self):
        dialog = self.dialog()
        missing = self.root / "missing" / "error.log"
        initial = dialog.details.toPlainText()
        with patch("pixelkit.report.QFileDialog.getSaveFileName", return_value=(str(missing), "")), patch("pixelkit.report.QMessageBox.warning") as warning:
            dialog._save_error_log()
            warning.assert_called_once()
        self.assertFalse(missing.exists())
        self.assertEqual(dialog.details.toPlainText(), initial)
        valid = self.root / "retry.log"
        with patch("pixelkit.report.QFileDialog.getSaveFileName", return_value=(str(valid), "")):
            dialog._save_error_log()
        self.assertEqual(valid.read_text(encoding="utf-8"), error_log(self.failed))

    def test_partial_write_or_commit_failure_preserves_existing_log_and_cleans_temporary_file(self):
        destination = self.root / "existing.log"
        original = b"keep previous log"
        class PartialWrite(QSaveFile):
            def write(self, data):
                return super().write(data[:10])
        class CommitFailure(QSaveFile):
            def commit(self):
                self.cancelWriting()
                return super().commit()
        for writer in (PartialWrite, CommitFailure):
            with self.subTest(writer=writer):
                destination.write_bytes(original)
                dialog = self.dialog()
                with patch("pixelkit.report.QFileDialog.getSaveFileName", return_value=(str(destination), "")), patch("pixelkit.report.QSaveFile", writer), patch("pixelkit.report.QMessageBox.warning") as warning:
                    dialog._save_error_log()
                    warning.assert_called_once()
                self.assertEqual(destination.read_bytes(), original)
                self.assertEqual(list(self.root.iterdir()), [destination])
                self.assertEqual(dialog.export_status.text(), "")

    def test_empty_report_has_no_export_actions_and_direct_handlers_do_nothing(self):
        dialog = ReportDialog(BatchReport((), self.root))
        self.addCleanup(dialog.close)
        with patch("pixelkit.report.QApplication.clipboard") as clipboard, patch("pixelkit.report.QFileDialog.getSaveFileName") as save:
            dialog._copy_error_log()
            dialog._save_error_log()
            clipboard.assert_not_called()
            save.assert_not_called()
        for button in (dialog.copy_error_button, dialog.save_error_button):
            self.assertTrue(button.isHidden())
            self.assertFalse(button.isEnabled())

    def test_encoder_failure_keeps_first_cause_and_long_unicode_diagnostics(self):
        message = "Initial cause: cannot encode\n" + "Помилка відео\n" * 600 + "Final failure"
        script = "import sys; message = " + repr("Initial cause: cannot encode\n") + " + " + repr("Помилка відео\n") + " * 600 + 'Final failure'; sys.stderr.buffer.write(message.encode('utf-8')); sys.exit(1)"
        command = [sys.executable, "-c", script]
        with self.assertRaises(ValueError) as error:
            encode_video(command, 1, lambda: False, lambda percent: None)
        self.assertEqual(str(error.exception), "Could not compress this video. " + message)
        file = FileResult(self.root / "recording.mov", self.root / "recording.mp4", 100, None, str(error.exception), media_type="video")
        self.assertTrue(error_log(file).endswith(message))

    def test_probe_failure_keeps_first_cause_and_long_unicode_diagnostics(self):
        message = "Initial cause: damaged header\n" + "Помилка читання\n" * 200 + "Final failure"
        with patch("pixelkit.video._run_captured", return_value=subprocess.CompletedProcess([], 1, "", message)), self.assertRaises(ValueError) as error:
            probe_video(self.root / "recording.mov", "ffprobe")
        self.assertEqual(str(error.exception), "Cannot read this video. " + message)


if __name__ == "__main__":
    unittest.main()
