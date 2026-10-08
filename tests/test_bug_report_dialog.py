from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication, QLabel

from pixelkit.bug_report import BugReportContext, ISSUE_URL, compose_report
from pixelkit.bug_report_dialog import BugReportDialog
from pixelkit.report import FileResult


class BugReportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "private holiday.png"
        self.output = self.root / "private result.webp"
        self.source.write_bytes(b"source media")
        self.output.write_bytes(b"output media")
        self.context = BugReportContext(
            "Images",
            FileResult(self.source, self.output, 12, None, f"Unable to read '{self.source}'"),
            (self.source, self.output),
        )
        self.dialog = BugReportDialog(self.context)
        self.addCleanup(self.dialog.close)

    def complete(self):
        self.dialog.title_edit.setText("Processing fails — помилка")
        self.dialog.steps_edit.setPlainText("1. Select an image\n2. Process it")
        self.dialog.actual_edit.setPlainText("The image fails with an error")
        self.dialog.expected_edit.setPlainText("The image is saved")

    def show(self):
        self.dialog.show()
        self.app.processEvents()

    def assert_current_report(self, expected):
        self.assertEqual(self.dialog.report_text(), expected)
        self.assertEqual(self.dialog.preview.toPlainText(), expected)

    def test_automatic_diagnostics_are_sanitized_and_editable(self):
        details = self.dialog.technical_edit.toPlainText()
        self.assertIn("PixelKit version:", details)
        self.assertIn("Mode: Images", details)
        self.assertIn("Unable to read", details)
        self.assertNotIn(str(self.root), details)
        self.assertNotIn(self.source.name, details)
        self.assertNotIn(self.output.name, details)
        self.assertFalse(self.dialog.technical_edit.isReadOnly())
        notice = self.dialog.findChild(QLabel, "bugReportNotice").text()
        self.assertIn("public", notice)
        self.assertIn("GitHub account", notice)
        self.assertIn("No files are uploaded automatically", notice)

    def test_preview_copy_and_save_use_the_exact_edited_report(self):
        self.complete()
        technical = "User-edited diagnostics\nUnicode: Привіт 🌻\n```\nfull log\n```"
        self.dialog.technical_edit.setPlainText(technical)
        expected = compose_report(
            self.dialog.title_edit.text(), self.dialog.steps_edit.toPlainText(),
            self.dialog.actual_edit.toPlainText(), self.dialog.expected_edit.toPlainText(), technical,
        )
        self.dialog.tabs.setCurrentIndex(1)
        self.assert_current_report(expected)
        self.dialog.copy_button.click()
        self.assertEqual(self.app.clipboard().text(), expected)
        target = self.root / "report.md"
        with patch("pixelkit.bug_report_dialog.QFileDialog.getSaveFileName", return_value=(str(target), "Markdown files (*.md)")):
            self.dialog.save_button.click()
        self.assertEqual(target.read_bytes(), expected.encode("utf-8"))
        self.assert_current_report(expected)

    def test_all_actions_validate_only_when_attempted(self):
        self.assertTrue(all(label.isHidden() for label in self.dialog.field_errors.values()))
        self.show()
        self.app.clipboard().setText("clipboard marker")
        with patch("pixelkit.bug_report_dialog.QDesktopServices.openUrl") as browser, patch("pixelkit.bug_report_dialog.QFileDialog.getSaveFileName") as save:
            for button in (self.dialog.copy_button, self.dialog.save_button, self.dialog.github_button):
                with self.subTest(action=button.text()):
                    button.click()
                    self.app.processEvents()
                    self.assertEqual(self.app.focusWidget(), self.dialog.title_edit)
                    for editor, _ in self.dialog._required:
                        self.assertFalse(self.dialog.field_errors[editor].isHidden())
                        self.assertTrue(editor.property("invalid"))
            browser.assert_not_called()
            save.assert_not_called()
        self.assertEqual(self.app.clipboard().text(), "clipboard marker")
        self.assertIn("required fields", self.dialog.feedback.text())

    def test_whitespace_fields_fail_and_focus_the_first_missing_field(self):
        self.show()
        self.dialog.title_edit.setText("A valid title")
        self.dialog.steps_edit.setPlainText(" \n\t")
        self.dialog.actual_edit.setPlainText("What happened")
        self.dialog.tabs.setCurrentIndex(1)
        self.dialog.copy_button.click()
        self.app.processEvents()
        self.assertEqual(self.dialog.tabs.currentIndex(), 0)
        self.assertEqual(self.app.focusWidget(), self.dialog.steps_edit)
        self.assertTrue(self.dialog.field_errors[self.dialog.title_edit].isHidden())
        self.assertTrue(self.dialog.field_errors[self.dialog.actual_edit].isHidden())
        self.assertFalse(self.dialog.field_errors[self.dialog.steps_edit].isHidden())
        self.dialog.steps_edit.setPlainText("A reproducing step")
        self.assertTrue(self.dialog.field_errors[self.dialog.steps_edit].isHidden())
        self.dialog.copy_button.click()
        self.assertIn("Report copied", self.dialog.feedback.text())

    def test_expected_result_is_optional_but_other_fields_are_revalidated(self):
        self.complete()
        self.dialog.expected_edit.clear()
        with patch("pixelkit.bug_report_dialog.QDesktopServices.openUrl", return_value=True) as browser:
            self.dialog.github_button.click()
        browser.assert_called_once()
        self.dialog.actual_edit.clear()
        with patch("pixelkit.bug_report_dialog.QDesktopServices.openUrl") as browser:
            self.dialog.github_button.click()
        browser.assert_not_called()
        self.assertIn("what happened", self.dialog.field_errors[self.dialog.actual_edit].text())

    def test_short_report_opens_complete_fixed_issue_draft_without_publication(self):
        self.complete()
        self.dialog.technical_edit.setPlainText("Details with & ? #\nПривіт")
        expected = self.dialog.report_text()
        with patch("pixelkit.bug_report_dialog.QDesktopServices.openUrl", return_value=True) as browser:
            self.dialog.github_button.click()
        url = browser.call_args.args[0].toString()
        parts = urlsplit(url)
        self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", ISSUE_URL)
        query = parse_qs(parts.query)
        self.assertEqual(query["title"], [self.dialog.title_edit.text()])
        self.assertEqual(query["body"], [expected])
        self.assertIn("Review it there, then submit", self.dialog.feedback.text())
        self.assertNotIn("sent", self.dialog.feedback.text().lower())
        self.assertNotIn("published", self.dialog.feedback.text().lower())

    def test_long_unicode_report_is_copied_in_full_and_uses_short_title_link(self):
        self.complete()
        self.dialog.technical_edit.setPlainText("Помилка 🌻\n" * 1000)
        expected = self.dialog.report_text()
        self.assertEqual(self.dialog.github_button.text().replace("&&", "&"), "Copy report & open GitHub")
        self.assertFalse(self.dialog.route_notice.isHidden())
        with patch("pixelkit.bug_report_dialog.QDesktopServices.openUrl", return_value=True) as browser:
            self.dialog.github_button.click()
        query = parse_qs(urlsplit(browser.call_args.args[0].toString()).query, keep_blank_values=True)
        self.assertEqual(query["title"], [self.dialog.title_edit.text()])
        self.assertEqual(query["body"], [""])
        self.assertEqual(self.app.clipboard().text(), expected)
        self.assertIn("Paste it into the issue body", self.dialog.feedback.text())
        self.assert_current_report(expected)
        self.dialog.technical_edit.setPlainText("Short again")
        self.assertEqual(self.dialog.github_button.text(), "Continue on GitHub")
        self.assertTrue(self.dialog.route_notice.isHidden())

    def test_browser_failure_and_retry_keep_current_diagnostics_and_preview(self):
        self.complete()
        self.dialog.technical_edit.setPlainText("Entire edited log\nПривіт")
        expected = self.dialog.report_text()
        with patch("pixelkit.bug_report_dialog.QDesktopServices.openUrl", return_value=False):
            self.dialog.github_button.click()
        self.assertIn("Could not open your browser", self.dialog.feedback.text())
        self.assert_current_report(expected)
        self.dialog.copy_button.click()
        self.assertEqual(self.app.clipboard().text(), expected)
        with patch("pixelkit.bug_report_dialog.QDesktopServices.openUrl", return_value=True):
            self.dialog.github_button.click()
        self.assertIn("GitHub opened", self.dialog.feedback.text())
        self.assert_current_report(expected)

    def test_long_report_browser_exception_still_copies_the_full_report(self):
        self.complete()
        self.dialog.technical_edit.setPlainText("a long log " * 1000)
        expected = self.dialog.report_text()
        with patch("pixelkit.bug_report_dialog.QDesktopServices.openUrl", side_effect=RuntimeError("browser unavailable")):
            self.dialog.github_button.click()
        self.assertEqual(self.app.clipboard().text(), expected)
        self.assertIn("Report copied", self.dialog.feedback.text())
        self.assertIn("Could not open your browser", self.dialog.feedback.text())
        self.assert_current_report(expected)

    def test_save_cancellation_failure_and_retry_keep_the_draft(self):
        self.complete()
        self.dialog.technical_edit.setPlainText("Manually corrected log")
        expected = self.dialog.report_text()
        target = self.root / "bug.txt"
        with patch("pixelkit.bug_report_dialog.QFileDialog.getSaveFileName", return_value=("", "")), patch("pixelkit.bug_report_dialog.save_report") as save:
            self.dialog.save_button.click()
        save.assert_not_called()
        self.assert_current_report(expected)
        with patch("pixelkit.bug_report_dialog.QFileDialog.getSaveFileName", return_value=(str(target), "")), patch("pixelkit.bug_report_dialog.save_report", side_effect=OSError("permission denied")):
            self.dialog.save_button.click()
        self.assertIn("Could not save", self.dialog.feedback.text())
        self.assert_current_report(expected)
        with patch("pixelkit.bug_report_dialog.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            self.dialog.save_button.click()
        self.assertEqual(target.read_text("utf-8"), expected)
        self.assertIn("Report saved", self.dialog.feedback.text())

    def test_save_refuses_to_replace_protected_media_and_leaves_draft_usable(self):
        self.complete()
        expected = self.dialog.report_text()
        with patch("pixelkit.bug_report_dialog.QFileDialog.getSaveFileName", return_value=(str(self.source), "")):
            self.dialog.save_button.click()
        self.assertEqual(self.source.read_bytes(), b"source media")
        self.assertIn("cannot replace an input or output file", self.dialog.feedback.text())
        self.assert_current_report(expected)
        self.dialog.copy_button.click()
        self.assertEqual(self.app.clipboard().text(), expected)

    def test_save_refreshes_protected_results_after_file_chooser_returns(self):
        self.complete()
        expected = self.dialog.report_text()
        new_output = self.root / "completed while choosing.webp"
        new_context_input = self.root / "loaded while choosing.png"

        def choose(*args):
            new_output.write_bytes(b"new output media")
            new_context_input.write_bytes(b"new input media")
            self.dialog.context = replace(self.dialog.context, protected_paths=(new_context_input,))
            return str(new_output), ""

        provider = lambda: (new_output,) if new_output.exists() else ()
        self.dialog.protected_paths_provider = provider
        with patch("pixelkit.bug_report_dialog.QFileDialog.getSaveFileName", side_effect=choose):
            self.dialog.save_button.click()
        self.assertEqual(new_output.read_bytes(), b"new output media")
        self.assertIn("cannot replace an input or output file", self.dialog.feedback.text())
        self.assert_current_report(expected)
        target = self.root / "report.txt"
        with patch("pixelkit.bug_report_dialog.QFileDialog.getSaveFileName", return_value=(str(target), "")), patch("pixelkit.bug_report_dialog.save_report") as save:
            self.dialog.save_button.click()
        self.assertEqual(save.call_args.args, (target, expected, (new_context_input, new_output)))

    def test_save_provider_failure_stops_export_and_preserves_the_draft(self):
        self.complete()
        expected = self.dialog.report_text()

        def unavailable():
            raise LookupError("Could not refresh protected files")

        self.dialog.protected_paths_provider = unavailable
        with patch("pixelkit.bug_report_dialog.QFileDialog.getSaveFileName", return_value=(str(self.root / "report.txt"), "")), patch("pixelkit.bug_report_dialog.save_report") as save:
            self.dialog.save_button.click()
        save.assert_not_called()
        self.assertIn("Could not refresh protected files", self.dialog.feedback.text())
        self.assert_current_report(expected)

    def test_toggling_preview_validation_close_and_reopen_preserve_edits(self):
        self.dialog.technical_toggle.click()
        self.dialog.technical_edit.setPlainText("edited version\n  keep whitespace  ")
        self.dialog.steps_edit.setPlainText("An unfinished but valuable draft")
        expected = self.dialog.report_text()
        self.dialog.technical_toggle.click()
        self.dialog.tabs.setCurrentIndex(1)
        self.dialog.github_button.click()
        self.assert_current_report(expected)
        self.show()
        self.dialog.close_button.click()
        self.show()
        self.assert_current_report(expected)
        self.dialog.technical_toggle.click()
        self.assertEqual(self.dialog.technical_edit.toPlainText(), "edited version\n  keep whitespace  ")

    def test_small_layout_keeps_actions_visible_and_form_scrollable(self):
        self.dialog.resize(700, 560)
        self.show()
        self.dialog.technical_toggle.click()
        self.dialog.github_button.click()
        self.app.processEvents()
        rect = self.dialog.contentsRect()
        for button in (self.dialog.github_button, self.dialog.copy_button, self.dialog.save_button, self.dialog.close_button):
            self.assertTrue(button.isVisible())
            self.assertTrue(rect.contains(button.geometry()))
        self.assertGreater(self.dialog.form_scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.dialog.form_scroll.horizontalScrollBar().maximum(), 0)
        self.dialog.form_scroll.verticalScrollBar().setValue(self.dialog.form_scroll.verticalScrollBar().maximum())
        self.app.processEvents()
        self.assertLessEqual(self.dialog.technical_edit.width(), self.dialog.form_scroll.viewport().width())

    def test_technical_disclosure_reveals_the_editor_after_layout(self):
        self.dialog.resize(700, 560)
        self.show()
        self.dialog.technical_toggle.click()
        self.app.processEvents()
        self.app.processEvents()
        viewport = self.dialog.form_scroll.viewport()
        top_left = self.dialog.technical_edit.mapTo(viewport, self.dialog.technical_edit.rect().topLeft())
        bottom_right = self.dialog.technical_edit.mapTo(viewport, self.dialog.technical_edit.rect().bottomRight())
        self.assertGreaterEqual(top_left.y(), 0)
        self.assertLess(bottom_right.y(), viewport.height())

    def test_field_labels_have_buddies_and_editors_support_keyboard_navigation(self):
        editors = (self.dialog.title_edit, self.dialog.steps_edit, self.dialog.actual_edit, self.dialog.expected_edit, self.dialog.technical_edit)
        buddies = {label.buddy() for label in self.dialog.findChildren(QLabel) if label.buddy() is not None}
        self.assertTrue(set(editors).issubset(buddies))
        self.assertTrue(all(editor.accessibleName() for editor in editors))
        self.assertTrue(all(editor.tabChangesFocus() for editor in editors[1:]))
        self.assertTrue(self.dialog.github_button.isDefault())


if __name__ == "__main__":
    unittest.main()
