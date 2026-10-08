"""An in-memory draft that users review before publishing on GitHub."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QTimer, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .bug_report import (
    BugReportContext,
    compose_report,
    diagnostics,
    github_issue_url,
    save_report,
)


class BugReportDialog(QDialog):
    """Keep the user's current draft intact across actions and reopening."""

    def __init__(self, context: BugReportContext, parent=None) -> None:
        super().__init__(parent)
        self.context = context
        self.protected_paths_provider: Callable[[], tuple[Path, ...]] | None = None
        self.setWindowTitle("Report a bug")
        self.setObjectName("bugReportDialog")
        self.setModal(True)
        self.setMinimumSize(480, 400)
        self.resize(720, 660)
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(min(self.width(), available.width() - 40), min(self.height(), available.height() - 40))
        self.setStyleSheet("""
            QDialog#bugReportDialog { background: #151b24; color: #f4f7fb; }
            QLabel { color: #f4f7fb; }
            QLabel#bugReportHeading { font-size: 20px; font-weight: 600; }
            QLabel#bugReportNotice, QLabel#bugReportHint { color: #b3c0d2; }
            QLabel#bugReportFieldError { color: #ffb5b5; }
            QLabel#bugReportFeedback { color: #e1d8ff; }
            QWidget#bugReportForm, QScrollArea#bugReportScroll { background: #151b24; border: none; }
            QLineEdit, QPlainTextEdit { background: #202a38; color: #f4f7fb; border: 1px solid #697d97; border-radius: 7px; padding: 6px; selection-background-color: #6240d4; }
            QLineEdit:focus, QPlainTextEdit:focus { border-color: #6cdecf; }
            QLineEdit[invalid="true"], QPlainTextEdit[invalid="true"] { border-color: #ff9e9e; }
            QTabWidget::pane { border: 1px solid #39485c; border-radius: 5px; }
            QTabBar::tab { background: #202a38; color: #b3c0d2; padding: 8px 16px; }
            QTabBar::tab:selected { background: #302843; color: white; }
            QPushButton { background: #202a38; color: #f4f7fb; border: 1px solid #697d97; border-radius: 8px; padding: 8px 12px; font-weight: 600; }
            QPushButton:hover { background: #2a3749; }
            QPushButton:focus { border-color: #6cdecf; }
            QPushButton#bugReportPrimary { background: #8255ed; border-color: #8255ed; color: white; }
            QPushButton#bugReportPrimary:hover { background: #9369f2; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        heading = QLabel("Report a bug")
        heading.setObjectName("bugReportHeading")
        layout.addWidget(heading)
        notice = QLabel(
            "GitHub reports are public and require a GitHub account. Review your text before publishing. "
            "No files are uploaded automatically; add screenshots or media yourself on GitHub."
        )
        notice.setObjectName("bugReportNotice")
        notice.setWordWrap(True)
        layout.addWidget(notice)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Bug report details and preview")
        self.form_scroll = QScrollArea()
        self.form_scroll.setObjectName("bugReportScroll")
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        form = QWidget()
        form.setObjectName("bugReportForm")
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(12, 12, 12, 12)
        form_layout.setSpacing(7)

        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("bugReportTitle")
        self.title_edit.setMaxLength(160)
        self.title_edit.setPlaceholderText("A short summary of the problem")
        self.title_edit.setAccessibleDescription("Required. A short summary, up to 160 characters.")
        self.steps_edit = self._text_edit("bugReportSteps", "For example: choose a video, select Small, then process")
        self.actual_edit = self._text_edit("bugReportActual", "Describe the error or unexpected behavior")
        self.expected_edit = self._text_edit("bugReportExpected", "Describe the result you expected (optional)")
        self._required = (
            (self.title_edit, "Add a short title."),
            (self.steps_edit, "Describe the steps that reproduce the problem."),
            (self.actual_edit, "Describe what happened."),
        )
        self.field_errors: dict[QWidget, QLabel] = {}
        self._add_field(form_layout, "&Title (required)", self.title_edit)
        self._add_field(form_layout, "&Steps to reproduce (required)", self.steps_edit)
        self._add_field(form_layout, "What &happened? (required)", self.actual_edit)
        self._add_field(form_layout, "&Expected result (optional)", self.expected_edit)

        self.technical_toggle = QPushButton("Show technical details")
        self.technical_toggle.setCheckable(True)
        self.technical_toggle.setAutoDefault(False)
        self.technical_toggle.setAccessibleDescription("Show or hide editable technical details included in the report.")
        form_layout.addWidget(self.technical_toggle)
        self.technical_container = QWidget()
        technical_layout = QVBoxLayout(self.technical_container)
        technical_layout.setContentsMargins(0, 0, 0, 0)
        technical_layout.setSpacing(7)
        self.technical_edit = self._text_edit("bugReportTechnical", "Technical details (optional)")
        self.technical_edit.setFixedHeight(160)
        self.technical_edit.setPlainText(diagnostics(context))
        technical_label = QLabel("&Technical details (editable)")
        technical_label.setBuddy(self.technical_edit)
        self.technical_edit.setAccessibleName("Technical details (editable)")
        technical_layout.addWidget(technical_label)
        hint = QLabel("Automatic details hide personal paths and file names. Review any text you add yourself.")
        hint.setObjectName("bugReportHint")
        hint.setWordWrap(True)
        technical_layout.addWidget(hint)
        technical_layout.addWidget(self.technical_edit)
        self.technical_container.hide()
        form_layout.addWidget(self.technical_container)
        form_layout.addStretch()
        self.form_scroll.setWidget(form)
        self.tabs.addTab(self.form_scroll, "&Details")

        preview_page = QWidget()
        preview_layout = QVBoxLayout(preview_page)
        preview_layout.setContentsMargins(10, 10, 10, 10)
        preview_hint = QLabel("This is the full report that will be copied, saved or opened on GitHub. Edit it in Details.")
        preview_hint.setObjectName("bugReportHint")
        preview_hint.setWordWrap(True)
        preview_layout.addWidget(preview_hint)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("bugReportPreview")
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Full bug report preview")
        preview_layout.addWidget(self.preview, 1)
        self.tabs.addTab(preview_page, "&Preview")
        layout.addWidget(self.tabs, 1)

        self.route_notice = QLabel()
        self.route_notice.setObjectName("bugReportHint")
        self.route_notice.setWordWrap(True)
        self.route_notice.hide()
        layout.addWidget(self.route_notice)
        self.feedback = QLabel()
        self.feedback.setObjectName("bugReportFeedback")
        self.feedback.setAccessibleName("Bug report action status")
        self.feedback.setWordWrap(True)
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.feedback.hide()
        layout.addWidget(self.feedback)

        self.github_button = QPushButton("Continue on GitHub")
        self.github_button.setObjectName("bugReportPrimary")
        self.github_button.setDefault(True)
        self.github_button.clicked.connect(self._open_github)
        layout.addWidget(self.github_button)
        actions = QHBoxLayout()
        self.copy_button = QPushButton("&Copy report")
        self.copy_button.setAutoDefault(False)
        self.copy_button.clicked.connect(self._copy_report)
        self.save_button = QPushButton("&Save report…")
        self.save_button.setAutoDefault(False)
        self.save_button.clicked.connect(self._save_report)
        self.close_button = QPushButton("Close")
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.save_button)
        actions.addStretch()
        actions.addWidget(self.close_button)
        layout.addLayout(actions)

        self.technical_toggle.toggled.connect(self._toggle_technical)
        for editor in (self.title_edit, self.steps_edit, self.actual_edit, self.expected_edit, self.technical_edit):
            editor.textChanged.connect(self._draft_changed)
        self._draft_changed()
        self.title_edit.setFocus()

    @staticmethod
    def _text_edit(name: str, placeholder: str) -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setObjectName(name)
        editor.setPlaceholderText(placeholder)
        editor.setFixedHeight(78)
        editor.setTabChangesFocus(True)
        return editor

    def _add_field(self, layout: QVBoxLayout, text: str, editor: QWidget) -> None:
        label = QLabel(text)
        label.setBuddy(editor)
        editor.setAccessibleName(text.replace("&", ""))
        layout.addWidget(label)
        layout.addWidget(editor)
        error = QLabel()
        error.setObjectName("bugReportFieldError")
        error.setWordWrap(True)
        error.hide()
        self.field_errors[editor] = error
        layout.addWidget(error)

    @staticmethod
    def _text(editor: QWidget) -> str:
        return editor.text() if isinstance(editor, QLineEdit) else editor.toPlainText()

    def report_text(self) -> str:
        """Build the current report without changing any of its editable fields."""
        return compose_report(
            self.title_edit.text(),
            self.steps_edit.toPlainText(),
            self.actual_edit.toPlainText(),
            self.expected_edit.toPlainText(),
            self.technical_edit.toPlainText(),
        )

    def _draft_changed(self) -> None:
        report = self.report_text()
        self.preview.setPlainText(report)
        long_report = github_issue_url(self.title_edit.text(), report) is None
        self.github_button.setText("Copy report && open GitHub" if long_report else "Continue on GitHub")
        self.route_notice.setText(
            "The full report is too long for a prefilled link. Copy it and paste it into the GitHub issue body."
            if long_report else ""
        )
        self.route_notice.setVisible(long_report)
        self.feedback.hide()
        for editor, _ in self._required:
            if self._text(editor).strip() and editor.property("invalid"):
                self._set_field_error(editor, "")

    def _set_field_error(self, editor: QWidget, message: str) -> None:
        editor.setProperty("invalid", bool(message))
        editor.style().unpolish(editor)
        editor.style().polish(editor)
        self.field_errors[editor].setText(message)
        self.field_errors[editor].setVisible(bool(message))
        editor.setAccessibleDescription(message)

    def _validate(self) -> bool:
        invalid = []
        for editor, message in self._required:
            missing = not self._text(editor).strip()
            self._set_field_error(editor, message if missing else "")
            if missing:
                invalid.append(editor)
        if invalid:
            self.tabs.setCurrentIndex(0)
            self._status("Fill in the required fields to continue. Your draft is still here.")
            invalid[0].setFocus()
            QTimer.singleShot(0, lambda editor=invalid[0]: self._reveal_field_error(editor))
            return False
        return True

    def _toggle_technical(self, checked: bool) -> None:
        self.technical_container.setVisible(checked)
        self.technical_toggle.setText("Hide technical details" if checked else "Show technical details")
        if checked:
            QTimer.singleShot(0, self._reveal_technical)

    def _reveal_technical(self) -> None:
        if self.technical_toggle.isChecked():
            # QPlainTextEdit's input-method rectangle describes its cursor;
            # ensureWidgetVisible can reveal only that cursor, not the editor.
            top = self.technical_edit.mapTo(self.form_scroll.widget(), self.technical_edit.rect().topLeft()).y()
            visible_height = min(self.technical_edit.height(), max(1, self.form_scroll.viewport().height() - 24))
            self.form_scroll.ensureVisible(0, top + visible_height, 0, 12)

    def _reveal_field_error(self, editor: QWidget) -> None:
        if editor.property("invalid"):
            self.form_scroll.ensureWidgetVisible(self.field_errors[editor], 0, 12)

    def _status(self, text: str) -> None:
        self.feedback.setText(text)
        self.feedback.show()

    def _copy_report(self) -> None:
        if not self._validate():
            return
        QApplication.clipboard().setText(self.report_text())
        self._status("Report copied. You can paste it into a GitHub issue.")

    def _save_report(self) -> None:
        if not self._validate():
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save bug report",
            "PixelKit-bug-report.txt",
            "Text files (*.txt);;Markdown files (*.md)",
        )
        if not filename:
            return
        try:
            # A worker may finish while the native chooser is open. Refresh
            # protected destinations after it closes, without resetting drafts.
            protected_paths = tuple(self.context.protected_paths)
            if self.protected_paths_provider is not None:
                protected_paths += tuple(self.protected_paths_provider())
            save_report(Path(filename), self.report_text(), protected_paths)
        except Exception as error:
            self._status(f"Could not save the report: {error}. Your draft is still here; try another location.")
            return
        self._status("Report saved. You can attach the file to your GitHub issue.")

    def _open_github(self) -> None:
        if not self._validate():
            return
        body = self.report_text()
        url = github_issue_url(self.title_edit.text(), body)
        long_report = url is None
        if long_report:
            QApplication.clipboard().setText(body)
            url = github_issue_url(self.title_edit.text(), "")
        if url is None:
            self._status("Could not prepare the GitHub link. Your draft is still here; copy or save the report.")
            return
        try:
            opened = QDesktopServices.openUrl(QUrl(url))
        except (OSError, RuntimeError):
            opened = False
        if not opened:
            self._status(
                ("Report copied. " if long_report else "")
                + "Could not open your browser. Your draft is still here; copy or save it and open GitHub manually."
            )
            return
        self._status(
            "Report copied. Paste it into the issue body on GitHub, review it, then submit."
            if long_report else "GitHub opened with your draft. Review it there, then submit."
        )
