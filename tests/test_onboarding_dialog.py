from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPoint, QSettings, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QScrollArea, QWidget

from pixelkit.onboarding import OnboardingDialog


class OnboardingDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dialog = OnboardingDialog()
        self.addCleanup(self.dialog.close)

    def show(self):
        self.dialog.show()
        self.app.processEvents()

    def page_text(self):
        return " ".join(label.text() for label in self.dialog.page_stack.currentWidget().findChildren(QLabel))

    def assert_contents_fit(self, dialog):
        """All visible children and wrapped text must fit without scrolling."""
        self.assertFalse(dialog.findChildren(QScrollArea))
        for widget in dialog.findChildren(QWidget):
            if not widget.isVisible():
                continue
            with self.subTest(widget=widget.objectName() or type(widget).__name__):
                top_left = widget.mapTo(dialog, QPoint(0, 0))
                bottom_right = widget.mapTo(dialog, QPoint(widget.width() - 1, widget.height() - 1))
                self.assertTrue(dialog.rect().contains(top_left))
                self.assertTrue(dialog.rect().contains(bottom_right))
                self.assertGreater(widget.width(), 0)
                self.assertGreater(widget.height(), 0)
                if isinstance(widget, QLabel):
                    self.assertGreaterEqual(widget.height(), widget.heightForWidth(widget.width()))
                if isinstance(widget, QPushButton):
                    self.assertGreaterEqual(widget.width(), widget.minimumSizeHint().width())
                    self.assertGreaterEqual(widget.height(), widget.minimumSizeHint().height())

    def test_pages_explain_supported_work_and_a_practical_first_file(self):
        self.show()
        self.assertEqual(self.dialog.page_stack.count(), 2)
        self.assertIn("Resize and convert images", self.page_text())
        self.assertIn("Compress images and videos", self.page_text())
        self.assertIn("file-size limit", self.page_text())
        self.assertIn("Processed on your computer. Originals stay untouched.", self.page_text())
        self.assertIn("Save a new result", self.page_text())
        self.assertEqual(self.dialog.progress_label.text(), "Step 1 of 2")
        self.assertTrue(self.dialog.back_button.isHidden())
        self.assertEqual(self.dialog.next_button.text(), "Next")

        self.dialog.next_button.click()
        self.app.processEvents()
        self.assertIn("Start with your first file", self.page_text())
        for action in ("Add file", "Choose settings", "Save result"):
            self.assertIn(action, self.page_text())
        self.assertEqual(self.dialog.next_button.text(), "Add first file…")
        self.assertEqual(self.dialog.progress_label.text(), "Step 2 of 2")
        self.assertFalse(self.dialog.back_button.isHidden())

    def test_next_and_back_change_pages_without_finishing_or_requesting_a_file(self):
        finished = []
        self.dialog.finished.connect(finished.append)
        self.show()
        self.assertIs(self.app.focusWidget(), self.dialog.next_button)
        self.dialog.next_button.click()
        self.dialog.back_button.click()
        self.app.processEvents()
        self.assertEqual(self.dialog.page_stack.currentIndex(), 0)
        self.assertTrue(self.dialog.isVisible())
        self.assertFalse(self.dialog.add_file_requested)
        self.assertEqual(finished, [])
        self.assertIs(self.app.focusWidget(), self.dialog.next_button)

    def test_return_advances_and_final_action_finishes_before_the_caller_chooses_a_file(self):
        observed = []
        self.dialog.finished.connect(lambda result: observed.append(
            (result, self.dialog.isVisible(), self.dialog.add_file_requested)
        ))

        def advance():
            QTest.keyClick(self.dialog, Qt.Key.Key_Return)
            self.assertEqual(self.dialog.page_stack.currentIndex(), 1)
            self.assertFalse(self.dialog.add_file_requested)
            QTest.keyClick(self.dialog, Qt.Key.Key_Return)

        QTimer.singleShot(0, advance)
        result = self.dialog.exec()
        self.assertEqual(result, QDialog.DialogCode.Accepted)
        self.assertTrue(self.dialog.add_file_requested)
        self.assertEqual(observed, [(QDialog.DialogCode.Accepted, False, True)])

    def test_skip_dismisses_either_page_without_a_file_request(self):
        for index in (0, 1):
            with self.subTest(page=index):
                self.dialog._show_page(index)
                self.show()
                self.dialog.skip_button.click()
                self.assertEqual(self.dialog.result(), QDialog.DialogCode.Rejected)
                self.assertFalse(self.dialog.isVisible())
                self.assertFalse(self.dialog.add_file_requested)

    def test_escape_dismisses_from_either_page_and_from_back_button_focus(self):
        for index in (0, 1):
            with self.subTest(page=index):
                self.dialog._show_page(index)
                self.show()
                if index == 1:
                    self.dialog.back_button.setFocus()
                QTest.keyClick(self.dialog, Qt.Key.Key_Escape)
                self.assertEqual(self.dialog.result(), QDialog.DialogCode.Rejected)
                self.assertFalse(self.dialog.isVisible())
                self.assertFalse(self.dialog.add_file_requested)

    def test_window_close_is_a_dismissal(self):
        rejected = []
        self.dialog.rejected.connect(lambda: rejected.append(True))
        self.show()
        self.dialog.next_button.click()
        self.dialog.close()
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Rejected)
        self.assertFalse(self.dialog.add_file_requested)
        self.assertEqual(rejected, [True])

    def test_keyboard_tab_order_reaches_all_visible_actions(self):
        self.show()
        for page, expected in (
            (0, (self.dialog.skip_button, self.dialog.next_button)),
            (1, (self.dialog.skip_button, self.dialog.back_button, self.dialog.next_button)),
        ):
            with self.subTest(page=page):
                self.dialog._show_page(page)
                self.app.processEvents()
                for button in expected:
                    QTest.keyClick(self.dialog, Qt.Key.Key_Tab)
                    self.assertIs(self.app.focusWidget(), button)

    def test_pages_and_actions_have_accessible_names_and_descriptions(self):
        self.show()
        self.assertIn("Escape", self.dialog.accessibleDescription())
        for index in (0, 1):
            self.dialog._show_page(index)
            self.app.processEvents()
            self.assertIn(f"Step {index + 1} of 2", self.dialog.progress_label.accessibleName())
            self.assertTrue(self.dialog.page_stack.currentWidget().accessibleName())
            self.assertTrue(self.dialog.page_stack.accessibleDescription())
            for button in (self.dialog.skip_button, self.dialog.back_button, self.dialog.next_button):
                self.assertTrue(button.accessibleName())
                self.assertTrue(button.accessibleDescription())
            illustrations = [widget for widget in self.dialog.page_stack.currentWidget().findChildren(QWidget)
                             if type(widget).__name__ == "_Illustration"]
            self.assertTrue(illustrations)
            for illustration in illustrations:
                self.assertTrue(illustration.accessibleName())
                self.assertTrue(illustration.accessibleDescription())
                self.assertEqual(illustration.focusPolicy(), Qt.FocusPolicy.NoFocus)

    def test_both_pages_fit_at_minimum_and_default_size_without_clipped_text(self):
        self.show()
        for size in (self.dialog.minimumSize(), self.dialog.size()):
            for index in (0, 1):
                with self.subTest(size=size, page=index):
                    self.dialog._show_page(index)
                    self.dialog.resize(size)
                    self.app.processEvents()
                    self.assert_contents_fit(self.dialog)
        self.assertLessEqual(self.dialog.width(), 680)
        self.assertLessEqual(self.dialog.height(), 500)

    def test_real_parent_stylesheet_preserves_type_hierarchy_and_minimum_layout(self):
        from pixelkit.app import ImageMagickStudio
        from pixelkit.presets import PresetStore

        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "presets.ini"), QSettings.Format.IniFormat)
            with patch("pixelkit.app.find_magick", return_value="magick"):
                parent = ImageMagickStudio(PresetStore(settings))
            self.addCleanup(parent.close)
            dialog = OnboardingDialog(parent)
            self.addCleanup(dialog.close)
            dialog.resize(dialog.minimumSize())
            dialog.show()
            self.app.processEvents()
            for index in (0, 1):
                dialog._show_page(index)
                self.app.processEvents()
                heading = dialog.page_stack.currentWidget().findChild(QLabel, "onboardingHeading")
                body = dialog.page_stack.currentWidget().findChild(QLabel, "onboardingBody")
                self.assertAlmostEqual(heading.font().pointSizeF(), 21, delta=.1)
                self.assertGreater(heading.fontMetrics().height(), body.fontMetrics().height())
                self.assertTrue(heading.font().bold())
                self.assert_contents_fit(dialog)
            dialog.close()
            parent.close()


if __name__ == "__main__":
    unittest.main()
