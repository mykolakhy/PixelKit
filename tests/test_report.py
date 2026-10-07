from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from pixelkit.app import BatchWorker
from pixelkit.report import BatchReport, FileResult, ReportDialog, human_size, size_change


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_totals_exclude_failed_files_and_allow_larger_outputs(self):
        report = BatchReport((
            FileResult(self.root / "one.png", self.root / "one.webp", 1000, 300),
            FileResult(self.root / "two.png", self.root / "two.jpg", 1000, 1100),
            FileResult(self.root / "bad.png", self.root / "bad.jpg", 9000, 8000, "Failed"),
        ), self.root)
        self.assertEqual(report.before, 2000)
        self.assertEqual(report.after, 1400)
        self.assertEqual(len(report.successful), 2)
        self.assertEqual(size_change(report.before, report.after), "Saved 30.0%")
        self.assertEqual(size_change(100, 150), "Larger 50.0%")
        self.assertEqual(size_change(100, 100), "No change")
        self.assertEqual(size_change(0, 1), "—")

    def test_completion_status_distinguishes_failures_successes_and_cancellation(self):
        good = FileResult(self.root / "one.png", self.root / "one.webp", 100, 40)
        bad = FileResult(self.root / "bad.png", self.root / "bad.webp", 100, None, "Encoding failed")
        cancelled = FileResult(self.root / "cancelled.png", self.root / "cancelled.webp", 100, None, "Processing cancelled", stopped="Cancelled")
        skipped = FileResult(self.root / "skipped.png", self.root / "skipped.webp", 100, None, "Not processed", stopped="Skipped")
        cases = (
            ((good,), False, "Done: 1 / 1 files saved"),
            ((bad,), False, "Failed: 0 / 1 files saved · 1 failed"),
            ((good, bad), False, "Completed: 1 / 2 files saved · 1 failed"),
            ((good, cancelled, skipped), True, "Cancelled: 1 / 3 files saved"),
            ((bad, cancelled, skipped), True, "Cancelled: 0 / 3 files saved · 1 failed"),
            ((), False, "No files processed"),
        )
        for files, was_cancelled, expected in cases:
            with self.subTest(expected=expected):
                report = BatchReport(files, self.root, cancelled=was_cancelled)
                self.assertEqual(report.completion_status(), expected)
                self.assertEqual(report.failed, tuple(file for file in files if file is bad))

    def test_report_opens_on_first_failure_ahead_of_successful_and_stopped_files(self):
        files = (
            FileResult(self.root / "good.png", self.root / "good.webp", 100, 40),
            FileResult(self.root / "cancelled.png", self.root / "cancelled.webp", 100, None, "Processing cancelled", stopped="Cancelled"),
            FileResult(self.root / "bad.png", self.root / "bad.webp", 100, None, "The first failed conversion"),
            FileResult(self.root / "second.png", self.root / "second.webp", 100, None, "Another failed conversion"),
        )
        dialog = ReportDialog(BatchReport(files, self.root, cancelled=True))
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.table.currentRow(), 2)
        self.assertEqual(dialog.details.toPlainText(), "The first failed conversion")
        self.assertFalse(dialog.compare_button.isEnabled())
        self.assertIn("2 failed", dialog.findChild(QLabel, "reportSummary").text())

    def test_report_scrolls_to_initial_failure_in_a_long_batch(self):
        files = tuple(FileResult(self.root / f"{row}.png", self.root / f"{row}.webp", 100, 40) for row in range(50))
        failure = FileResult(self.root / "failed.png", self.root / "failed.webp", 100, None, "Could not encode this file")
        dialog = ReportDialog(BatchReport(files + (failure,), self.root))
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.table.currentRow(), 50)
        self.assertEqual(dialog.details.toPlainText(), failure.error)
        self.assertGreater(dialog.table.verticalScrollBar().value(), 0)
        self.assertTrue(dialog.table.viewport().rect().intersects(dialog.table.visualItemRect(dialog.table.item(50, 0))))

    def test_report_opens_on_stopped_file_or_first_success_and_leaves_empty_report_blank(self):
        good = FileResult(self.root / "good.png", self.root / "good.webp", 100, 40)
        cancelled = FileResult(self.root / "cancelled.png", self.root / "cancelled.webp", 100, None, "Processing cancelled", stopped="Cancelled")
        skipped = FileResult(self.root / "skipped.png", self.root / "skipped.webp", 100, None, "Not processed", stopped="Skipped")
        cases = (
            ((good, cancelled, skipped), True, 1, "Processing cancelled"),
            ((good,), False, 0, str(good.output)),
            ((), False, -1, ""),
        )
        for files, was_cancelled, row, details in cases:
            with self.subTest(row=row):
                dialog = ReportDialog(BatchReport(files, self.root, cancelled=was_cancelled))
                self.addCleanup(dialog.close)
                self.assertEqual(dialog.table.currentRow(), row)
                self.assertEqual(dialog.details.toPlainText(), details)

    def test_report_headline_explains_all_failed_and_cancelled_batches(self):
        bad = FileResult(self.root / "bad.mov", self.root / "bad.mp4", 100, None, "Video encoding failed", media_type="video")
        cancelled = FileResult(self.root / "cancelled.mov", self.root / "cancelled.mp4", 100, None, "Processing cancelled", stopped="Cancelled", media_type="video")
        cases = (
            ((bad,), False, "Processing failed — no files saved"),
            ((cancelled,), True, "Processing cancelled — no files saved"),
            ((), False, "No files processed"),
        )
        for files, was_cancelled, headline in cases:
            with self.subTest(headline=headline):
                dialog = ReportDialog(BatchReport(files, self.root, cancelled=was_cancelled))
                self.addCleanup(dialog.close)
                self.assertEqual(dialog.findChild(QLabel, "reportSummary").text(), headline)

    def test_binary_sizes_use_unambiguous_units_in_summary_and_table(self):
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(1023), "1023 B")
        self.assertEqual(human_size(1024), "1.0 KiB")
        self.assertEqual(human_size(1024 ** 2), "1.0 MiB")
        self.assertEqual(human_size(1024 ** 3), "1.0 GiB")
        dialog = ReportDialog(BatchReport((FileResult(self.root / "one.png", self.root / "one.webp", 1024 ** 2, 1024),), self.root))
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.table.item(0, 2).text(), "1.0 MiB")
        self.assertEqual(dialog.table.item(0, 3).text(), "1.0 KiB")
        self.assertIn("1.0 MiB → 1.0 KiB", dialog.findChild(QLabel, "reportSummary").text())

    def test_worker_captures_original_size_before_conversion_and_reports_failures(self):
        jobs = []
        for index in range(3):
            source = self.root / f"source {index}.png"
            source.write_bytes(b"x" * 100)
            output = self.root / f"result {index}.webp"
            jobs.append((["magick", str(source), str(output)], output))

        def convert(command, **kwargs):
            source, output = Path(command[1]), Path(command[-1])
            if source.name == "source 0.png":
                source.write_bytes(b"x" * 500)
                output.write_bytes(b"x" * 20)
                return SimpleNamespace(returncode=0, stderr="", stdout="")
            if source.name == "source 1.png":
                output.write_bytes(b"partial output")
                return SimpleNamespace(returncode=1, stderr="Cannot encode", stdout="")
            raise subprocess.TimeoutExpired(command, 300)

        worker = BatchWorker(jobs, self.root)
        reports = []
        worker.finished.connect(reports.append)
        with patch("pixelkit.app.run_magick", side_effect=convert):
            worker.run()
        report = reports[0]
        self.assertEqual(report.before, 100)
        self.assertEqual(report.after, 20)
        self.assertEqual(len(report.files), 3)
        self.assertEqual(report.files[1].error, "Cannot encode")
        self.assertIsNone(report.files[1].after)
        self.assertIn("5-minute", report.files[2].error)

    def test_missing_source_is_a_failed_result_and_processing_continues(self):
        worker = BatchWorker([(["magick", str(self.root / "missing.png")], self.root / "output.webp")], self.root)
        reports = []
        worker.finished.connect(reports.append)
        with patch("pixelkit.app.run_magick") as run:
            worker.run()
            run.assert_not_called()
        self.assertFalse(reports[0].files[0].succeeded)
        self.assertEqual(reports[0].before, 0)

    def test_thread_delivers_one_report_to_the_ui(self):
        source = self.root / "source.png"
        source.write_bytes(b"source")
        output = self.root / "result.webp"
        output.write_bytes(b"out")
        worker = BatchWorker([(["magick", str(source), str(output)], output)], self.root)
        reports = []
        worker.finished.connect(reports.append)
        def convert(command, **kwargs):
            Path(command[-1]).write_bytes(b"out")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        with patch("pixelkit.app.run_magick", side_effect=convert):
            worker.start()
            self.assertTrue(worker.wait(5000))
            self.app.processEvents()
        self.assertEqual(len(reports), 1)
        self.assertIsInstance(reports[0], BatchReport)
        self.assertEqual(reports[0].after, 3)

    def test_report_shows_each_result_full_error_and_opens_folder_with_spaces(self):
        error = "Encoding error\n" * 100
        folder = self.root / "output folder"
        folder.mkdir()
        report = BatchReport((FileResult(self.root / "bad.png", folder / "out.webp", 100, None, error),), folder)
        dialog = ReportDialog(report)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 3).text(), "—")
        self.assertEqual(dialog.table.item(0, 5).text(), "Failed")
        dialog.table.selectRow(0)
        self.assertEqual(dialog.details.toPlainText(), error)
        with patch("pixelkit.report.QDesktopServices.openUrl", return_value=False) as open_url:
            dialog.open_folder.click()
        self.assertEqual(open_url.call_args.args[0].toLocalFile(), str(folder.resolve()))
        self.assertIn(str(folder), dialog.details.toPlainText())

    def test_missing_output_folder_disables_open_button(self):
        dialog = ReportDialog(BatchReport((), self.root / "missing"))
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.open_folder.isEnabled())

    def test_known_image_errors_show_recovery_and_preserve_optional_diagnostics(self):
        cases = (
            ("magick: improper image header `bad.png' @ error/png.c/ReadPNGImage/3956.", "damaged or incomplete", "export a fresh copy"),
            ("magick: no decode delegate for this image format `UNKNOWN' @ error/constitute.c/ReadImage/587.", "cannot read this image format", "Export the image as PNG or JPEG"),
            ("magick: no encode delegate for this image format `UNKNOWN' @ error/constitute.c/WriteImage/1301.", "cannot save this image format", "Choose PNG or JPEG"),
            ("magick: unable to open image `private.png': Permission denied @ error/blob.c/OpenBlob/3596.", "could not access", "original image is readable"),
            ("[WinError 5] Access is denied: 'private.png'", "could not access", "output folder is writable"),
            ("[Errno 30] Read-only file system: 'out.png'", "could not access", "output folder is writable"),
            ("[Errno 2] No such file or directory: 'gone.png'", "could not be found", "still exist"),
        )
        for error, cause, recovery in cases:
            with self.subTest(error=error):
                file = FileResult(self.root / "bad.png", self.root / "bad.jpg", 100, None, error)
                dialog = ReportDialog(BatchReport((file,), self.root))
                self.addCleanup(dialog.close)
                dialog.table.selectRow(0)
                self.assertIn(cause, dialog.details.toPlainText())
                self.assertIn(recovery, dialog.details.toPlainText())
                self.assertNotIn(error, dialog.details.toPlainText())
                self.assertFalse(dialog.technical_details_button.isHidden())
                self.assertEqual(dialog.technical_details_button.text(), "Show technical details")
                dialog.technical_details_button.click()
                self.assertIn("Technical details:\n" + error, dialog.details.toPlainText())
                self.assertEqual(dialog.technical_details_button.text(), "Hide technical details")
                self.assertEqual(file.error, error)
                dialog.technical_details_button.click()
                self.assertNotIn(error, dialog.details.toPlainText())

    def test_unknown_video_and_cleanup_errors_stay_complete_and_selection_resets_details(self):
        known_error = "magick: improper image header `bad.png' @ error/png.c/ReadPNGImage/3956."
        unknown_error = "Encoding error\n" * 100
        cleanup_error = known_error + "\nCould not remove temporary files at /tmp/output: Permission denied"
        files = (
            FileResult(self.root / "bad.png", self.root / "bad.jpg", 100, None, known_error),
            FileResult(self.root / "unknown.png", self.root / "unknown.jpg", 100, None, unknown_error),
            FileResult(self.root / "bad.mov", self.root / "bad.mp4", 100, None, known_error, media_type="video"),
            FileResult(self.root / "cleanup.png", self.root / "cleanup.jpg", 100, None, cleanup_error),
        )
        dialog = ReportDialog(BatchReport(files, self.root))
        self.addCleanup(dialog.close)
        dialog.table.selectRow(0)
        dialog.technical_details_button.click()
        for row in range(1, len(files)):
            dialog.table.selectRow(row)
            self.assertEqual(dialog.details.toPlainText(), files[row].error)
            self.assertTrue(dialog.technical_details_button.isHidden())
            self.assertFalse(dialog.technical_details_button.isChecked())
        dialog.table.selectRow(0)
        self.assertNotIn(known_error, dialog.details.toPlainText())
        self.assertFalse(dialog.technical_details_button.isChecked())
        dialog.table.clearSelection()
        self.assertEqual(dialog.details.toPlainText(), "")
        self.assertEqual(dialog.details.toolTip(), "")
        self.assertTrue(dialog.technical_details_button.isHidden())

    def test_technical_details_keep_keyboard_focus_for_repeated_toggles(self):
        error = "magick: improper image header `bad.png' @ error/png.c/ReadPNGImage/3956."
        file = FileResult(self.root / "bad.png", self.root / "bad.jpg", 100, None, error)
        dialog = ReportDialog(BatchReport((file,), self.root))
        self.addCleanup(dialog.close)
        dialog.show()
        dialog.table.selectRow(0)
        button = dialog.technical_details_button
        button.setFocus()
        self.app.processEvents()
        QTest.keyClick(button, Qt.Key.Key_Space)
        self.assertTrue(button.isChecked())
        self.assertIs(self.app.focusWidget(), button)
        self.assertTrue(dialog.details.toPlainText().startswith("Technical details:\n" + error))
        QTest.keyClick(self.app.focusWidget(), Qt.Key.Key_Space)
        self.assertFalse(button.isChecked())
        self.assertIs(self.app.focusWidget(), button)
        self.assertNotIn(error, dialog.details.toPlainText())

    def test_video_report_shows_decimal_limit_and_exact_actual_bytes(self):
        good = FileResult(self.root / "one.mov", self.root / "one.mp4", 2_000_000, 995_123, media_type="video", elapsed_seconds=2.0, target_bytes=1_000_000)
        failed = FileResult(self.root / "two.mov", self.root / "two.mp4", 2_000_000, None, "Cannot fit this video", media_type="video", target_bytes=1_000_000)
        unlimited = FileResult(self.root / "three.mov", self.root / "three.mp4", 2_000_000, 900_000, media_type="video")
        dialog = ReportDialog(BatchReport((good, failed, unlimited), self.root))
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.table.columnCount(), 8)
        self.assertEqual(dialog.table.horizontalHeaderItem(7).text(), "Limit")
        self.assertEqual(dialog.table.item(0, 7).text(), "1 MB")
        self.assertEqual(dialog.table.item(2, 7).text(), "—")
        dialog.table.selectRow(0)
        self.assertIn("Limit: 1 MB (1,000,000 bytes)", dialog.details.toPlainText())
        self.assertIn("Actual size: 0.995123 MB (995,123 bytes)", dialog.details.toPlainText())
        dialog.table.selectRow(1)
        self.assertIn("Cannot fit", dialog.details.toPlainText())
        self.assertIn("1,000,000 bytes", dialog.details.toPlainText())
        self.assertNotIn("Actual size", dialog.details.toPlainText())


if __name__ == "__main__":
    unittest.main()
