"""Per-file processing results and the compression report dialog."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QIODevice, QSaveFile, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QDialog, QFileDialog, QHeaderView, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout

from pixelkit import __version__
from pixelkit.bug_report import BugReportContext
from pixelkit.bug_report_dialog import BugReportDialog
from pixelkit.comparison import ComparisonDialog

if TYPE_CHECKING:
    from pixelkit.presets import Preset
    from pixelkit.video import VideoSettings


def human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def size_change(before: int, after: int) -> str:
    if before == 0:
        return "—"
    percentage = (before - after) / before * 100
    if percentage > 0:
        return f"Saved {percentage:.1f}%"
    if percentage < 0:
        return f"Larger {abs(percentage):.1f}%"
    return "No change"


def _image_error_message(error: str) -> str | None:
    """Explain known conversion failures without changing their diagnostics."""
    message = error.casefold()
    # A second failure during cleanup needs to remain visible too.
    if "could not remove temporary files" in message:
        return None
    if any(reason in message for reason in ("permission denied", "operation not permitted", "access is denied", "read-only file system")):
        return "PixelKit could not access a file or folder. Check that the original image is readable and the output folder is writable, then try again."
    if any(reason in message for reason in ("improper image header", "insufficient image data", "corrupt image", "unexpected end-of-file", "not enough image data", "invalid jpeg file structure")):
        return "This image appears to be damaged or incomplete. Open it in another app and export a fresh copy, or choose a different original file."
    if "no decode delegate for this image format" in message:
        return "PixelKit cannot read this image format. Export the image as PNG or JPEG in another app, then try again."
    if "no encode delegate for this image format" in message:
        return "PixelKit cannot save this image format. Choose PNG or JPEG as the output format, then try again."
    if "no such file or directory" in message:
        return "A required file or folder could not be found. Check that the original image and output folder still exist, then try again."
    return None


def _result_error_message(file: FileResult) -> str | None:
    if file.media_type == "image" and file.error and file.stopped is None:
        return _image_error_message(file.error)
    return None


def error_log(file: FileResult) -> str:
    """Keep the original error intact, with the context available in the report."""
    context = [
        "PixelKit error log",
        f"Version: {__version__}",
        f"Media: {file.media_type}",
        f"Input: {file.source}",
        f"Output: {file.output}",
        f"Result: {file.status}",
    ]
    if file.before is not None:
        context.append(f"Input size: {file.before} bytes")
    if file.elapsed_seconds is not None:
        context.append(f"Processing time: {file.elapsed_seconds:g} seconds")
    if file.target_bytes is not None:
        context.append(f"File-size limit: {file.target_bytes} bytes")
    if file.quality is not None:
        context.append(f"Quality used: {file.quality}")
    context.extend(f"{key}: {value}" for key, value in file.processing_settings)
    return "\n".join(context) + "\n\nError:\n" + (file.error or "")


@dataclass(frozen=True)
class FileResult:
    source: Path
    output: Path
    before: int | None
    after: int | None
    error: str | None = None
    stopped: str | None = None
    quality: int | None = None
    media_type: str = "image"
    elapsed_seconds: float | None = None
    target_bytes: int | None = None
    processing_settings: tuple[tuple[str, str], ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.stopped is None and self.error is None and self.before is not None and self.after is not None

    @property
    def status(self) -> str:
        return self.stopped or ("Done" if self.succeeded else "Failed")


@dataclass(frozen=True)
class BatchReport:
    files: tuple[FileResult, ...]
    output_dir: Path
    cancelled: bool = False
    retry_settings: Preset | VideoSettings | None = None

    @property
    def successful(self) -> tuple[FileResult, ...]:
        return tuple(file for file in self.files if file.succeeded)

    @property
    def failed(self) -> tuple[FileResult, ...]:
        return tuple(file for file in self.files if not file.succeeded and file.stopped is None)

    def completion_status(self, unit: str = "files") -> str:
        """Count successful processing results without inferring output publication."""
        successful, failed = len(self.successful), len(self.failed)
        if not self.files and not self.cancelled:
            return "No files processed"
        if self.cancelled:
            outcome = "Cancelled"
        elif failed:
            outcome = "Completed" if successful else "Failed"
        else:
            outcome = "Done" if successful == len(self.files) else "Completed"
        status = f"{outcome}: {successful} / {len(self.files)} {unit} processed successfully"
        return status + (f" · {failed} failed" if failed else "")

    @property
    def before(self) -> int:
        return sum(file.before for file in self.successful)

    @property
    def after(self) -> int:
        return sum(file.after for file in self.successful)


class ReportDialog(QDialog):
    retry_requested = pyqtSignal(object)

    def __init__(self, report: BatchReport, parent=None) -> None:
        super().__init__(parent)
        self.report = report
        self._retry_enabled = True
        self._bug_dialogs: dict[int, BugReportDialog] = {}
        self.setWindowTitle("Compression report")
        self.setObjectName("compressionReport")
        self.resize(820, 520)
        self.setMinimumSize(660, 380)
        self.setStyleSheet("""
            QDialog#compressionReport { background: #151b24; }
            QLabel#reportSummary { font-size: 20px; font-weight: 600; }
            QTableWidget { background: #202a38; alternate-background-color: #1b2532; border: 1px solid #697d97; border-radius: 8px; gridline-color: #2b3747; selection-background-color: #302843; selection-color: #f4f7fb; }
            QTableWidget::item:selected { background: #302843; color: #f4f7fb; }
            QHeaderView::section { background: #151b24; color: #f4f7fb; border: none; padding: 8px; font-weight: 600; }
            QPlainTextEdit { background: #202a38; color: #f4f7fb; border: 1px solid #697d97; border-radius: 8px; padding: 6px; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        count = len(report.successful)
        failed = len(report.failed)
        if count:
            summary_text = f"{human_size(report.before)} → {human_size(report.after)}  ·  {size_change(report.before, report.after)}"
            if failed:
                summary_text += f"  ·  {failed} failed"
            notes = sum(bool(file.warnings) for file in report.successful)
            if notes:
                summary_text += f"  ·  {notes} with notes"
        elif report.cancelled:
            summary_text = "Processing cancelled"
        elif failed:
            summary_text = "Processing failed"
        else:
            summary_text = "No files processed"
        summary = QLabel(summary_text)
        summary.setObjectName("reportSummary")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        status = QLabel(("Batch cancelled. " if report.cancelled else "") + f"Successful: {count} / {len(report.files)}. " + (f"Failed: {failed}. " if failed else "") + "Totals include successful files only.")
        status.setWordWrap(True)
        layout.addWidget(status)

        video_report = any(file.media_type == "video" for file in report.files)
        limited_report = any(file.media_type == "video" and file.target_bytes is not None for file in report.files)
        self.table = QTableWidget(len(report.files), 6 + int(video_report) + int(limited_report))
        self.table.setAccessibleName("Processing results for each file")
        self.table.setHorizontalHeaderLabels(["File", "Format", "Before", "After", "Change", "Result"] + (["Time"] if video_report else []) + (["Limit"] if limited_report else []))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for row, file in enumerate(report.files):
            values = [file.source.name, file.output.suffix.lstrip(".").upper(), human_size(file.before) if file.before is not None else "—", human_size(file.after) if file.succeeded else "—", size_change(file.before, file.after) if file.succeeded else "—", file.status]
            if file.succeeded and file.warnings:
                values[5] += " (note)"
            if video_report:
                values.append(f"{file.elapsed_seconds:.1f} s" if file.elapsed_seconds is not None else "—")
            if limited_report:
                values.append(f"{file.target_bytes / 1000000:g} MB" if file.target_bytes is not None else "—")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip((_result_error_message(file) or file.error) if column == 5 and file.error else str(file.source if column == 0 else file.output))
                if column == 5 and file.succeeded and file.warnings:
                    item.setToolTip("\n".join(file.warnings))
                self.table.setItem(row, column, item)
        layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Selected file output path or error details")
        self.details.setPlaceholderText("Select a file to see its output path or error details")
        self.details.setFixedHeight(76)
        self.copy_error_button = QPushButton("Copy error log")
        self.copy_error_button.setToolTip("Copy the full error and file details to the clipboard")
        self.copy_error_button.clicked.connect(self._copy_error_log)
        self.save_error_button = QPushButton("Save error log…")
        self.save_error_button.setToolTip("Save the full error and file details as a UTF-8 text file")
        self.save_error_button.clicked.connect(self._save_error_log)
        self.export_status = QLabel()
        self.export_status.setAccessibleName("Error log export status")
        self.technical_details_button = QPushButton("Show technical details")
        self.technical_details_button.setCheckable(True)
        self.technical_details_button.setVisible(False)
        self.technical_details_button.setToolTip("Show the original converter message for troubleshooting")
        self.technical_details_button.toggled.connect(self._update_details)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        details_actions = QHBoxLayout()
        details_actions.addWidget(self.copy_error_button)
        details_actions.addWidget(self.save_error_button)
        details_actions.addWidget(self.export_status)
        details_actions.addStretch()
        details_actions.addWidget(self.technical_details_button)
        layout.addLayout(details_actions)
        layout.addWidget(self.details)
        buttons = QHBoxLayout()
        self.open_folder = QPushButton("Open output folder")
        self.open_folder.setEnabled(report.output_dir.is_dir())
        self.open_folder.clicked.connect(self._open_output_folder)
        buttons.addWidget(self.open_folder)
        self.compare_button = QPushButton("Compare images")
        self.compare_button.setEnabled(False)
        self.compare_button.setVisible(not video_report)
        self.compare_button.clicked.connect(self._compare_images)
        buttons.addWidget(self.compare_button)
        self.report_bug_button = QPushButton("Report a bug…")
        self.report_bug_button.setToolTip("Prepare a public bug report for the selected failed file")
        self.report_bug_button.clicked.connect(self._report_bug)
        buttons.addWidget(self.report_bug_button)
        buttons.addStretch()
        self.retry_button = QPushButton("Retry failed")
        self.retry_button.setObjectName("retryFailed")
        self.retry_button.setToolTip("Return failed files and the original settings to the queue for review. Processing does not start automatically.")
        self.retry_button.clicked.connect(self._request_retry)
        self._update_retry_action()
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        if self._retry_available():
            # Keep every action readable at the report's minimum width.
            layout.setSpacing(8)
            footer = QVBoxLayout()
            footer.setSpacing(8)
            footer.addLayout(buttons)
            retry_actions = QHBoxLayout()
            retry_actions.addWidget(self.retry_button)
            retry_actions.addStretch()
            retry_actions.addWidget(close)
            footer.addLayout(retry_actions)
            layout.addLayout(footer)
        else:
            buttons.addWidget(self.retry_button)
            buttons.addWidget(close)
            layout.addLayout(buttons)
        self._update_details()
        if report.files:
            failed_row = next((row for row, file in enumerate(report.files) if not file.succeeded and file.stopped is None), None)
            row = failed_row if failed_row is not None else next((row for row, file in enumerate(report.files) if not file.succeeded), 0)
            self.table.selectRow(row)
            self.table.scrollToItem(self.table.item(row, 0))

    def showEvent(self, event) -> None:
        self._update_retry_action()
        super().showEvent(event)

    def set_retry_enabled(self, enabled: bool) -> None:
        """Allow the controller to inhibit retry while another batch is active."""
        self._retry_enabled = enabled
        self._update_retry_action()

    def _retry_available(self) -> bool:
        return bool(self.report.failed) and self.report.retry_settings is not None

    def _update_retry_action(self) -> None:
        available = self._retry_available()
        self.retry_button.setVisible(available)
        self.retry_button.setEnabled(available and self._retry_enabled and self.receivers(self.retry_requested) > 0)

    def _request_retry(self) -> None:
        if not self._retry_available() or not self.retry_button.isEnabled() or not self.receivers(self.retry_requested):
            return
        # Queue preparation belongs to the controller. Keep this report open if
        # it declines the request or cannot prepare a retry.
        self.retry_requested.emit(self.report)

    def _selection_changed(self) -> None:
        self.export_status.clear()
        self.export_status.setToolTip("")
        self.technical_details_button.setChecked(False)
        self._update_details()

    def _update_details(self) -> None:
        row = self.table.currentRow() if self.table.selectionModel().hasSelection() else -1
        can_export = self._selected_failure() is not None
        for button in (self.copy_error_button, self.save_error_button, self.report_bug_button):
            button.setVisible(can_export)
            button.setEnabled(can_export)
        self.compare_button.setEnabled(False)
        if row >= 0:
            file = self.report.files[row]
            explanation = _result_error_message(file)
            self.technical_details_button.setVisible(explanation is not None)
            error = file.error
            if explanation:
                expanded = self.technical_details_button.isChecked()
                self.technical_details_button.setText("Hide technical details" if expanded else "Show technical details")
                error = "Technical details:\n" + file.error if expanded else explanation
            quality = f"\nQuality used: {file.quality}" if file.quality is not None else ""
            limit = f"\nLimit: {file.target_bytes / 1000000:g} MB ({file.target_bytes:,} bytes)" if file.target_bytes is not None else ""
            actual = f"\nActual size: {file.after / 1000000:g} MB ({file.after:,} bytes)" if file.target_bytes is not None and file.succeeded else ""
            notes = "\n\n" + "\n".join(file.warnings) if file.succeeded and file.warnings else ""
            self.details.setPlainText((error or (str(file.output) + quality)) + limit + actual + notes)
            self.details.setToolTip(explanation or file.error or (str(file.output) + notes))
            self.compare_button.setEnabled(file.media_type == "image" and file.succeeded and file.source.is_file() and file.output.is_file())
        else:
            self.technical_details_button.setVisible(False)
            self.details.clear()
            self.details.setToolTip("")

    def _selected_failure(self) -> FileResult | None:
        row = self.table.currentRow()
        if not self.table.selectionModel().hasSelection() or not 0 <= row < len(self.report.files):
            return None
        file = self.report.files[row]
        return file if file.error and file.stopped is None and not file.succeeded else None

    def _report_bug(self) -> None:
        file = self._selected_failure()
        if file is None:
            return
        context = BugReportContext(
            mode="Video" if file.media_type == "video" else "Images", file=file,
            protected_paths=tuple(path for result in self.report.files for path in (result.source, result.output)),
        )
        parent = self.parentWidget()
        while parent is not None:
            handler = getattr(parent, "_show_bug_report", None)
            if callable(handler):
                handler(context)
                return
            parent = parent.parentWidget()
        key = self.table.currentRow()
        if key not in self._bug_dialogs:
            self._bug_dialogs[key] = BugReportDialog(context, self)
        self._bug_dialogs[key].exec()

    def _copy_error_log(self) -> None:
        file = self._selected_failure()
        if file is None:
            return
        QApplication.clipboard().setText(error_log(file))
        self.export_status.setText("Copied")
        self.export_status.setToolTip("The full error log is on the clipboard")

    def _save_error_log(self) -> None:
        file = self._selected_failure()
        if file is None:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save error log", str(self.report.output_dir / f"{file.source.stem}-error.log"),
            "Log files (*.log);;Text files (*.txt)",
        )
        if not filename or self._selected_failure() is not file:
            return
        destination = QSaveFile(filename)
        destination.setDirectWriteFallback(False)
        try:
            self._check_log_destination(Path(filename))
            data = error_log(file).encode("utf-8")
            if not destination.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(destination.errorString())
            if destination.write(data) != len(data):
                raise OSError(destination.errorString())
            if not destination.commit():
                raise OSError(destination.errorString())
        except (OSError, ValueError, RuntimeError) as exc:
            destination.cancelWriting()
            self.export_status.clear()
            self.export_status.setToolTip("")
            QMessageBox.warning(self, "Could not save error log", f"Choose another writable log file and try again. Your processing results are kept.\n\n{exc}")
            return
        self.export_status.setText("Saved")
        self.export_status.setToolTip(filename)

    def _check_log_destination(self, destination: Path) -> None:
        resolved = destination.resolve()
        for file in self.report.files:
            for media_path in (file.source, file.output):
                if str(resolved).casefold() == str(media_path.resolve()).casefold() or (
                    destination.exists() and media_path.exists() and destination.samefile(media_path)
                ):
                    raise ValueError("The error log must use a different path from every input and output file.")

    def _compare_images(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        file = self.report.files[row]
        if not file.succeeded or file.media_type != "image":
            return
        try:
            dialog = ComparisonDialog(file.source, file.output, self)
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            self.details.setPlainText(f"Could not open image comparison:\n{exc}")

    def _open_output_folder(self) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.report.output_dir.resolve()))):
            self.details.setPlainText("Could not open the output folder. You can open this path manually:\n" + str(self.report.output_dir))
