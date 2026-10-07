"""Video compression controls, queue and report, separate from image settings."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from PyQt6.QtCore import QLocale, Qt, pyqtSignal
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidgetItem, QMessageBox, QPushButton, QProgressBar, QScrollArea, QVBoxLayout, QWidget

from pixelkit.report import BatchReport, ReportDialog, human_size
from pixelkit.video import VIDEO_SUFFIXES, VideoSettings, VideoWorker, find_ffmpeg, find_ffprobe
from pixelkit.video_presets import VideoPresetStore, video_preset_description, video_preset_name
from pixelkit.widgets import DETAIL_ROLE, DropdownComboBox


class VideoPanel(QWidget):
    busy_changed = pyqtSignal(bool)
    state_changed = pyqtSignal()

    def __init__(self, colors, drop_list, show_message, parent=None, preset_store: VideoPresetStore | None = None) -> None:
        super().__init__(parent)
        self.colors = colors
        self.show_message = show_message
        self.sources = []
        self.worker = None
        self.processing = False
        self.completed_files = 0
        self.batch_size = 0
        self.last_report = None
        self.source_list = drop_list
        self.video_preset_store = preset_store if preset_store is not None else VideoPresetStore()
        self.custom_presets = self.video_preset_store.load()
        self.applying_preset = False
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
        self.source_list.setFixedHeight(160)
        self.source_list.files_dropped.connect(self.add_sources)
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
        inputs_layout.addWidget(self.source_list)
        self.source_info = QLabel('Drop videos here to get started')
        self.source_info.setObjectName('infoLabel')
        self.source_info.setWordWrap(True)
        inputs_layout.addWidget(self.source_info)
        grid.addWidget(inputs, 0, 0, alignment=Qt.AlignmentFlag.AlignTop)

        settings, settings_layout = self._card('Compression', 'Choose a balance between quality and file size')
        self.saved_preset_combo = DropdownComboBox(colors)
        self.saved_preset_combo.setAccessibleName('Saved video preset')
        self.saved_preset_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.saved_preset_combo.setMinimumContentsLength(16)
        preset_label = QLabel('Saved preset')
        preset_label.setBuddy(self.saved_preset_combo)
        settings_layout.addWidget(preset_label)
        settings_layout.addWidget(self.saved_preset_combo)
        preset_actions = QHBoxLayout()
        self.save_preset_button = QPushButton('Save preset…')
        self.rename_preset_button = QPushButton('Rename…')
        self.delete_preset_button = QPushButton('Delete')
        for button, callback in ((self.save_preset_button, self._save_preset), (self.rename_preset_button, self._rename_preset), (self.delete_preset_button, self._delete_preset)):
            button.clicked.connect(callback)
            preset_actions.addWidget(button)
        self.rename_preset_button.setAccessibleName('Rename video preset')
        self.delete_preset_button.setAccessibleName('Delete video preset')
        settings_layout.addLayout(preset_actions)
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
        self.preset_caption = QLabel('Quality')
        for row, (label, control) in enumerate((('Quality', self.preset_combo), ('Resolution', self.resolution_combo), ('Audio', self.audio_combo))):
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
        grid.addWidget(settings, 0, 1, 2, 1, alignment=Qt.AlignmentFlag.AlignTop)

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
        grid.addWidget(output, 1, 0, alignment=Qt.AlignmentFlag.AlignTop)
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
        if self.video_preset_store.load_error:
            self.status.setText('Saved video presets could not be loaded. You can adjust settings and save new presets.')
        self._refresh_presets()
        self.saved_preset_combo.currentIndexChanged.connect(self._apply_selected_preset)
        for combo in (self.preset_combo, self.resolution_combo, self.audio_combo):
            combo.currentIndexChanged.connect(self._preset_edited)
        self.target_size_check.toggled.connect(self._preset_edited)
        self.target_size_edit.textChanged.connect(self._target_edited)
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

    def add_sources(self, paths: list[Path]) -> None:
        """Extend the queue without discarding an existing output folder."""
        if self.processing:
            return
        sources = list(dict.fromkeys(path.resolve() for path in [*self.sources, *paths] if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES))
        if sources == self.sources:
            return
        previous_sources = self.sources
        previous_output = self.output_edit.text().strip()
        previous_batch = len(previous_sources) > 1
        previous_default = ''
        if previous_sources:
            first = previous_sources[0]
            previous_default = str(first.parent / 'optimized' if previous_batch else first.with_name(first.stem + '_optimized.mp4'))
        self.set_sources(sources)
        if previous_sources and previous_output and previous_output != previous_default:
            # A single-file destination becomes a folder when a second input
            # is added. Keep its chosen parent rather than creating a folder
            # named after the old MP4 file.
            output = Path(previous_output)
            self.output_edit.setText(str(output if previous_batch or len(sources) == 1 else output.parent))

    def _update_state(self) -> None:
        self._update_target_controls()
        self._update_preset_controls()
        self.process_button.setEnabled(self.available and bool(self.sources) and bool(self.output_edit.text().strip()) and not self.processing)
        self.clear_button.setEnabled(bool(self.sources) and not self.processing)
        self.report_button.setEnabled(self.last_report is not None and not self.processing)
        self.state_changed.emit()

    def _update_target_controls(self) -> None:
        limited = self.target_size_check.isChecked()
        self.target_size_edit.setEnabled(limited and not self.processing)
        self.target_size_hint.setVisible(limited)
        self.preset_caption.setText('Starting quality' if limited else 'Quality')
        self.preset_combo.setToolTip('The chosen preset is tried first. If needed, compression increases to fit the limit.' if limited else 'Choose a balance between quality and file size.')

    def _refresh_presets(self, selected: str | None = None) -> None:
        self.saved_preset_combo.blockSignals(True)
        try:
            self.saved_preset_combo.clear()
            self.saved_preset_combo.addItem('Custom settings', None)
            self.saved_preset_combo.setItemData(0, 'Adjust video settings manually', DETAIL_ROLE)
            if self.custom_presets:
                self.saved_preset_combo.insertSeparator(1)
            for name, preset in self.custom_presets.items():
                self.saved_preset_combo.addItem(name, name)
                row = self.saved_preset_combo.count() - 1
                detail = video_preset_description(preset)
                self.saved_preset_combo.setItemData(row, detail, DETAIL_ROLE)
                self.saved_preset_combo.setItemData(row, f'{name}\n{detail}', Qt.ItemDataRole.ToolTipRole)
            self.saved_preset_combo.setCurrentIndex(max(0, self.saved_preset_combo.findData(selected)))
        finally:
            self.saved_preset_combo.blockSignals(False)
        self._update_preset_controls()

    def _update_preset_controls(self) -> None:
        selected = self.saved_preset_combo.currentData() in self.custom_presets
        self.saved_preset_combo.setEnabled(not self.processing)
        self.save_preset_button.setEnabled(not self.processing)
        self.rename_preset_button.setEnabled(selected and not self.processing)
        self.delete_preset_button.setEnabled(selected and not self.processing)
        self.saved_preset_combo.setToolTip('Apply saved settings to every input video. Editing a setting switches to Custom settings.')

    def _preset_edited(self, *_args) -> None:
        if not self.applying_preset:
            self.saved_preset_combo.setCurrentIndex(0)
            self._update_preset_controls()

    def _target_edited(self, *_args) -> None:
        if self.target_size_check.isChecked():
            self._preset_edited()

    def _apply_selected_preset(self, _index: int) -> None:
        if self.processing:
            return
        preset = self.custom_presets.get(self.saved_preset_combo.currentData())
        if preset is not None:
            self.applying_preset = True
            try:
                self.preset_combo.setCurrentIndex(self.preset_combo.findData(preset.preset))
                self.resolution_combo.setCurrentIndex(self.resolution_combo.findData(preset.max_height))
                self.audio_combo.setCurrentIndex(self.audio_combo.findData(preset.audio))
                self.target_size_check.setChecked(preset.target_bytes is not None)
                megabytes = Decimal(preset.target_bytes) / 1_000_000 if preset.target_bytes is not None else Decimal(25)
                self.target_size_edit.setText(format(megabytes, 'f'))
                self._update_target_controls()
            finally:
                self.applying_preset = False
        self._update_preset_controls()

    def _current_settings(self) -> VideoSettings:
        target_bytes = None
        if self.target_size_check.isChecked():
            if not self.target_size_edit.hasAcceptableInput():
                raise ValueError('Enter a size from 0.001 to 1,000,000 MB, using a decimal point for fractions.')
            target_bytes = int(Decimal(self.target_size_edit.text()) * 1_000_000)
        return VideoSettings(self.preset_combo.currentData(), self.resolution_combo.currentData(), self.audio_combo.currentData(), target_bytes=target_bytes)

    def _ask_preset_name(self, suggested: str, title: str = 'Save video preset') -> str | None:
        dialog = QDialog(self)
        dialog.setObjectName('videoPresetDialog')
        dialog.setWindowTitle(title)
        dialog.setFixedWidth(420)
        dialog.setStyleSheet(f"QDialog#videoPresetDialog {{ background: {self.colors['card']}; }}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(QLabel('Preset name'))
        edit = QLineEdit(suggested)
        edit.setMaxLength(60)
        edit.setPlaceholderText('e.g. For sharing · 25 MB')
        edit.setAccessibleName('Video preset name')
        layout.addWidget(edit)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(dialog.reject)
        save = QPushButton('Rename' if title == 'Rename video preset' else 'Save')
        save.setObjectName('primaryButton')
        save.setDefault(True)
        save.setEnabled(bool(suggested.strip()))
        edit.textChanged.connect(lambda text: save.setEnabled(bool(text.strip())))
        save.clicked.connect(dialog.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        edit.setFocus()
        edit.selectAll()
        try:
            return edit.text().strip() if dialog.exec() == QDialog.DialogCode.Accepted else None
        finally:
            dialog.deleteLater()

    def _validated_preset_name(self, suggested: str, title: str) -> str | None:
        while True:
            answer = self._ask_preset_name(suggested, title)
            if answer is None:
                return None
            try:
                return video_preset_name(answer)
            except ValueError as exc:
                self.show_message(QMessageBox.Icon.Warning, 'Choose a preset name', str(exc))
                suggested = answer

    def _confirm(self, title: str, text: str) -> bool:
        return self.show_message(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _persist_presets(self, presets: dict[str, VideoSettings]) -> bool:
        try:
            self.video_preset_store.save(presets)
        except (OSError, ValueError) as exc:
            self.show_message(QMessageBox.Icon.Warning, 'Could not save video presets', str(exc))
            return False
        self.custom_presets = presets
        return True

    def _save_preset(self) -> None:
        if self.processing:
            return
        try:
            preset = self._current_settings()
        except ValueError as exc:
            self.show_message(QMessageBox.Icon.Warning, 'Check preset settings', str(exc))
            return
        name = self._validated_preset_name(self.saved_preset_combo.currentData() or '', 'Save video preset')
        if name is None:
            return
        existing = next((old for old in self.custom_presets if old.casefold() == name.casefold()), None)
        if existing and not self._confirm('Replace video preset?', f'Replace the saved settings for “{existing}”?'):
            return
        updated = dict(self.custom_presets)
        if existing:
            del updated[existing]
        updated[name] = preset
        if self._persist_presets(updated):
            self._refresh_presets(name)
            self.status.setText(f'Video preset saved: {name}')

    def _rename_preset(self) -> None:
        old = self.saved_preset_combo.currentData()
        if self.processing or old not in self.custom_presets:
            return
        name = self._validated_preset_name(old, 'Rename video preset')
        if name is None or name == old:
            return
        existing = next((key for key in self.custom_presets if key != old and key.casefold() == name.casefold()), None)
        if existing and not self._confirm('Replace video preset?', f'Replace “{existing}” with the saved settings from “{old}”?'):
            return
        updated = {key: value for key, value in self.custom_presets.items() if key not in (old, existing)}
        updated[name] = self.custom_presets[old]
        if self._persist_presets(updated):
            self._refresh_presets(name)
            self.status.setText(f'Video preset renamed: {name}')

    def _delete_preset(self) -> None:
        name = self.saved_preset_combo.currentData()
        if self.processing or name not in self.custom_presets:
            return
        if not self._confirm('Delete video preset?', f'Delete “{name}”? Your current video settings will be kept.'):
            return
        updated = dict(self.custom_presets)
        del updated[name]
        if self._persist_presets(updated):
            self._refresh_presets()
            self.status.setText(f'Video preset deleted: {name}')

    def choose_many(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, 'Choose videos', '', 'Videos (*.mp4 *.mov *.m4v)')
        if paths:
            self.add_sources([Path(path) for path in paths])

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, 'Choose a video folder')
        if folder:
            paths = sorted(path for path in Path(folder).iterdir() if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES)
            if paths:
                self.add_sources(paths)
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
        try:
            settings = self._current_settings()
        except ValueError as exc:
            self.show_message(QMessageBox.Icon.Warning, 'Check file size', str(exc))
            return
        batch = len(self.sources) > 1
        try:
            output = Path(text).expanduser()
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
                if output.is_dir():
                    raise ValueError('Choose an output file, rather than an existing folder.')
                if output.suffix.lower() != '.mp4':
                    output = output.with_suffix('.mp4')
                    self.output_edit.setText(str(output))
                if output.resolve() in self.sources or (output.exists() and output.samefile(self.sources[0])):
                    raise ValueError('The output file must be different from the original video.')
                output.parent.mkdir(parents=True, exist_ok=True)
                if output.exists():
                    answer = self.show_message(QMessageBox.Icon.Question, 'File already exists', f'Overwrite this file?\n\n{output}', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                    if answer != QMessageBox.StandardButton.Yes:
                        return
                outputs = [output]
        except (OSError, ValueError, RuntimeError) as exc:
            self.show_message(QMessageBox.Icon.Warning, 'Check output location', str(exc))
            return
        self.worker = VideoWorker(list(zip(self.sources, outputs)), output if batch else output.parent, settings)
        self.worker.encoding_progress.connect(self._encoding_progress)
        self.worker.progress.connect(self._file_progress)
        self.worker.finished.connect(self._finished)
        self.completed_files = 0
        self.batch_size = len(self.sources)
        self._set_busy(True)
        self.progress.setValue(0)
        self.status.setText(f'File 1 of {self.batch_size} · Preparing…')
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
            current = min(self.completed_files + 1, self.batch_size)
            stage = 'Starting…' if percent == 0 else f'Compressing · {percent}%'
            self.status.setText(f'File {current} of {self.batch_size} · {stage} · {name}')
            self.status.setToolTip(name)

    def _file_progress(self, current: int, total: int, name: str) -> None:
        self.completed_files = current
        self.batch_size = total
        if self.cancel_button.isEnabled():
            self.status.setText(f'File {current} of {total} · Processed · {name}')

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
