from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QPushButton, QWidget

from pixelkit.presets import Preset
from pixelkit.report import BatchReport, FileResult, ReportDialog
from pixelkit.video import VideoSettings


class RetryReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary_root = "/private/tmp" if Path("/private/tmp").is_dir() else None
        self.directory = tempfile.TemporaryDirectory(prefix="pixelkit-retry-report-", dir=temporary_root)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.settings = Preset(
            resize_mode="longest_side", longest_side=1536, keep_ratio=False,
            quality=71, strip_metadata=False, background="#112233",
            output_format="WEBP", target_kib=640,
        )
        self.failed = FileResult(
            self.root / "failed.png", self.root / "failed.webp", 100, None,
            "magick: improper image header",
            processing_settings=(("Quality", "display-only value"),),
        )
        self.good = FileResult(self.root / "good.png", self.root / "good.webp", 100, 40)
        self.stopped = FileResult(
            self.root / "cancelled.png", self.root / "cancelled.webp", 100, None,
            "Processing cancelled", stopped="Cancelled",
        )

    def dialog(self, report, *, connected=True, parent=None):
        dialog = ReportDialog(report, parent)
        self.addCleanup(dialog.close)
        requests = []
        if connected:
            dialog.retry_requested.connect(requests.append)
        dialog.show()
        self.app.processEvents()
        return dialog, requests

    def test_retry_snapshot_and_report_are_immutable_and_positional_calls_stay_valid(self):
        legacy = BatchReport((self.failed,), self.root, True)
        self.assertTrue(legacy.cancelled)
        self.assertIsNone(legacy.retry_settings)
        report = BatchReport((self.failed,), self.root, retry_settings=self.settings)
        self.assertIs(report.retry_settings, self.settings)
        with self.assertRaises(FrozenInstanceError):
            report.retry_settings = Preset()
        with self.assertRaises(FrozenInstanceError):
            report.retry_settings.quality = 82

    def test_image_retry_click_emits_original_report_once_without_closing_or_external_actions(self):
        report = BatchReport((self.failed,), self.root, retry_settings=self.settings)
        dialog, requests = self.dialog(report)
        original_details = dialog.details.toPlainText()
        with patch("pixelkit.report.QApplication.clipboard") as clipboard, patch("pixelkit.report.QDesktopServices.openUrl") as browser, patch("pixelkit.report.QFileDialog.getSaveFileName") as save_file, patch("pixelkit.report.subprocess.Popen") as process, patch("pixelkit.report.ComparisonDialog") as compare, patch("pixelkit.report.BugReportDialog") as bug_dialog:
            QTest.mouseClick(dialog.retry_button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(requests), 1)
        self.assertIs(requests[0], report)
        self.assertIs(requests[0].retry_settings, self.settings)
        self.assertEqual(requests[0].files[0].processing_settings, (("Quality", "display-only value"),))
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(dialog.details.toPlainText(), original_details)
        for action in (clipboard, browser, save_file, process, compare, bug_dialog):
            action.assert_not_called()

    def test_video_retry_keeps_original_frozen_settings_including_size_limit(self):
        settings = VideoSettings(preset="high", max_height=720, audio="remove", target_bytes=3_456_789)
        failed = FileResult(self.root / "failed.mov", self.root / "failed.mp4", 100, None, "Encode failed", media_type="video")
        report = BatchReport((failed,), self.root, retry_settings=settings)
        dialog, requests = self.dialog(report)
        QTest.mouseClick(dialog.retry_button, Qt.MouseButton.LeftButton)
        self.assertIs(requests[0], report)
        self.assertIs(requests[0].retry_settings, settings)
        self.assertEqual(requests[0].retry_settings.target_bytes, 3_456_789)
        with self.assertRaises(FrozenInstanceError):
            requests[0].retry_settings.audio = "keep"

    def test_retry_in_cancelled_batch_is_available_for_real_failures_regardless_of_selected_row(self):
        second_failure = FileResult(self.root / "other.png", self.root / "other.webp", 100, None, "Encode failed")
        report = BatchReport((self.good, self.stopped, self.failed, second_failure), self.root, cancelled=True, retry_settings=self.settings)
        dialog, requests = self.dialog(report)
        dialog.table.selectRow(0)
        self.assertTrue(dialog.retry_button.isEnabled())
        QTest.mouseClick(dialog.retry_button, Qt.MouseButton.LeftButton)
        self.assertIs(requests[0], report)
        self.assertEqual(requests[0].failed, (self.failed, second_failure))
        self.assertNotIn(self.stopped, requests[0].failed)
        self.assertNotIn(self.good, requests[0].failed)

    def test_without_real_failures_retry_is_hidden_and_cannot_emit(self):
        skipped = FileResult(self.root / "skipped.png", self.root / "skipped.webp", None, None, "Not processed", stopped="Skipped")
        for files, cancelled in (((), False), ((self.good,), False), ((self.stopped,), True), ((self.stopped, skipped), True), ((self.good, self.stopped, skipped), True)):
            with self.subTest(files=files):
                report = BatchReport(files, self.root, cancelled=cancelled, retry_settings=self.settings)
                dialog, requests = self.dialog(report)
                self.assertTrue(dialog.retry_button.isHidden())
                self.assertFalse(dialog.retry_button.isEnabled())
                dialog.set_retry_enabled(True)
                dialog.retry_button.click()
                dialog._request_retry()
                self.assertEqual(requests, [])

    def test_failure_without_snapshot_cannot_retry_or_reconstruct_settings_from_display(self):
        report = BatchReport((self.failed,), self.root)
        dialog, requests = self.dialog(report)
        self.assertTrue(dialog.retry_button.isHidden())
        self.assertFalse(dialog.retry_button.isEnabled())
        dialog.set_retry_enabled(True)
        dialog.retry_button.click()
        dialog._request_retry()
        self.assertEqual(requests, [])

    def test_standalone_dialog_requires_a_consumer_before_retry_can_be_enabled(self):
        report = BatchReport((self.failed,), self.root, retry_settings=self.settings)
        dialog, requests = self.dialog(report, connected=False)
        self.assertFalse(dialog.retry_button.isHidden())
        self.assertFalse(dialog.retry_button.isEnabled())
        dialog.set_retry_enabled(True)
        dialog.retry_button.click()
        dialog._request_retry()
        self.assertEqual(requests, [])
        dialog.retry_requested.connect(requests.append)
        dialog.set_retry_enabled(True)
        self.assertTrue(dialog.retry_button.isEnabled())
        QTest.mouseClick(dialog.retry_button, Qt.MouseButton.LeftButton)
        self.assertEqual(requests, [report])

    def test_disabled_retry_cannot_emit_and_controller_can_reenable_it(self):
        report = BatchReport((self.failed,), self.root, retry_settings=self.settings)
        dialog, requests = self.dialog(report)
        dialog.set_retry_enabled(False)
        self.assertFalse(dialog.retry_button.isEnabled())
        dialog.retry_button.click()
        dialog._request_retry()
        self.assertEqual(requests, [])
        self.assertTrue(dialog.isVisible())
        dialog.set_retry_enabled(True)
        QTest.mouseClick(dialog.retry_button, Qt.MouseButton.LeftButton)
        self.assertEqual(requests, [report])

    def test_retry_checks_consumer_again_if_disconnected_after_showing(self):
        report = BatchReport((self.failed,), self.root, retry_settings=self.settings)
        dialog, requests = self.dialog(report)
        dialog.retry_requested.disconnect()
        dialog.retry_button.click()
        dialog._request_retry()
        self.assertEqual(requests, [])
        self.assertTrue(dialog.isVisible())

    def test_controller_owns_accepting_dialog_after_successful_queue_preparation(self):
        report = BatchReport((self.failed,), self.root, retry_settings=self.settings)
        dialog, requests = self.dialog(report)
        dialog.retry_requested.connect(lambda _: dialog.accept())
        QTest.mouseClick(dialog.retry_button, Qt.MouseButton.LeftButton)
        self.assertEqual(requests, [report])
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertFalse(dialog.isVisible())

    def test_retry_can_be_invoked_with_keyboard(self):
        report = BatchReport((self.failed,), self.root, retry_settings=self.settings)
        dialog, requests = self.dialog(report)
        dialog.retry_button.setFocus()
        self.app.processEvents()
        QTest.keyClick(dialog.retry_button, Qt.Key.Key_Space)
        self.assertEqual(requests, [report])
        self.assertTrue(dialog.isVisible())

    def test_retry_and_existing_actions_fit_standard_and_minimum_report_sizes(self):
        parent = QWidget()
        self.addCleanup(parent.close)
        # Use the application's inherited button sizing without creating a main
        # window, reading preferences, or touching its processing queues.
        parent.setStyleSheet("QWidget { font-size: 13px; } QPushButton { border: 1px solid gray; border-radius: 9px; padding: 9px 13px; font-weight: 600; }")
        report = BatchReport((self.failed, self.good), self.root, retry_settings=self.settings)
        dialog, _ = self.dialog(report, parent=parent)
        dialog.export_status.setText("Copied")
        close = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Close")
        widgets = (dialog.copy_error_button, dialog.save_error_button, dialog.technical_details_button, dialog.report_bug_button, dialog.compare_button, dialog.open_folder, dialog.retry_button, close)
        for width, height in ((820, 520), (660, 380)):
            with self.subTest(size=(width, height)):
                dialog.resize(width, height)
                self.app.processEvents()
                self.assertEqual((dialog.width(), dialog.height()), (width, height))
                for widget in widgets:
                    self.assertFalse(widget.visibleRegion().isEmpty(), widget.text())
                    self.assertTrue(dialog.rect().contains(widget.mapTo(dialog, QPoint(0, 0))), widget.text())
                    self.assertTrue(dialog.rect().contains(widget.mapTo(dialog, QPoint(widget.width() - 1, widget.height() - 1))), widget.text())
                    self.assertGreaterEqual(widget.width(), widget.minimumSizeHint().width(), widget.text())
                self.assertGreater(dialog.table.viewport().height(), 20)


if __name__ == "__main__":
    unittest.main()
