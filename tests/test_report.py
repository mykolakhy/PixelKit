from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
from pixelkit.app import BatchWorker
from pixelkit.report import BatchReport, FileResult, ReportDialog, size_change


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


if __name__ == "__main__":
    unittest.main()
