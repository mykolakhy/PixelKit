from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPoint, QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea, QWidget

from pixelkit.app import ImageMagickStudio
from pixelkit.onboarding import OnboardingPage
from pixelkit.presets import PresetStore


class OnboardingPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = QSettings(str(Path(directory.name) / "presets.ini"), QSettings.Format.IniFormat)
        with patch("pixelkit.app.find_magick", return_value="magick"):
            self.window = ImageMagickStudio(PresetStore(settings))
        self.addCleanup(self.window.close)
        self.page = OnboardingPage(self.window.content_stack)
        self.window.content_stack.addWidget(self.page)
        self.window.content_stack.setCurrentWidget(self.page)
        self.finished = []
        self.page.finished.connect(self.finished.append)
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()

    def page_text(self):
        return " ".join(label.text() for label in self.page.page_stack.currentWidget().findChildren(QLabel))

    def assert_contents_fit(self):
        """The introduction fills the host, and all its content fits without scrolling."""
        self.assertFalse(self.page.isWindow())
        self.assertIs(self.page.window(), self.window)
        self.assertEqual(self.page.geometry(), self.window.content_stack.contentsRect())
        self.assertFalse(self.page.findChildren(QScrollArea))
        for widget in self.page.findChildren(QWidget):
            if not widget.isVisible():
                continue
            with self.subTest(widget=widget.objectName() or type(widget).__name__):
                top_left = widget.mapTo(self.page, QPoint(0, 0))
                bottom_right = widget.mapTo(self.page, QPoint(widget.width() - 1, widget.height() - 1))
                self.assertTrue(self.page.rect().contains(top_left))
                self.assertTrue(self.page.rect().contains(bottom_right))
                self.assertGreater(widget.width(), 0)
                self.assertGreater(widget.height(), 0)
                if isinstance(widget, QLabel):
                    self.assertGreaterEqual(widget.height(), widget.heightForWidth(widget.width()))
                if isinstance(widget, QPushButton):
                    self.assertGreaterEqual(widget.width(), widget.minimumSizeHint().width())
                    self.assertGreaterEqual(widget.height(), widget.minimumSizeHint().height())
        margins = self.page.layout().contentsMargins()
        footer_bottom = self.page.next_button.mapTo(self.page, QPoint(0, self.page.next_button.height())).y()
        self.assertEqual(footer_bottom, self.page.height() - margins.bottom())

    def test_introduction_is_embedded_and_explains_supported_work(self):
        self.assertFalse(self.page.isWindow())
        self.assertEqual(self.page.page_stack.count(), 2)
        self.assertIn("Resize and convert images", self.page_text())
        self.assertIn("Compress images and videos", self.page_text())
        self.assertIn("file-size limit", self.page_text())
        self.assertIn("Processed on your computer. Originals stay untouched.", self.page_text())
        self.assertIn("Save a new result", self.page_text())
        self.assertEqual(self.page.progress_label.text(), "Step 1 of 2")
        self.assertTrue(self.page.back_button.isHidden())
        self.assertEqual(self.page.next_button.text(), "Next")
        self.assert_contents_fit()

    def test_next_and_back_change_pages_without_finishing(self):
        self.assertIs(self.app.focusWidget(), self.page.next_button)
        self.page.next_button.click()
        self.app.processEvents()
        self.assertIn("Start with your first file", self.page_text())
        for action in ("Add file", "Choose settings", "Save result"):
            self.assertIn(action, self.page_text())
        self.assertEqual(self.page.next_button.text(), "Add first file…")
        self.assertEqual(self.page.progress_label.text(), "Step 2 of 2")
        self.assertFalse(self.page.back_button.isHidden())
        self.page.back_button.click()
        self.app.processEvents()
        self.assertEqual(self.page.page_stack.currentIndex(), 0)
        self.assertTrue(self.page.isVisible())
        self.assertEqual(self.finished, [])
        self.assertIs(self.app.focusWidget(), self.page.next_button)

    def test_return_advances_and_enter_requests_a_file_once(self):
        QTest.keyClick(self.page.next_button, Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertEqual(self.page.page_stack.currentIndex(), 1)
        self.assertEqual(self.finished, [])
        QTest.keyClick(self.page.next_button, Qt.Key.Key_Enter)
        self.assertEqual(self.finished, [True])
        self.page.next_button.click()
        self.page.dismiss()
        self.assertEqual(self.finished, [True])
        self.assertTrue(self.window.isVisible())

    def test_return_activates_focused_back_or_skip_instead_of_next(self):
        self.page.next_button.click()
        self.page.back_button.setFocus()
        QTest.keyClick(self.page.back_button, Qt.Key.Key_Return)
        self.assertEqual(self.page.page_stack.currentIndex(), 0)
        self.assertEqual(self.finished, [])
        self.page.skip_button.setFocus()
        QTest.keyClick(self.page.skip_button, Qt.Key.Key_Return)
        self.assertEqual(self.finished, [False])

    def test_return_on_the_page_itself_activates_next(self):
        self.page.setFocus()
        QTest.keyClick(self.page, Qt.Key.Key_Return)
        self.assertEqual(self.page.page_stack.currentIndex(), 1)
        self.assertEqual(self.finished, [])

    def test_skip_dismiss_and_escape_emit_false_once(self):
        for index, action in ((0, "skip"), (1, "skip"), (0, "escape"), (1, "escape"), (1, "dismiss")):
            with self.subTest(page=index, action=action):
                page = OnboardingPage(self.window.content_stack)
                self.window.content_stack.addWidget(page)
                self.window.content_stack.setCurrentWidget(page)
                page._show_page(index)
                results = []
                page.finished.connect(results.append)
                self.app.processEvents()
                if action == "skip":
                    page.skip_button.click()
                elif action == "escape":
                    button = page.back_button if index else page.next_button
                    button.setFocus()
                    QTest.keyClick(button, Qt.Key.Key_Escape)
                else:
                    page.dismiss()
                self.assertEqual(results, [False])
                page.dismiss()
                page.next_button.click()
                self.assertEqual(results, [False])
                self.assertTrue(self.window.isVisible())
                self.window.content_stack.removeWidget(page)
                page.deleteLater()
        self.window.content_stack.setCurrentWidget(self.page)

    def test_finishing_does_not_hide_or_remove_content_owned_by_the_controller(self):
        self.page.dismiss()
        self.assertEqual(self.finished, [False])
        self.assertIs(self.window.content_stack.currentWidget(), self.page)
        self.assertTrue(self.page.isVisible())

    def test_showing_an_existing_page_keeps_its_progress(self):
        self.page.next_button.click()
        self.window.content_stack.setCurrentWidget(self.window.workspace_page)
        self.window.content_stack.setCurrentWidget(self.page)
        self.app.processEvents()
        self.assertEqual(self.page.page_stack.currentIndex(), 1)
        self.assertIs(self.app.focusWidget(), self.page.next_button)
        self.assertEqual(self.finished, [])

    def test_keyboard_tab_order_reaches_all_visible_actions(self):
        for index, expected in (
            (0, (self.page.skip_button, self.page.next_button)),
            (1, (self.page.skip_button, self.page.back_button, self.page.next_button)),
        ):
            with self.subTest(page=index):
                self.page._show_page(index)
                self.app.processEvents()
                for button in expected:
                    QTest.keyClick(self.app.focusWidget(), Qt.Key.Key_Tab)
                    self.assertIs(self.app.focusWidget(), button)

    def test_pages_actions_and_illustrations_have_accessible_text(self):
        self.assertIn("Escape", self.page.accessibleDescription())
        for index in (0, 1):
            self.page._show_page(index)
            self.app.processEvents()
            self.assertIn(f"Step {index + 1} of 2", self.page.progress_label.accessibleName())
            self.assertTrue(self.page.page_stack.currentWidget().accessibleName())
            self.assertTrue(self.page.page_stack.accessibleDescription())
            for button in (self.page.skip_button, self.page.back_button, self.page.next_button):
                self.assertTrue(button.accessibleName())
                self.assertTrue(button.accessibleDescription())
            illustrations = [widget for widget in self.page.page_stack.currentWidget().findChildren(QWidget)
                             if type(widget).__name__ == "_Illustration"]
            self.assertTrue(illustrations)
            for illustration in illustrations:
                self.assertTrue(illustration.accessibleName())
                self.assertTrue(illustration.accessibleDescription())
                self.assertEqual(illustration.focusPolicy(), Qt.FocusPolicy.NoFocus)

    def test_both_pages_fill_minimum_default_enlarged_and_maximized_windows(self):
        for width, height in ((1040, 620), (1100, 780), (1400, 1000)):
            self.window.resize(width, height)
            self.app.processEvents()
            self.assertEqual((self.window.width(), self.window.height()), (width, height))
            for index in (0, 1):
                with self.subTest(size=(width, height), page=index):
                    self.page._show_page(index)
                    self.app.processEvents()
                    self.assert_contents_fit()
        self.window.showMaximized()
        self.app.processEvents()
        # At 2x scale, Qt's 800px offscreen test screen becomes only 400
        # logical pixels tall, and its fake window manager ignores minimum
        # size while maximizing. The real app requires a 620px-tall host.
        if self.window.height() < self.window.minimumHeight():
            return
        for index in (0, 1):
            self.page._show_page(index)
            self.app.processEvents()
            self.assert_contents_fit()

    def test_real_parent_stylesheet_preserves_type_hierarchy_at_all_sizes(self):
        for width, height in ((1040, 620), (1400, 1000)):
            self.window.resize(width, height)
            self.app.processEvents()
            for index in (0, 1):
                self.page._show_page(index)
                self.app.processEvents()
                heading = self.page.page_stack.currentWidget().findChild(QLabel, "onboardingHeading")
                body = self.page.page_stack.currentWidget().findChild(QLabel, "onboardingBody")
                self.assertGreaterEqual(heading.font().pointSizeF(), 28)
                self.assertGreater(heading.fontMetrics().height(), body.fontMetrics().height())
                self.assertTrue(heading.font().bold())
                self.assert_contents_fit()


if __name__ == "__main__":
    unittest.main()
