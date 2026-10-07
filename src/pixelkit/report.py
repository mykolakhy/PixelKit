"""Per-file processing results and the compression report dialog."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QAbstractItemView, QDialog, QHeaderView, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout

from pixelkit.comparison import ComparisonDialog


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

    @property
    def successful(self) -> tuple[FileResult, ...]:
        return tuple(file for file in self.files if file.succeeded)

    @property
    def before(self) -> int:
        return sum(file.before for file in self.successful)

    @property
    def after(self) -> int:
        return sum(file.after for file in self.successful)


class ReportDialog(QDialog):
    def __init__(self, report: BatchReport, parent=None) -> None:
        super().__init__(parent)
        self.report = report
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
        summary = QLabel(f"{human_size(report.before)} → {human_size(report.after)}  ·  {size_change(report.before, report.after)}" if count else "No files processed successfully")
        summary.setObjectName("reportSummary")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        status = QLabel(("Batch cancelled. " if report.cancelled else "") + f"Successful: {count} / {len(report.files)}. Totals include successful files only.")
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
            if video_report:
                values.append(f"{file.elapsed_seconds:.1f} s" if file.elapsed_seconds is not None else "—")
            if limited_report:
                values.append(f"{file.target_bytes / 1000000:g} MB" if file.target_bytes is not None else "—")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip((_result_error_message(file) or file.error) if column == 5 and file.error else str(file.source if column == 0 else file.output))
                self.table.setItem(row, column, item)
        layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Selected file output path or error details")
        self.details.setPlaceholderText("Select a file to see its output path or error details")
        self.details.setFixedHeight(76)
        self.technical_details_button = QPushButton("Show technical details")
        self.technical_details_button.setCheckable(True)
        self.technical_details_button.setVisible(False)
        self.technical_details_button.setToolTip("Show the original converter message for troubleshooting")
        self.technical_details_button.toggled.connect(self._update_details)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        details_actions = QHBoxLayout()
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
        buttons.addStretch()
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _selection_changed(self) -> None:
        self.technical_details_button.setChecked(False)
        self._update_details()

    def _update_details(self) -> None:
        row = self.table.currentRow() if self.table.selectionModel().hasSelection() else -1
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
            self.details.setPlainText((error or (str(file.output) + quality)) + limit + actual)
            self.details.setToolTip(explanation or file.error or str(file.output))
            self.compare_button.setEnabled(file.media_type == "image" and file.succeeded and file.source.is_file() and file.output.is_file())
        else:
            self.technical_details_button.setVisible(False)
            self.details.clear()
            self.details.setToolTip("")

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
