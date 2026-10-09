from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from pixelkit import __version__
from pixelkit.app import ImageMagickStudio
from pixelkit.presets import PresetStore


class RecordingUpdateDialog(QDialog):
    """Observe the main window's ownership and action wiring without HTTP."""

    checkingChanged = pyqtSignal(bool)

    def __init__(self, current_version, parent=None):
        super().__init__(parent)
        self.current_version = current_version
        self._checking = False
        self.requests = 0
        self.cancellations = 0

    @property
    def is_checking(self):
        return self._checking

    def check_for_updates(self):
        if self._checking:
            return
        self.requests += 1
        self._checking = True
        self.checkingChanged.emit(True)

    def complete(self):
        self._checking = False
        self.checkingChanged.emit(False)

    def cancel_check(self):
        if self._checking:
            self.cancellations += 1
            self.complete()

    def closeEvent(self, event):
        self.cancel_check()
        super().closeEvent(event)


class UpdateIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.settings = QSettings(str(self.root / "preferences.ini"), QSettings.Format.IniFormat)
        dialog_patch = patch("pixelkit.app.UpdateDialog", RecordingUpdateDialog)
        dialog_patch.start()
        self.addCleanup(dialog_patch.stop)
        with patch("pixelkit.app.find_magick", return_value="magick"):
            self.window = ImageMagickStudio(PresetStore(self.settings))
        self.addCleanup(self.window.close)

    def test_startup_does_not_construct_or_start_update_check(self):
        self.window.show()
        self.app.processEvents()
        self.assertIsNone(self.window._update_dialog)
        self.assertTrue(self.window.check_updates_action.isEnabled())

    def test_help_action_exists_on_both_platforms_without_native_menu_relocation(self):
        self.assertEqual(self.window.check_updates_action.menuRole(), QAction.MenuRole.NoRole)
        self.assertIn(self.window.check_updates_action, self.window.menuBar().actions()[-1].menu().actions())
        with (
            patch("pixelkit.app.sys.platform", "win32"),
            patch("pixelkit.app.find_magick", return_value="magick"),
            patch("pixelkit.video_panel.find_ffmpeg", return_value=None),
            patch("pixelkit.video_panel.find_ffprobe", return_value=None),
        ):
            windows = ImageMagickStudio(PresetStore(QSettings(str(self.root / "windows.ini"), QSettings.Format.IniFormat)))
        self.addCleanup(windows.close)
        self.assertEqual([action.text() for action in windows.menuBar().actions()], ["Help"])
        self.assertIn(windows.check_updates_action, windows.menuBar().actions()[0].menu().actions())
        self.assertEqual(windows.check_updates_action.menuRole(), QAction.MenuRole.NoRole)

    def test_manual_check_reuses_owned_dialog_and_avoids_duplicate_requests(self):
        self.window.check_updates_action.trigger()
        dialog = self.window._update_dialog
        self.assertIsInstance(dialog, RecordingUpdateDialog)
        self.assertIs(dialog.parent(), self.window)
        self.assertEqual(dialog.current_version, __version__)
        self.assertEqual(dialog.requests, 1)
        self.assertFalse(self.window.check_updates_action.isEnabled())
        self.window._show_update_check()
        self.assertIs(self.window._update_dialog, dialog)
        self.assertEqual(dialog.requests, 1)
        dialog.complete()
        self.assertTrue(self.window.check_updates_action.isEnabled())
        self.window.check_updates_action.trigger()
        self.assertEqual(dialog.requests, 2)

    def test_closing_the_dialog_restores_action_and_can_reopen_it(self):
        self.window.check_updates_action.trigger()
        dialog = self.window._update_dialog
        dialog.close()
        self.assertEqual(dialog.cancellations, 1)
        self.assertTrue(self.window.check_updates_action.isEnabled())
        self.window.check_updates_action.trigger()
        self.assertIs(self.window._update_dialog, dialog)
        self.assertEqual(dialog.requests, 2)

    def test_check_preserves_queues_settings_destination_and_report(self):
        self.window.sources = [self.root / "image.png"]
        self.window.video_panel.sources = [self.root / "video.mov"]
        self.window.output_edit.setText(str(self.root / "image.webp"))
        self.window.video_panel.output_edit.setText(str(self.root / "video.mp4"))
        self.window.quality_slider.setValue(61)
        report = object()
        self.window.last_report = report
        before = (list(self.window.sources), list(self.window.video_panel.sources),
                  self.window.output_edit.text(), self.window.video_panel.output_edit.text(),
                  self.window.quality_slider.value(), self.settings.allKeys())
        self.window.check_updates_action.trigger()
        self.window._update_dialog.complete()
        self.assertEqual(before, (self.window.sources, self.window.video_panel.sources,
                                 self.window.output_edit.text(), self.window.video_panel.output_edit.text(),
                                 self.window.quality_slider.value(), self.settings.allKeys()))
        self.assertIs(self.window.last_report, report)

    def test_update_check_keeps_active_onboarding_page_and_dismissal_state(self):
        self.window.show()
        self.window._show_onboarding()
        page = self.window._onboarding_page
        self.assertIsNotNone(page)
        page._show_page(1)
        self.window.check_updates_action.trigger()
        self.window._update_dialog.complete()
        self.assertIs(self.window._onboarding_page, page)
        self.assertIs(self.window.content_stack.currentWidget(), page)
        self.assertEqual(page.page_stack.currentIndex(), 1)
        self.assertFalse(self.settings.value("onboarding/dismissed", False, type=bool))

    def test_accepted_main_window_close_cancels_check_and_prevents_new_checks(self):
        self.window.show()
        self.window.check_updates_action.trigger()
        dialog = self.window._update_dialog
        self.window.close()
        self.assertTrue(self.window._closing)
        self.assertFalse(dialog.isVisible())
        self.assertFalse(dialog.is_checking)
        self.assertEqual(dialog.cancellations, 1)
        self.assertFalse(self.window.check_updates_action.isEnabled())
        self.window._show_update_check()
        self.assertEqual(dialog.requests, 1)

    def test_processing_close_guard_keeps_update_request_owned_and_running(self):
        self.window.show()
        self.window.check_updates_action.trigger()
        dialog = self.window._update_dialog
        self.window.worker = Mock()
        self.window.worker.isRunning.return_value = True
        try:
            with patch.object(self.window, "_show_message", return_value=QMessageBox.StandardButton.Ok):
                self.window.close()
            self.assertFalse(self.window._closing)
            self.assertTrue(dialog.is_checking)
            self.assertEqual(dialog.cancellations, 0)
        finally:
            self.window.worker = None


if __name__ == "__main__":
    unittest.main()
