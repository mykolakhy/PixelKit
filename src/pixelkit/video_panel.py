"""Video compression controls, queue and report, separate from image settings."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from PyQt6.QtCore import QLocale, Qt, pyqtSignal
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidgetItem, QMessageBox, QPushButton, QProgressBar, QScrollArea, QVBoxLayout, QWidget

from pixelkit.report import BatchReport, ReportDialog, human_size
from pixelkit.video import VIDEO_SUFFIXES, VideoSettings, VideoWorker, find_ffmpeg, find_ffprobe
from pixelkit.widgets import DETAIL_ROLE, DropdownComboBox


class VideoPanel(QWidget):
    busy_changed = pyqtSignal(bool)
    state_changed = pyqtSignal()

    def __init__(self, colors, drop_list, show_message, parent=None) -> None:
        super().__init__(parent)
        self.show_message = show_message
        self.sources = []
        self.worker = None
        self.processing = False
        self.last_report = None
        self.source_list = drop_list
        self.ffmpeg, self.ffprobe = find_ffmpeg(), find_ffprobe()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        scroll = QScrollArea()
        scroll.setObjectName('leftScroll')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName('leftContent')
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 8, 0)
        grid.setSpacing(18)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 4)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        inputs, inputs_layout = self._card('Input videos', 'MP4, MOV and M4V · process a file or a batch')
        self.source_list.setMinimumHeight(180)
        self.source_list.files_dropped.connect(self.set_sources)
        inputs_layout.addWidget(self.source_list)
        input_actions = QHBoxLayout()
        self.add_button = QPushButton('Add videos…')
        self.add_button.clicked.connect(self.choose_many)
        self.folder_button = QPushButton('Folder…')
        self.folder_button.clicked.connect(self.choose_folder)
        self.clear_button = QPushButton('Clear')
        self.clear_button.setObjectName('subtleButton')
        self.clear_button.clicked.connect(lambda: self.set_sources([]))
        for button in (self.add_button, self.folder_button, self.clear_button):
            input_actions.addWidget(button)
        inputs_layout.addLayout(input_actions)
        self.source_info = QLabel('Drop videos here to get started')
        self.source_info.setObjectName('infoLabel')
        self.source_info.setWordWrap(True)
        inputs_layout.addWidget(self.source_info)
        grid.addWidget(inputs, 0, 0, 2, 1)

        settings, settings_layout = self._card('Compression', 'Choose a balance between quality and file size')
        self.preset_combo = DropdownComboBox(colors)
        self.preset_combo.setAccessibleName('Video compression preset')
        for name, key, detail in (
            ('High quality', 'high', 'Preserve more detail · larger output'),
            ('Balanced', 'balanced', 'Recommended for everyday sharing'),
            ('Smallest file', 'small', 'Stronger compression · fewer details'),
        ):
            self.preset_combo.addItem(name, key)
            self.preset_combo.setItemData(self.preset_combo.count() - 1, detail, DETAIL_ROLE)
        self.preset_combo.setCurrentIndex(1)
        self.resolution_combo = DropdownComboBox(colors)
        self.resolution_combo.setAccessibleName('Video resolution')
        for name, height in (('Original resolution', 0), ('Up to 1080p', 1080), ('Up to 720p', 720)):
            self.resolution_combo.addItem(name, height)
        self.audio_combo = DropdownComboBox(colors)
        self.audio_combo.setAccessibleName('Video audio')
        for name, key in (('Keep audio', 'keep'), ('Compress audio', 'compress'), ('Remove audio', 'remove')):
            self.audio_combo.addItem(name, key)
        self.audio_combo.setCurrentIndex(1)
        settings_grid = QGridLayout()
        settings_grid.setHorizontalSpacing(12)
        settings_grid.setVerticalSpacing(14)
        settings_grid.setColumnStretch(1, 1)
        self.preset_caption = QLabel('Preset')
        for row, (label, control) in enumerate((('Preset', self.preset_combo), ('Resolution', self.resolution_combo), ('Audio', self.audio_combo))):
            caption = self.preset_caption if row == 0 else QLabel(label)
            caption.setBuddy(control)
            settings_grid.addWidget(caption, row, 0)
            settings_grid.addWidget(control, row, 1)
        settings_layout.addLayout(settings_grid)
        target_row = QHBoxLayout()
        self.target_size_check = QCheckBox('Limit file size')
        self.target_size_check.setAccessibleName('Limit the size of each output video')
        self.target_size_edit = QLineEdit('25')
        validator = QDoubleValidator(0.001, 1_000_000, 3, self.target_size_edit)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        locale = QLocale.c()
        locale.setNumberOptions(QLocale.NumberOption.RejectGroupSeparator)
        validator.setLocale(locale)
        self.target_size_edit.setValidator(validator)
        self.target_size_edit.setAccessibleName('Maximum size per output video in MB')
        self.target_size_edit.setToolTip('1 MB = 1,000,000 bytes. You can enter decimals, for example 2.5.')
        self.target_size_edit.setMaximumWidth(110)
        self.target_size_edit.setEnabled(False)
        self.target_size_check.toggled.connect(self._update_target_controls)
        target_row.addWidget(self.target_size_check)
        target_row.addStretch()
        target_row.addWidget(self.target_size_edit)
        target_row.addWidget(QLabel('MB'))
        settings_layout.addLayout(target_row)
        self.target_size_hint = QLabel('The limit applies to each video. Bitrate is adjusted automatically and processing can take longer. Resolution and audio follow your choices.')
        self.target_size_hint.setObjectName('infoLabel')
        self.target_size_hint.setWordWrap(True)
        self.target_size_hint.hide()
        settings_layout.addWidget(self.target_size_hint)
        note = QLabel('Aspect ratio is preserved. Smaller videos are never enlarged. SDR video without transparency only.')
        note.setObjectName('infoLabel')
        note.setWordWrap(True)
        settings_layout.addWidget(note)
        self.audio_combo.setToolTip('Keep audio copies compatible tracks; other audio is converted to high-quality AAC.')
        grid.addWidget(settings, 0, 1)

        output, output_layout = self._card('Save output', 'MP4 · compatible H.264 video')
        self.output_edit = QLineEdit()
        self.output_edit.setAccessibleName('Video output file or folder')
        self.output_edit.setPlaceholderText('Output file')
        self.output_edit.textChanged.connect(self._update_state)
        self.output_button = QPushButton('Choose…')
        self.output_button.clicked.connect(self.choose_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.output_button)
        output_layout.addLayout(output_row)
        output_note = QLabel('Subtitles, chapters and metadata are not included.')
        output_note.setObjectName('infoLabel')
        output_note.setWordWrap(True)
        output_note.setToolTip('Output keeps the main video and all audio tracks according to the audio setting. Extra video tracks are omitted.')
        output_layout.addWidget(output_note)
        self.process_button = QPushButton('Compress and save')
        self.process_button.setObjectName('primaryButton')
        self.process_button.setMinimumHeight(46)
        self.process_button.clicked.connect(self.start_processing)
        self.report_button = QPushButton('View last report')
        self.report_button.clicked.connect(self.show_report)
        self.report_button.hide()
        output_layout.addWidget(self.report_button)
        grid.addWidget(output, 1, 1)
        grid.setRowStretch(2, 1)
        self.locked_widgets = (inputs, settings, self.output_edit, self.output_button)

        footer = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName('statusLabel')
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFixedWidth(180)
        self.progress.setFixedHeight(8)
        self.progress.setTextVisible(False)
        self.progress.hide()
        footer.addWidget(self.progress)
        self.cancel_button = QPushButton('Cancel processing')
        self.cancel_button.clicked.connect(self.cancel_processing)
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.process_button)
        layout.addLayout(footer)
        self.status.setText('Add videos to get started' if self.available else 'Video processing is unavailable. Install FFmpeg and FFprobe, then restart PixelKit.')
        self._update_state()

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)

    @staticmethod
    def _card(title, subtitle):
        card = QFrame()
        card.setObjectName('card')
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName('sectionTitle')
        layout.addWidget(heading)
        description = QLabel(subtitle)
        description.setObjectName('sectionSubtitle')
        description.setWordWrap(True)
        layout.addWidget(description)
        return card, layout

    def set_sources(self, paths: list[Path]) -> None:
        if self.processing:
            return
        self.sources = list(dict.fromkeys(path.resolve() for path in paths if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES))
        self.source_list.clear()
        self.source_list.placeholder.setVisible(not self.sources)
        for path in self.sources:
            item = QListWidgetItem(path.name)
            item.setToolTip(str(path))
            self.source_list.addItem(item)
        count = len(self.sources)
        self.source_info.setText(f'{count} video(s) · {human_size(sum(path.stat().st_size for path in self.sources))}' if count else 'Supports MP4, MOV and M4V')
        batch = count > 1
        self.output_edit.setPlaceholderText('Output folder' if batch else 'Output file')
        self.output_button.setText('Choose folder…' if batch else 'Choose…')
        if self.sources:
            source = self.sources[0]
            self.output_edit.setText(str(source.parent / 'optimized' if batch else source.with_name(source.stem + '_optimized.mp4')))
            self.status.setText(f'Selected videos: {count}')
        else:
            self.output_edit.clear()
            self.status.setText('Add videos to get started')
        if not self.available:
            self.status.setText('Video processing is unavailable. Install FFmpeg and FFprobe, then restart PixelKit.')
        self._update_state()

    def _update_state(self) -> None:
        self._update_target_controls()
        self.process_button.setEnabled(self.available and bool(self.sources) and bool(self.output_edit.text().strip()) and not self.processing)
        self.clear_button.setEnabled(bool(self.sources) and not self.processing)
        self.report_button.setEnabled(self.last_report is not None and not self.processing)
        self.state_changed.emit()

    def _update_target_controls(self) -> None:
        limited = self.target_size_check.isChecked()
        self.target_size_edit.setEnabled(limited and not self.processing)
        self.target_size_hint.setVisible(limited)
        self.preset_caption.setText('Starting quality' if limited else 'Preset')
        self.preset_combo.setToolTip('The chosen preset is tried first. If needed, compression increases to fit the limit.' if limited else 'Choose a balance between quality and file size.')

    def choose_many(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, 'Choose videos', '', 'Videos (*.mp4 *.mov *.m4v)')
        if paths:
            self.set_sources([Path(path) for path in paths])

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, 'Choose a video folder')
        if folder:
            paths = sorted(path for path in Path(folder).iterdir() if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES)
            if paths:
                self.set_sources(paths)
            else:
                self.show_message(QMessageBox.Icon.Information, 'No videos found', 'This folder does not contain MP4, MOV or M4V videos.')

    def choose_output(self) -> None:
        if len(self.sources) > 1:
            path = QFileDialog.getExistingDirectory(self, 'Choose an output folder', self.output_edit.text())
        else:
            path, _ = QFileDialog.getSaveFileName(self, 'Save compressed video', self.output_edit.text(), 'MP4 video (*.mp4)')
        if path:
            self.output_edit.setText(path)

    def start_processing(self) -> None:
        if self.processing or not self.sources or not self.available:
            return
        text = self.output_edit.text().strip()
        if not text:
            return
        target_bytes = None
        if self.target_size_check.isChecked():
            if not self.target_size_edit.hasAcceptableInput():
                self.show_message(QMessageBox.Icon.Warning, 'Check file size', 'Enter a size from 0.001 to 1,000,000 MB, using a decimal point for fractions.')
                return
            target_bytes = int(Decimal(self.target_size_edit.text()) * 1_000_000)
        output = Path(text).expanduser()
        batch = len(self.sources) > 1
        try:
            if batch:
                output.mkdir(parents=True, exist_ok=True)
                outputs = []
                used = {str(path.resolve()).casefold() for path in self.sources}
                for source in self.sources:
                    candidate = output / f'{source.stem}_optimized.mp4'
                    counter = 2
                    while str(candidate.resolve()).casefold() in used or candidate.exists():
                        candidate = output / f'{source.stem}_optimized_{counter}.mp4'
                        counter += 1
                    used.add(str(candidate.resolve()).casefold())
                    outputs.append(candidate)
            else:
                if output.suffix.lower() != '.mp4':
                    output = output.with_suffix('.mp4')
                    self.output_edit.setText(str(output))
                if output.resolve() in self.sources:
                    raise ValueError('The output file must be different from the original video.')
                output.parent.mkdir(parents=True, exist_ok=True)
                if output.exists():
                    answer = self.show_message(QMessageBox.Icon.Question, 'File already exists', f'Overwrite this file?\n\n{output}', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                    if answer != QMessageBox.StandardButton.Yes:
                        return
                outputs = [output]
            settings = VideoSettings(self.preset_combo.currentData(), self.resolution_combo.currentData(), self.audio_combo.currentData(), target_bytes=target_bytes)
        except (OSError, ValueError) as exc:
            self.show_message(QMessageBox.Icon.Warning, 'Check output location', str(exc))
            return
        self.worker = VideoWorker(list(zip(self.sources, outputs)), output if batch else output.parent, settings)
        self.worker.encoding_progress.connect(self._encoding_progress)
        self.worker.progress.connect(self._file_progress)
        self.worker.finished.connect(self._finished)
        self._set_busy(True)
        self.progress.setValue(0)
        self.status.setText(f'Preparing 1 / {len(self.sources)}…')
        self.worker.start()

    def _set_busy(self, busy: bool) -> None:
        self.processing = busy
        for widget in self.locked_widgets:
            widget.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)
        self.cancel_button.setText('Cancel processing')
        self.process_button.setText('Compressing…' if busy else 'Compress and save')
        self._update_state()
        self.busy_changed.emit(busy)

    def _encoding_progress(self, percent: int, name: str) -> None:
        self.progress.setValue(percent)
        if self.cancel_button.isEnabled():
            self.status.setText(f'{name} · {percent}%')
            self.status.setToolTip(name)

    def _file_progress(self, current: int, total: int, name: str) -> None:
        if self.cancel_button.isEnabled():
            self.status.setText(f'Processed {current} / {total} · {name}')

    def cancel_processing(self) -> None:
        if self.worker and self.processing:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText('Cancelling…')
            self.status.setText('Cancelling… Completed videos will be kept.')

    def _finished(self, report: BatchReport) -> None:
        # The report signal is emitted at the end of run(); join before a new
        # batch can replace the worker or the main window can close.
        if self.worker:
            self.worker.wait()
        self.last_report = report
        self._set_busy(False)
        self.report_button.show()
        self.status.setText(f"{'Cancelled' if report.cancelled else 'Done'}: {len(report.successful)} / {len(report.files)} videos")
        self.show_report()

    def show_report(self) -> None:
        if self.last_report is not None and not self.processing:
            dialog = ReportDialog(self.last_report, self)
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()
