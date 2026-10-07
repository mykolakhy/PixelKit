from __future__ import annotations

import subprocess
import shutil
import sys
import tempfile
from threading import Event
from pathlib import Path

from PyQt6.QtCore import QEvent, QLocale, QThread, QTimer, Qt, QSize, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QImageReader, QIntValidator, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QProgressBar,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pixelkit.runtime import ProcessingCancelled, find_magick, missing_magick_message, resource_path, run_magick
from pixelkit.presets import BUILTIN_PRESETS, OUTPUT_FORMATS, Preset, PresetStore, preset_name
from pixelkit.widgets import DETAIL_ROLE, DropdownComboBox
from pixelkit.report import BatchReport, FileResult, ReportDialog, human_size
from pixelkit.target_size import TARGET_FORMATS, compress_to_size
from pixelkit.video import VIDEO_SUFFIXES
from pixelkit.video_panel import VideoPanel
from pixelkit.video_presets import VideoPresetStore


APP_TITLE = "PixelKit"
ICON_PATH = resource_path("PixelKit.png")
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".heif", ".ico"}


class ElidedLabel(QLabel):
    """Keep long status text inside its layout, with the full text in a tooltip."""
    def __init__(self, text: str = "") -> None:
        super().__init__()
        self.full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setMinimumHeight(20)
        self.setText(text)

    def setText(self, text: str) -> None:
        self.full_text = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        self._fit_text()

    def _fit_text(self) -> None:
        super().setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideMiddle, max(0, self.contentsRect().width())))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_text()


class MessageDialog(QDialog):
    """Size wrapped text after Qt has applied the inherited dialog font."""
    def showEvent(self, event) -> None:
        super().showEvent(event)
        body = self.findChild(QLabel, "messageBody")
        scroll = self.findChild(QScrollArea, "messageBodyScroll")
        if body and scroll:
            height = max(36, body.heightForWidth(body.width()) + 8)
            body.setFixedHeight(height)
            maximum = max(120, min(340, self.screen().availableGeometry().height() - 220))
            scroll.setFixedHeight(min(height, maximum))
            self.adjustSize()


class PixelKitApplication(QApplication):
    """Receive Finder's Open With and Dock drop events, including at startup."""
    files_opened = pyqtSignal(list)

    def __init__(self, argv: list[str]) -> None:
        self.pending_files: list[Path] = []
        super().__init__(argv)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.FileOpen and event.file():
            self.pending_files.append(Path(event.file()))
            QTimer.singleShot(0, self.dispatch_open_files)
            return True
        return super().event(event)

    def dispatch_open_files(self) -> None:
        if self.pending_files:
            paths, self.pending_files = self.pending_files, []
            self.files_opened.emit(paths)


class DropListWidget(QListWidget):
    files_dropped = pyqtSignal(list)

    def __init__(self, suffixes=None, media="images") -> None:
        super().__init__()
        self.suffixes = SUPPORTED_SUFFIXES if suffixes is None else suffixes
        self.setAcceptDrops(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setAccessibleName(f"Input {media}")
        self.setAccessibleDescription(f"Drop {media} or folders here, or use the file selection buttons.")
        self.placeholder = QLabel(f"Drop {media} or folders here\nor use the file selection buttons", self.viewport())
        self.placeholder.setObjectName("dropHint")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.placeholder.setGeometry(self.viewport().rect().adjusted(16, 12, -16, -12))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        files: list[Path] = []
        for path in paths:
            if path.is_dir():
                files.extend(sorted(item for item in path.iterdir() if item.is_file() and item.suffix.lower() in self.suffixes))
            elif path.is_file() and path.suffix.lower() in self.suffixes:
                files.append(path)
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()
        else:
            event.ignore()

    def wheelEvent(self, event) -> None:
        # Keep wheel events inside the file list so the parent QScrollArea
        # does not scroll at the same time, especially at the list edges.
        super().wheelEvent(event)
        event.accept()


class BatchWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(object)

    def __init__(self, jobs: list[tuple[list[str], Path]], output_dir: Path, target_bytes: int | None = None) -> None:
        super().__init__()
        self.jobs = jobs
        self.output_dir = output_dir
        self.cancel_event = Event()
        self.target_bytes = target_bytes

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        files = []
        for index, (command, output) in enumerate(self.jobs, start=1):
            if self.cancel_event.is_set():
                files.append(FileResult(Path(command[1]), output, None, None, "Not processed because the batch was cancelled.", "Skipped"))
                continue
            source_name = Path(command[1]).name
            before = None
            after = None
            error = None
            stopped = None
            quality = None
            temporary = None
            temporary_dir = None
            try:
                before = Path(command[1]).stat().st_size
                # Publish only a completed conversion, preserving any existing
                # destination and removing partial files when cancelled.
                temporary_dir = Path(tempfile.mkdtemp(prefix=".pixelkit-", dir=output.parent))
                temporary = temporary_dir / output.name
                converted_command = [*command[:-1], str(temporary)]
                if self.target_bytes is not None:
                    quality = compress_to_size(converted_command, temporary, self.target_bytes, self.cancel_event.is_set)
                    result = subprocess.CompletedProcess(converted_command, 0, "", "")
                else:
                    result = run_magick(converted_command, capture_output=True, text=True, timeout=300, cancel_requested=self.cancel_event.is_set)
                if self.cancel_event.is_set():
                    raise ProcessingCancelled()
                if result.returncode == 0 and temporary.is_file():
                    after = temporary.stat().st_size
                    temporary.replace(output)
                else:
                    error = result.stderr.strip() or result.stdout.strip() or "ImageMagick returned an unknown error."
            except ProcessingCancelled:
                error = "Processing cancelled. No partial output was saved."
                stopped = "Cancelled"
            except subprocess.TimeoutExpired:
                error = "Processing exceeded the 5-minute limit."
            except (OSError, ValueError) as exc:
                error = str(exc)
            finally:
                if temporary_dir is not None:
                    try:
                        shutil.rmtree(temporary_dir)
                    except OSError as exc:
                        error = f"{error or 'Conversion completed.'}\nCould not remove temporary files at {temporary_dir}: {exc}"
            files.append(FileResult(Path(command[1]), output, before, after, error, stopped, quality))
            self.progress.emit(index, len(self.jobs), source_name)
        self.finished.emit(BatchReport(tuple(files), self.output_dir, any(file.stopped for file in files)))


class ImageMagickStudio(QMainWindow):
    def __init__(self, preset_store: PresetStore | None = None) -> None:
        super().__init__()
        self.magick = find_magick()
        self.sources: list[Path] = []
        self.default_output = True
        self.processing = False
        self.open_action: QAction | None = None
        self.save_action: QAction | None = None
        self.worker: BatchWorker | None = None
        self.last_report: BatchReport | None = None
        self.field_errors: dict[QLineEdit, QLabel] = {}
        self.preset_store = preset_store if preset_store is not None else PresetStore()
        self.custom_presets = self.preset_store.load()
        self.applying_preset = False
        self.colors = {
            "bg": "#0d1117",
            "card": "#151b24",
            "input": "#202a38",
            "border": "#2b3747",
            "control_border": "#697d97",
            "text": "#f4f7fb",
            "muted": "#93a0b3",
            "accent": "#7452eb",
            "accent_hover": "#7958ee",
            "teal": "#32d6c8",
        }
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1100, 780)
        self.setMinimumSize(1040, 620)
        self._build_ui()
        self._set_sources([])
        self._connect_preset_changes()
        if self.preset_store.load_error:
            self._set_status("Saved presets could not be loaded; built-in presets are available.")

        if sys.platform == "darwin":
            self._build_macos_menu()
        self._update_action_state()

        if not self.magick:
            QTimer.singleShot(0, lambda: self._show_message(QMessageBox.Icon.Warning, "ImageMagick not found", missing_magick_message()))

    def _build_macos_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        for label, shortcut, callback in (
            ("Open files…", QKeySequence.StandardKey.Open, self._open_current_mode),
            ("Process and save", QKeySequence.StandardKey.Save, self._process_current_mode),
            ("Close", QKeySequence.StandardKey.Close, self.close),
        ):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            file_menu.addAction(action)
            if shortcut == QKeySequence.StandardKey.Open:
                self.open_action = action
            elif shortcut == QKeySequence.StandardKey.Save:
                self.save_action = action
        quit_action = QAction("Quit PixelKit", self)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Quit))
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def open_files(self, paths: list[Path]) -> None:
        images = [path for path in paths if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES]
        videos = [path for path in paths if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES]
        if images or videos:
            if self.processing or self.video_panel.processing or (self.worker and self.worker.isRunning()):
                self._show_message(QMessageBox.Icon.Information, "Processing files", "Wait for the current batch to finish before opening more files.")
                return
            if images and videos:
                self._show_message(QMessageBox.Icon.Information, "Choose one media type", "Open images and videos as separate batches.")
                return
            self.mode_buttons[1 if videos else 0].click()
            if videos:
                self.video_panel.set_sources(videos)
            else:
                self._set_sources(images)
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _build_ui(self) -> None:
        self.setStyleSheet(self._stylesheet())
        page = QWidget()
        page.setObjectName("page")
        self.setCentralWidget(page)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(30, 26, 30, 20)
        outer.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(14)
        logo = QLabel("✦")
        logo.setObjectName("logo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(52, 52)
        logo.setPixmap(QIcon(str(ICON_PATH)).pixmap(QSize(52, 52), self.devicePixelRatioF()))
        logo.setStyleSheet("background: transparent;")
        header.addWidget(logo)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel(APP_TITLE)
        title.setObjectName("appTitle")
        subtitle = QLabel("Resize images and compress videos")
        subtitle.setObjectName("appSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        badge = QLabel("LOCAL PROCESSING")
        badge.setToolTip("Your files are processed on this computer.")
        badge.setObjectName("badge")
        header.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header)

        mode_row = QHBoxLayout()
        mode_group = QButtonGroup(self)
        self.mode_buttons = []
        for index, name in enumerate(("Images", "Video")):
            button = QPushButton(name)
            button.setObjectName("modeButton")
            button.setCheckable(True)
            mode_group.addButton(button, index)
            mode_row.addWidget(button)
            self.mode_buttons.append(button)
        self.mode_buttons[0].setChecked(True)
        mode_row.addStretch()
        outer.addLayout(mode_row)
        self.media_stack = QStackedWidget()
        outer.addWidget(self.media_stack, 1)
        image_page = QWidget()
        self.media_stack.addWidget(image_page)
        outer = QVBoxLayout(image_page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(18)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(10)
        preset_row.addWidget(QLabel("Preset"))
        self.preset_combo = DropdownComboBox(self.colors)
        self.preset_combo.setAccessibleName("Processing preset")
        self.preset_combo.setMinimumWidth(260)
        self.preset_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preset_combo.setMinimumContentsLength(24)
        self.preset_combo.currentIndexChanged.connect(self._apply_selected_preset)
        preset_row.addWidget(self.preset_combo, 1)
        self.save_preset_button = QPushButton("Save preset…")
        self.save_preset_button.clicked.connect(self._save_preset)
        preset_row.addWidget(self.save_preset_button)
        self.delete_preset_button = QPushButton("Delete preset")
        self.delete_preset_button.clicked.connect(self._delete_preset)
        preset_row.addWidget(self.delete_preset_button)
        outer.addLayout(preset_row)

        content = QHBoxLayout()
        content.setSpacing(18)
        left_content = QWidget()
        left_content.setObjectName("leftContent")
        left = QVBoxLayout(left_content)
        left.setContentsMargins(0, 0, 8, 0)
        left.setSpacing(14)
        right_content = QWidget()
        right_content.setObjectName("rightContent")
        right = QVBoxLayout(right_content)
        right.setContentsMargins(0, 0, 8, 0)
        right.setSpacing(14)
        left_scroll = QScrollArea()
        left_scroll.setObjectName("leftScroll")
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_content)
        content.addWidget(left_scroll, 5)
        right_scroll = QScrollArea()
        right_scroll.setObjectName("rightScroll")
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setWidget(right_content)
        content.addWidget(right_scroll, 4)
        outer.addLayout(content, 1)

        self.source_card = self._source_card()
        self.resize_card = self._resize_card()
        self.quality_card = self._quality_card()
        left.addWidget(self.source_card)
        left.addWidget(self.resize_card)
        left.addStretch(1)

        right.addWidget(self.quality_card)
        right.addWidget(self._output_card())
        right.addStretch(1)

        footer = QHBoxLayout()
        self.status_label = ElidedLabel("Add images to get started")
        self.status_label.setObjectName("statusLabel")
        footer.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(220)
        self.progress.setFixedHeight(8)
        self.progress.hide()
        footer.addWidget(self.progress)
        self.cancel_button = QPushButton("Cancel processing")
        self.cancel_button.setAccessibleName("Cancel image processing")
        self.cancel_button.clicked.connect(self._cancel_processing)
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        self.report_button = QPushButton("View last report")
        self.report_button.setEnabled(False)
        self.report_button.clicked.connect(self._show_last_report)
        footer.addWidget(self.report_button)
        self.process_button = QPushButton("Process and save")
        self.process_button.setObjectName("primaryButton")
        self.process_button.setMinimumHeight(46)
        self.process_button.clicked.connect(self._start_processing)
        footer.addWidget(self.process_button)
        outer.addLayout(footer)
        self.output_edit.textChanged.connect(self._output_path_changed)
        self.output_edit.textEdited.connect(lambda _text: setattr(self, "default_output", False))
        self.output_edit.editingFinished.connect(self._normalize_output_extension)
        self._refresh_presets()
        self.video_panel = VideoPanel(self.colors, DropListWidget(VIDEO_SUFFIXES, "videos"), self._show_message, self, preset_store=VideoPresetStore(self.preset_store.settings))
        self.media_stack.addWidget(self.video_panel)
        mode_group.idClicked.connect(self.media_stack.setCurrentIndex)
        self.media_stack.currentChanged.connect(lambda _index: self._update_action_state())
        self.video_panel.busy_changed.connect(lambda _busy: self._update_action_state())
        self.video_panel.state_changed.connect(self._update_action_state)

    def _open_current_mode(self) -> None:
        if self.media_stack.currentIndex() == 1:
            self.video_panel.choose_many()
        else:
            self._choose_many()

    def _process_current_mode(self) -> None:
        if self.media_stack.currentIndex() == 1:
            self.video_panel.start_processing()
        else:
            self._start_processing()

    def _refresh_presets(self, selected: str | None = None) -> None:
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("Custom settings", None)
        self.preset_combo.setItemData(0, "Adjust settings manually", DETAIL_ROLE)
        for group, presets in (("builtin", BUILTIN_PRESETS), ("saved", self.custom_presets)):
            if not presets:
                continue
            self.preset_combo.insertSeparator(self.preset_combo.count())
            for name, preset in presets.items():
                key = f"{group}:{name}"
                self.preset_combo.addItem(name, key)
                row = self.preset_combo.count() - 1
                self.preset_combo.setItemData(row, preset.description(), DETAIL_ROLE)
                self.preset_combo.setItemData(row, f"{name}\n{preset.description()}", Qt.ItemDataRole.ToolTipRole)
        self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(selected)))
        self.preset_combo.blockSignals(False)
        self._update_preset_controls()

    def _connect_preset_changes(self) -> None:
        for edit in (self.width_edit, self.height_edit, self.long_side_edit, self.background_edit, self.target_size_edit):
            edit.textChanged.connect(self._preset_edited)
        for checkbox in (self.keep_ratio, self.strip_metadata, self.target_size_check):
            checkbox.toggled.connect(self._preset_edited)
        self.quality_slider.valueChanged.connect(self._preset_edited)
        self.resize_mode.currentIndexChanged.connect(self._preset_edited)
        self.format_combo.currentIndexChanged.connect(self._preset_edited)

    def _preset_edited(self, *_args) -> None:
        if not self.applying_preset:
            self.preset_combo.setCurrentIndex(0)
            self._update_preset_controls()

    def _update_preset_controls(self) -> None:
        key = self.preset_combo.currentData()
        self.preset_combo.setEnabled(not self.processing)
        self.save_preset_button.setEnabled(not self.processing)
        self.delete_preset_button.setEnabled(bool(key and key.startswith("saved:")) and not self.processing)
        hint = "Choose a preset for all input images. Changing a processing setting switches to Custom settings."
        if key:
            group, name = key.split(":", 1)
            presets = BUILTIN_PRESETS if group == "builtin" else self.custom_presets
            hint = f"{name}\n{presets[name].description()}\n\n{hint}"
        self.preset_combo.setToolTip(hint)

    def _apply_selected_preset(self, _index: int) -> None:
        if self.processing:
            return
        key = self.preset_combo.currentData()
        if key:
            group, name = key.split(":", 1)
            presets = BUILTIN_PRESETS if group == "builtin" else self.custom_presets
            preset = presets[name]
            self.applying_preset = True
            try:
                self.resize_mode.setCurrentIndex(1 if preset.resize_mode == "longest_side" else 0)
                self.width_edit.setText(str(preset.width) if preset.width else "")
                self.height_edit.setText(str(preset.height) if preset.height else "")
                self.long_side_edit.setText(str(preset.longest_side) if preset.longest_side else "")
                self.keep_ratio.setChecked(preset.keep_ratio)
                self.quality_slider.setValue(preset.quality)
                self.strip_metadata.setChecked(preset.strip_metadata)
                self.background_edit.setText(preset.background)
                self.format_combo.setCurrentText(preset.output_format)
                self.target_size_check.setChecked(preset.target_kib is not None)
                self.target_size_edit.setText(str(preset.target_kib or 500))
                # Keep a manually chosen destination, with the preset's format.
                if len(self.sources) == 1 and not self.default_output and self.output_edit.text().strip():
                    output = Path(self.output_edit.text().strip())
                    if output.suffix:
                        self.output_edit.setText(str(output.with_suffix(f".{self._selected_extension()}")))
            finally:
                self.applying_preset = False
        self._update_preset_controls()

    def _current_preset(self) -> Preset:
        def dimension(edit: QLineEdit) -> int | None:
            text = edit.text().strip()
            return int(text) if text else None

        longest_side = self.resize_mode.currentIndex() == 1
        return Preset(
            resize_mode="longest_side" if longest_side else "dimensions",
            width=None if longest_side else dimension(self.width_edit),
            height=None if longest_side else dimension(self.height_edit),
            longest_side=dimension(self.long_side_edit) if longest_side else None,
            keep_ratio=self.keep_ratio.isChecked(),
            quality=self.quality_slider.value(), strip_metadata=self.strip_metadata.isChecked(),
            background=self.background_edit.text().strip() or "#ffffff",
            output_format=self.format_combo.currentText(),
            target_kib=int(self.target_size_edit.text()) if self.target_size_check.isChecked() else None,
        )

    def _ask_preset_name(self, suggested: str) -> str | None:
        dialog = QDialog(self)
        dialog.setObjectName("presetDialog")
        dialog.setWindowTitle("Save preset")
        dialog.setFixedWidth(420)
        dialog.setStyleSheet(self._stylesheet() + f"QDialog#presetDialog {{ background: {self.colors['card']}; }}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Preset name"))
        edit = QLineEdit(suggested)
        edit.setMaxLength(60)
        edit.setPlaceholderText("e.g. Product photos")
        edit.setAccessibleName("Preset name")
        layout.addWidget(edit)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dialog.reject)
        save = QPushButton("Save")
        save.setObjectName("primaryButton")
        save.setDefault(True)
        save.setEnabled(bool(suggested.strip()))
        edit.textChanged.connect(lambda text: save.setEnabled(bool(text.strip())))
        save.clicked.connect(dialog.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        edit.setFocus()
        edit.selectAll()
        return edit.text().strip() if dialog.exec() == QDialog.DialogCode.Accepted else None

    def _save_preset(self) -> None:
        if self.processing:
            return
        try:
            preset = self._current_preset()
        except ValueError as exc:
            self._show_message(QMessageBox.Icon.Warning, "Check preset settings", str(exc))
            return
        key = self.preset_combo.currentData()
        suggested = key.split(":", 1)[1] if key and key.startswith("saved:") else ""
        while True:
            answer = self._ask_preset_name(suggested)
            if answer is None:
                return
            suggested = answer
            try:
                name = preset_name(answer)
                break
            except ValueError as exc:
                self._show_message(QMessageBox.Icon.Warning, "Choose a preset name", str(exc))
        existing = next((old for old in self.custom_presets if old.casefold() == name.casefold()), None)
        if existing and not self._confirm("Replace preset?", f'Replace the saved settings for “{existing}”?'):
            return
        updated = dict(self.custom_presets)
        if existing:
            del updated[existing]
        updated[name] = preset
        if self._persist_presets(updated):
            self._refresh_presets(f"saved:{name}")
            self._set_status(f"Preset saved: {name}")

    def _persist_presets(self, presets: dict[str, Preset]) -> bool:
        try:
            self.preset_store.save(presets)
        except OSError as exc:
            self._show_message(QMessageBox.Icon.Warning, "Could not save presets", str(exc))
            return False
        self.custom_presets = presets
        return True

    def _delete_preset(self) -> None:
        key = self.preset_combo.currentData()
        if self.processing or not key or not key.startswith("saved:"):
            return
        name = key.split(":", 1)[1]
        if not self._confirm("Delete preset?", f'Delete “{name}”? Your current processing settings will be kept.'):
            return
        updated = dict(self.custom_presets)
        del updated[name]
        if self._persist_presets(updated):
            self._refresh_presets()
            self._set_status(f"Preset deleted: {name}")

    def _section_header(self, number: str, title: str, subtitle: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        label = QLabel(f"{number}   {title.upper()}")
        label.setObjectName("sectionTitle")
        note = QLabel(subtitle)
        note.setObjectName("sectionSubtitle")
        note.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(note)
        return widget

    def _source_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(self._section_header("01", "Input images", "One file, multiple files, or an entire folder"))

        self.source_list = DropListWidget()
        self.source_list.setFixedHeight(145)
        self.source_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.source_list.files_dropped.connect(self._append_sources)
        layout.addWidget(self.source_list)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        one = QPushButton("One file")
        one.clicked.connect(self._choose_one)
        many = QPushButton("Add images…")
        many.clicked.connect(self._choose_many)
        folder = QPushButton("Folder")
        folder.clicked.connect(self._choose_folder)
        clear = QPushButton("Clear")
        self.clear_button = clear
        clear.setObjectName("subtleButton")
        clear.clicked.connect(self._clear_sources)
        buttons.addWidget(one)
        buttons.addWidget(many)
        buttons.addWidget(folder)
        buttons.addStretch(1)
        buttons.addWidget(clear)
        layout.addLayout(buttons)
        self.source_info = QLabel("Choose images or drag them here")
        self.source_info.setObjectName("infoLabel")
        self.source_info.setWordWrap(True)
        layout.addWidget(self.source_info)
        return card

    def _resize_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(self._section_header("02", "Resize", "Leave fields empty to keep the original size"))
        mode_row = QHBoxLayout()
        mode_label = QLabel("Resize mode")
        mode_row.addWidget(mode_label)
        self.resize_mode = DropdownComboBox(self.colors)
        self.resize_mode.setAccessibleName("Resize mode")
        mode_label.setBuddy(self.resize_mode)
        self.resize_mode.addItems(["Width × height", "Longest side"])
        mode_row.addStretch(1)
        mode_row.addWidget(self.resize_mode)
        layout.addLayout(mode_row)

        self.resize_stack = QStackedWidget()
        standard_page = QWidget()
        standard_grid = QGridLayout(standard_page)
        standard_grid.setContentsMargins(0, 0, 0, 0)
        standard_grid.setHorizontalSpacing(10)
        standard_grid.setVerticalSpacing(10)
        self.width_edit = QLineEdit()
        self.width_edit.setPlaceholderText("Original")
        self.width_edit.setValidator(self._integer_validator(1, 100000))
        self.width_edit.setAccessibleName("Width in pixels")
        self.height_edit = QLineEdit()
        self.height_edit.setPlaceholderText("Original")
        self.height_edit.setValidator(self._integer_validator(1, 100000))
        self.height_edit.setAccessibleName("Height in pixels")
        standard_grid.addWidget(QLabel("Width"), 0, 0)
        standard_grid.addWidget(self.width_edit, 0, 1)
        standard_grid.addWidget(QLabel("px"), 0, 2)
        standard_grid.addWidget(QLabel("Height"), 1, 0)
        standard_grid.addWidget(self.height_edit, 1, 1)
        standard_grid.addWidget(QLabel("px"), 1, 2)
        self.keep_ratio = QCheckBox("Keep aspect ratio")
        self.keep_ratio.setChecked(True)
        standard_grid.addWidget(self.keep_ratio, 0, 3, 2, 1)
        standard_grid.setColumnStretch(1, 1)
        standard_grid.addWidget(self._field_error_label(self.width_edit), 2, 0, 1, 4)
        standard_grid.addWidget(self._field_error_label(self.height_edit), 3, 0, 1, 4)
        self.resize_stack.addWidget(standard_page)

        long_side_page = QWidget()
        long_side_layout = QVBoxLayout(long_side_page)
        long_side_layout.setContentsMargins(0, 0, 0, 0)
        long_side_layout.setSpacing(8)
        long_side_row = QHBoxLayout()
        long_side_row.addWidget(QLabel("Longest side"))
        self.long_side_edit = QLineEdit()
        self.long_side_edit.setPlaceholderText("e.g. 1600")
        self.long_side_edit.setValidator(self._integer_validator(1, 100000))
        self.long_side_edit.setAccessibleName("Longest side in pixels")
        self.long_side_edit.setMinimumWidth(120)
        long_side_row.addWidget(self.long_side_edit, 1)
        long_side_row.addWidget(QLabel("px"))
        long_side_note = QLabel("Aspect ratio is preserved automatically")
        long_side_note.setObjectName("infoLabel")
        long_side_note.setWordWrap(True)
        long_side_layout.addLayout(long_side_row)
        long_side_layout.addWidget(self._field_error_label(self.long_side_edit))
        long_side_layout.addWidget(long_side_note)
        self.resize_stack.addWidget(long_side_page)
        self.resize_mode.currentIndexChanged.connect(self.resize_stack.setCurrentIndex)
        self.resize_mode.currentIndexChanged.connect(self._clear_field_errors)
        layout.addWidget(self.resize_stack)
        return card

    def _quality_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(self._section_header("03", "Quality and metadata", "Optimize file size without unnecessary settings"))
        quality_row = QHBoxLayout()
        self.quality_caption = QLabel("Quality")
        quality_row.addWidget(self.quality_caption)
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(10, 100)
        self.quality_slider.setValue(82)
        self.quality_slider.setAccessibleName("Image quality")
        self.quality_label = QLabel("82")
        self.quality_label.setObjectName("valueBadge")
        self.quality_label.setFixedWidth(42)
        self.quality_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.quality_slider.valueChanged.connect(lambda value: self.quality_label.setText(str(value)))
        quality_row.addWidget(self.quality_slider, 1)
        quality_row.addWidget(self.quality_label)
        layout.addLayout(quality_row)
        target_row = QHBoxLayout()
        self.target_size_check = QCheckBox("Limit file size")
        self.target_size_edit = QLineEdit("500")
        self.target_size_edit.setValidator(self._integer_validator(1, 1000000))
        self.target_size_edit.setAccessibleName("Maximum size per output file in KiB")
        self.target_size_edit.setMaximumWidth(90)
        self.target_size_edit.setEnabled(False)
        self.target_size_check.toggled.connect(self.target_size_edit.setEnabled)
        self.target_size_check.toggled.connect(lambda enabled: self.quality_caption.setText("Max quality" if enabled else "Quality"))
        target_row.addWidget(self.target_size_check)
        target_row.addStretch()
        target_row.addWidget(self.target_size_edit)
        target_row.addWidget(QLabel("KiB"))
        layout.addLayout(target_row)
        layout.addWidget(self._field_error_label(self.target_size_edit))
        self.target_size_check.toggled.connect(self._clear_field_errors)
        note = QLabel("JPG, WEBP or AVIF · quality adjusts automatically")
        note.setObjectName("infoLabel")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.strip_metadata = QCheckBox("Remove EXIF and other metadata")
        self.strip_metadata.setChecked(True)
        layout.addWidget(self.strip_metadata)
        background_row = QHBoxLayout()
        background_row.addWidget(QLabel("JPEG background"))
        self.background_edit = QLineEdit("#ffffff")
        self.background_edit.setMaximumWidth(125)
        self.background_edit.setAccessibleName("JPEG background color")
        self.background_edit.setToolTip("Background for transparent pixels when saving JPG. Enter a color name or #RRGGBB.")
        background_row.addStretch(1)
        background_row.addWidget(self.background_edit)
        layout.addLayout(background_row)
        return card

    def _output_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(self._section_header("04", "Export", "Output format and save location"))
        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("Format"))
        self.format_combo = DropdownComboBox(self.colors)
        self.format_combo.addItems(OUTPUT_FORMATS)
        self.format_combo.setAccessibleName("Output format")
        self.format_combo.setToolTip("Automatic keeps each input image's original format.")
        self.format_combo.currentTextChanged.connect(self._format_changed)
        format_row.addStretch(1)
        format_row.addWidget(self.format_combo)
        layout.addLayout(format_row)
        layout.addWidget(QLabel("Save to"))
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Output file or folder")
        self.output_edit.setAccessibleName("Output file or folder")
        self.output_button = QPushButton("Choose…")
        self.output_button.clicked.connect(self._choose_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.output_button)
        layout.addLayout(output_row)
        note = QLabel("The file extension follows the selected format.")
        note.setObjectName("infoLabel")
        note.setWordWrap(True)
        layout.addWidget(note)
        return card

    def _integer_validator(self, minimum: int, maximum: int) -> QIntValidator:
        validator = QIntValidator(minimum, maximum, self)
        locale = validator.locale()
        locale.setNumberOptions(locale.numberOptions() | QLocale.NumberOption.RejectGroupSeparator)
        validator.setLocale(locale)
        return validator

    def _field_error_label(self, edit: QLineEdit) -> QLabel:
        label = QLabel()
        label.setObjectName("fieldError")
        label.setWordWrap(True)
        label.hide()
        self.field_errors[edit] = label
        edit.textChanged.connect(lambda _text, field=edit: self._set_field_error(field, ""))
        return label

    def _set_field_error(self, edit: QLineEdit, message: str) -> None:
        edit.setProperty("invalid", bool(message))
        edit.setAccessibleDescription(message)
        edit.style().unpolish(edit)
        edit.style().polish(edit)
        label = self.field_errors[edit]
        label.setText(message)
        label.setVisible(bool(message))

    def _clear_field_errors(self, *_args) -> None:
        for edit in self.field_errors:
            self._set_field_error(edit, "")

    def _validate_processing_fields(self) -> bool:
        self._clear_field_errors()
        dimensions = [self.long_side_edit] if self.resize_mode.currentIndex() == 1 else [self.width_edit, self.height_edit]
        invalid = []
        for edit in dimensions:
            if edit.text().strip() and not edit.hasAcceptableInput():
                self._set_field_error(edit, "Use 1–100000 px, or leave empty for the original size.")
                invalid.append(edit)
        if self.target_size_check.isChecked() and not self.target_size_edit.hasAcceptableInput():
            self._set_field_error(self.target_size_edit, "Enter a file-size limit from 1 to 1000000 KiB.")
            invalid.append(self.target_size_edit)
        if not invalid:
            return True
        edit = invalid[0]
        edit.setFocus()
        # Wait for the new error label to receive its layout geometry.
        QTimer.singleShot(0, lambda: self._reveal_field_error(edit))
        self._set_status("Check the highlighted settings before processing.")
        if edit is self.target_size_edit:
            self._show_message(QMessageBox.Icon.Warning, "Check file-size limit", self.field_errors[edit].text())
        return False

    def _reveal_field_error(self, edit: QLineEdit) -> None:
        if not edit.property("invalid"):
            return
        parent = edit.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if parent is not None:
            parent.ensureWidgetVisible(self.field_errors[edit], 10, 10)

    def _stylesheet(self) -> str:
        c = self.colors
        check_icon = resource_path("check.svg").as_posix()
        arrow_icon = resource_path("chevron-down.svg").as_posix()
        arrow_up_icon = resource_path("chevron-up.svg").as_posix()
        return f"""
            QWidget {{ color: {c['text']}; font-size: 13px; }}
            QMainWindow, #page {{ background: {c['bg']}; }}
            QWidget#leftContent, QWidget#rightContent, QScrollArea#leftScroll, QScrollArea#rightScroll {{ background: transparent; border: none; }}
            QFrame#card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 16px; }}
            QLabel#appTitle {{ color: {c['text']}; font-size: 25px; font-weight: 700; }}
            QLabel#appSubtitle {{ color: {c['muted']}; font-size: 13px; }}
            QLabel#badge {{ background: #172d2d; color: {c['teal']}; border-radius: 12px; padding: 8px 12px; font-size: 12px; font-weight: 600; }}
            QLabel#sectionTitle {{ color: {c['text']}; font-size: 14px; font-weight: 700; letter-spacing: 0.5px; }}
            QLabel#sectionSubtitle, QLabel#infoLabel, QLabel#statusLabel {{ color: {c['muted']}; font-size: 12px; }}
            QLabel#dropHint {{ color: {c['muted']}; background: transparent; border: none; font-size: 13px; }}
            QLabel:disabled {{ color: {c['muted']}; }}
            QLabel#valueBadge {{ background: #282241; color: #bdb0ff; border-radius: 8px; padding: 5px 8px; font-weight: 700; }}
            QLineEdit, QComboBox {{ background: {c['input']}; color: {c['text']}; border: 1px solid {c['control_border']}; border-radius: 9px; padding: 8px 10px; selection-background-color: {c['accent']}; selection-color: white; }}
            QLineEdit {{ placeholder-text-color: {c['muted']}; }}
            QLineEdit:focus, QComboBox:focus {{ border-color: {c['teal']}; }}
            QLineEdit[invalid="true"] {{ border-color: #ff8c8c; }}
            QLabel#fieldError {{ color: #ff8c8c; font-size: 12px; }}
            QLineEdit:disabled, QComboBox:disabled {{ background: {c['card']}; color: {c['muted']}; border-color: {c['border']}; }}
            QComboBox {{ padding-right: 32px; }}
            QComboBox:hover {{ background: #243142; border-color: {c['muted']}; }}
            QComboBox:on {{ border-color: {c['teal']}; }}
            QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; border: none; width: 28px; }}
            QComboBox::down-arrow {{ image: url("{arrow_icon}"); width: 12px; height: 8px; }}
            QComboBox::down-arrow:on {{ image: url("{arrow_up_icon}"); }}
            QListWidget {{ background: {c['input']}; color: #dce5f0; border: 1px solid {c['control_border']}; border-radius: 11px; padding: 8px; outline: none; }}
            QListWidget:focus {{ border-color: {c['teal']}; }}
            QListWidget::item {{ padding: 7px 9px; border-radius: 6px; }}
            QListWidget::item:hover {{ background: #2a3749; }}
            QListWidget::item:selected {{ background: {c['accent']}; color: white; }}
            QListWidget:disabled {{ background: {c['card']}; color: {c['muted']}; border-color: {c['border']}; }}
            QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px 0 2px 2px; }}
            QScrollBar::handle:vertical {{ background: {c['control_border']}; border-radius: 4px; min-height: 28px; }}
            QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px 2px 0 2px; }}
            QScrollBar::handle:horizontal {{ background: {c['control_border']}; border-radius: 4px; min-width: 28px; }}
            QScrollBar::handle:horizontal:hover {{ background: {c['muted']}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
            QAbstractScrollArea::corner {{ background: {c['card']}; }}
            QCheckBox {{ color: #dce5f0; spacing: 8px; }}
            QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {c['control_border']}; background: {c['input']}; }}
            QCheckBox::indicator:checked {{ image: url("{check_icon}"); background: {c['accent']}; border-color: {c['accent']}; }}
            QCheckBox::indicator:hover {{ border-color: {c['text']}; }}
            QCheckBox::indicator:focus {{ border: 2px solid {c['teal']}; }}
            QCheckBox:disabled {{ color: {c['muted']}; }}
            QCheckBox::indicator:disabled {{ background: #413d58; border-color: {c['border']}; }}
            QPushButton {{ background: {c['input']}; color: #dce5f0; border: 1px solid {c['control_border']}; border-radius: 9px; padding: 9px 13px; font-weight: 600; }}
            QPushButton:hover {{ background: #2a3749; border-color: {c['muted']}; color: white; }}
            QPushButton:pressed {{ background: #344257; }}
            QPushButton:focus {{ border-color: {c['teal']}; }}
            QPushButton:disabled {{ background: {c['card']}; color: {c['muted']}; border-color: {c['border']}; }}
            QPushButton#subtleButton {{ background: transparent; border: 1px solid transparent; color: {c['muted']}; }}
            QPushButton#modeButton {{ min-width: 84px; }}
            QPushButton#modeButton:checked {{ background: #302843; border-color: {c['accent']}; color: white; }}
            QPushButton#subtleButton:hover {{ color: white; background: #202a38; }}
            QPushButton#subtleButton:focus {{ border-color: {c['teal']}; }}
            QPushButton#subtleButton:disabled {{ color: #69788d; background: transparent; }}
            QPushButton#primaryButton {{ background: {c['accent']}; color: white; border: 1px solid transparent; font-size: 14px; font-weight: 700; }}
            QPushButton#primaryButton:hover {{ background: {c['accent_hover']}; }}
            QPushButton#primaryButton:pressed {{ background: #6240d4; }}
            QPushButton#primaryButton:focus {{ border-color: white; }}
            QPushButton#primaryButton:disabled {{ background: #413d58; color: #9b98ad; }}
            QSlider::groove:horizontal {{ height: 6px; background: {c['control_border']}; border-radius: 3px; }}
            QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 3px; }}
            QSlider::handle:horizontal {{ width: 18px; height: 18px; margin: -6px 0; background: white; border: 3px solid {c['accent']}; border-radius: 9px; }}
            QSlider::handle:focus {{ border-color: {c['teal']}; }}
            QSlider::handle:disabled {{ background: {c['muted']}; border-color: #413d58; }}
            QSlider::sub-page:disabled {{ background: #413d58; }}
            QProgressBar {{ background: {c['input']}; border: none; border-radius: 4px; }}
            QProgressBar::chunk {{ background: {c['teal']}; border-radius: 4px; }}
            QToolTip {{ background: {c['card']}; color: {c['text']}; border: 1px solid {c['control_border']}; padding: 6px; }}
            QMenuBar, QMenu {{ background: {c['card']}; color: {c['text']}; }}
            QMenu::item:selected {{ background: {c['accent']}; }}
        """

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _output_path_changed(self, text: str) -> None:
        self.output_edit.setToolTip(text)
        self._update_action_state()

    def _update_action_state(self) -> None:
        ready = bool(self.magick and self.sources and self.output_edit.text().strip() and not self.processing)
        self.process_button.setEnabled(ready)
        self.report_button.setEnabled(self.last_report is not None and not self.processing)
        self.clear_button.setEnabled(bool(self.sources) and not self.processing)
        video_panel = getattr(self, "video_panel", None)
        busy = self.processing or (video_panel is not None and video_panel.processing)
        for button in getattr(self, "mode_buttons", ()):
            button.setEnabled(not busy)
        if self.open_action:
            self.open_action.setEnabled(not busy)
        if self.save_action:
            self.save_action.setEnabled(video_panel.process_button.isEnabled() if video_panel is not None and self.media_stack.currentIndex() == 1 else ready)
        self._update_preset_controls()

    def _set_processing_state(self, processing: bool) -> None:
        self.processing = processing
        for widget in (self.source_card, self.resize_card, self.quality_card, self.format_combo, self.output_edit, self.output_button):
            widget.setEnabled(not processing)
        self.process_button.setText("Processing…" if processing else "Process and save")
        self.cancel_button.setVisible(processing)
        self.cancel_button.setEnabled(processing)
        self.cancel_button.setText("Cancel processing")
        self._update_action_state()

    def _cancel_processing(self) -> None:
        if self.worker and self.processing:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("Cancelling…")
            self._set_status("Cancelling… Completed files will be kept.")

    def _show_message(
        self,
        icon: QMessageBox.Icon,
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    ) -> QMessageBox.StandardButton:
        dialog = MessageDialog(self)
        dialog.setObjectName("messageDialog")
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setFixedWidth(520)
        message_color = {
            QMessageBox.Icon.Information: self.colors["teal"],
            QMessageBox.Icon.Warning: "#f3b766",
            QMessageBox.Icon.Critical: "#ff8c8c",
        }.get(icon, self.colors["accent"])
        icon_color = self.colors["bg"] if icon != QMessageBox.Icon.Question else "#ffffff"
        dialog.setStyleSheet(
            f"""
            QDialog#messageDialog {{ background: {self.colors['card']}; }}
            QLabel#messageIcon {{ background: {message_color}; color: {icon_color}; border-radius: 23px; font-size: 22px; font-weight: 600; }}
            QLabel#messageTitle {{ color: {self.colors['text']}; font-size: 16px; font-weight: 700; }}
            QLabel#messageBody {{ color: #dce5f0; background: transparent; font-size: 13px; }}
            QScrollArea#messageBodyScroll {{ background: transparent; border: none; }}
            QPushButton {{ background: {self.colors['input']}; color: {self.colors['text']}; border: 1px solid {self.colors['control_border']}; border-radius: 8px; min-width: 76px; padding: 8px 15px; }}
            QPushButton:hover {{ background: {self.colors['accent']}; border-color: {self.colors['accent']}; color: white; }}
            QPushButton:focus {{ border-color: {self.colors['teal']}; }}
            """
        )
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(16)
        content = QHBoxLayout()
        content.setSpacing(16)
        icon_text = {
            QMessageBox.Icon.Information: "i",
            QMessageBox.Icon.Warning: "!",
            QMessageBox.Icon.Critical: "×",
            QMessageBox.Icon.Question: "?",
        }.get(icon, "i")
        icon_label = QLabel(icon_text)
        icon_label.setObjectName("messageIcon")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFixedSize(46, 46)
        content.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignTop)
        text_box = QVBoxLayout()
        text_box.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("messageTitle")
        title_label.setTextFormat(Qt.TextFormat.PlainText)
        title_label.setWordWrap(True)
        body_label = QLabel(text)
        body_label.setObjectName("messageBody")
        body_label.setTextFormat(Qt.TextFormat.PlainText)
        body_label.setWordWrap(True)
        body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body_scroll = QScrollArea()
        body_scroll.setObjectName("messageBodyScroll")
        body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body_scroll.viewport().setAutoFillBackground(False)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body_scroll.setWidget(body_label)
        body_label.setFixedWidth(398)
        body_label.ensurePolished()
        body_height = max(36, body_label.heightForWidth(398) + 4)
        body_label.setFixedHeight(body_height)
        max_body_height = max(120, min(340, self.screen().availableGeometry().height() - 220))
        body_scroll.setFixedHeight(min(body_height, max_body_height))
        text_box.addWidget(title_label)
        text_box.addWidget(body_scroll)
        content.addLayout(text_box, 1)
        layout.addLayout(content)
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        button_map = (
            (QMessageBox.StandardButton.No, "No"),
            (QMessageBox.StandardButton.Yes, "Yes"),
            (QMessageBox.StandardButton.Ok, "OK"),
        )
        for button, label in button_map:
            if buttons & button:
                action = QPushButton(label)
                default = QMessageBox.StandardButton.No if buttons & QMessageBox.StandardButton.No else QMessageBox.StandardButton.Ok
                action.setDefault(button == default)
                action.clicked.connect(lambda _checked=False, result=button: dialog.done(int(result)))
                action_row.addWidget(action)
        layout.addLayout(action_row)
        return QMessageBox.StandardButton(dialog.exec())

    def _confirm(self, title: str, text: str) -> bool:
        answer = self._show_message(
            QMessageBox.Icon.Question,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _set_sources(self, paths: list[Path]) -> None:
        unique = list(dict.fromkeys(path.resolve() for path in paths if path.is_file()))
        self.sources = unique
        self.source_list.clear()
        self.source_list.placeholder.setVisible(not unique)
        if not unique:
            self.source_info.setText("Supports JPG, PNG, WEBP, AVIF, HEIC and more")
            self.output_edit.clear()
            self._update_output_mode()
            self._set_status("Add images to get started")
            self._update_action_state()
            return

        for path in unique:
            item = QListWidgetItem(path.name)
            item.setToolTip(str(path))
            self.source_list.addItem(item)
        first = unique[0]
        reader = QImageReader(str(first))
        image_size = reader.size()
        dimensions = f"{image_size.width()} × {image_size.height()}" if image_size.isValid() else "unknown size"
        first_info = f"{dimensions}  •  {human_size(first.stat().st_size)}  •  {first.suffix.upper().lstrip('.')}"
        count = f"{len(unique)} images  •  First: " if len(unique) > 1 else "1 image  •  "
        self.source_info.setText(count + first_info)
        self.default_output = True
        self._set_default_output()
        self._update_output_mode()
        self._set_status(f"Selected files: {len(unique)}")
        self._update_action_state()

    def _choose_one(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose an image", "", "Images (*.jpg *.jpeg *.png *.webp *.gif *.bmp *.tif *.tiff *.avif *.heic *.ico);;All files (*.*)")
        if path:
            self._set_sources([Path(path)])

    def _choose_many(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose images", "", "Images (*.jpg *.jpeg *.png *.webp *.gif *.bmp *.tif *.tiff *.avif *.heic *.ico);;All files (*.*)")
        if paths:
            self._append_sources([Path(path) for path in paths])

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose an image folder")
        if folder:
            paths = sorted((path for path in Path(folder).iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES), key=lambda path: path.name.lower())
            if paths:
                self._append_sources(paths)
            else:
                self._show_message(QMessageBox.Icon.Information, "No images found", "This folder does not contain supported images.")

    def _append_sources(self, paths: list[Path]) -> None:
        if self.processing or (self.worker and self.worker.isRunning()):
            return
        previous = self.sources
        destination = self.output_edit.text().strip()
        automatic = self.default_output
        combined = list(dict.fromkeys([*previous, *(path.resolve() for path in paths if path.is_file())]))
        if combined == previous:
            return
        self._set_sources(combined)
        if previous and not automatic and destination:
            output = Path(destination)
            if len(previous) == 1 and len(combined) > 1:
                output = output.parent
            self.output_edit.setText(str(output))
            self.default_output = False

    def _clear_sources(self) -> None:
        if not self.worker or not self.worker.isRunning():
            self._set_sources([])

    def closeEvent(self, event) -> None:
        if self.video_panel.processing or (self.video_panel.worker and self.video_panel.worker.isRunning()):
            self._show_message(QMessageBox.Icon.Information, "Processing videos", "Cancel video processing or wait for it to finish before closing PixelKit.")
            event.ignore()
            return
        if self.worker and self.worker.isRunning():
            self._show_message(QMessageBox.Icon.Information, "Processing images", "Wait for image processing to finish before closing PixelKit.")
            event.ignore()
            return
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "source_list"):
            # Give batches more visible rows when the window has room for them.
            self.source_list.setFixedHeight(max(145, min(320, self.height() - 635)))

    def _selected_extension(self, source: Path | None = None) -> str:
        selected = self.format_combo.currentText().lower()
        current_source = source or (self.sources[0] if self.sources else None)
        if selected == "automatic":
            return current_source.suffix.lstrip(".").lower() if current_source else "jpg"
        return selected

    def _set_default_output(self) -> None:
        if not self.sources:
            return
        if len(self.sources) > 1:
            self.output_edit.setText(str(self.sources[0].parent / "optimized"))
        else:
            source = self.sources[0]
            self.output_edit.setText(str(source.with_name(f"{source.stem}_optimized.{self._selected_extension(source)}")))

    def _update_output_mode(self) -> None:
        batch = len(self.sources) > 1
        self.output_edit.setPlaceholderText("Output folder" if batch else "Output file")
        self.output_button.setText("Choose folder…" if batch else "Choose…")

    def _format_changed(self) -> None:
        if self.default_output:
            self._set_default_output()
        else:
            self._normalize_output_extension()

    def _normalize_output_extension(self) -> None:
        if len(self.sources) != 1 or not self.output_edit.text().strip():
            return
        output = Path(self.output_edit.text().strip())
        try:
            if output.expanduser().is_dir():
                return
            extension = self._selected_extension()
            aliases = {"jpeg": "jpg", "tif": "tiff"}
            if aliases.get(output.suffix.lower().lstrip("."), output.suffix.lower().lstrip(".")) != aliases.get(extension, extension):
                self.output_edit.setText(str(output.with_suffix(f".{extension}")))
        except (OSError, ValueError, RuntimeError):
            # Invalid destinations are explained by the processing action.
            return

    def _choose_output(self) -> None:
        if len(self.sources) > 1:
            folder = QFileDialog.getExistingDirectory(self, "Choose an output folder")
            if folder:
                self.output_edit.setText(folder)
                self.default_output = False
            return
        selected, _ = QFileDialog.getSaveFileName(self, "Save output", self.output_edit.text(), "Images (*.jpg *.jpeg *.png *.webp *.avif *.gif *.bmp *.tif *.tiff);;All files (*.*)")
        if selected:
            self.output_edit.setText(selected)
            self.default_output = False
            self._normalize_output_extension()

    def _build_command(self, source: Path, output: Path) -> list[str]:
        command = [self.magick, str(source), "-auto-orient"]
        if self.resize_mode.currentIndex() == 1:
            long_side = self.long_side_edit.text().strip()
            if long_side:
                long_side = str(int(long_side))
                command += ["-resize", f"{long_side}x{long_side}"]
        else:
            width = self.width_edit.text().strip()
            height = self.height_edit.text().strip()
            width = str(int(width)) if width else ""
            height = str(int(height)) if height else ""
            if width or height:
                if width and height:
                    resize = f"{width}x{height}" if self.keep_ratio.isChecked() else f"{width}x{height}!"
                elif width:
                    resize = f"{width}x"
                else:
                    resize = f"x{height}"
                command += ["-resize", resize]
        extension = output.suffix.lower().lstrip(".")
        if extension in {"jpg", "jpeg"}:
            command += ["-background", self.background_edit.text().strip() or "#ffffff", "-alpha", "remove"]
        if self.strip_metadata.isChecked():
            command.append("-strip")
        if extension in {"jpg", "jpeg", "webp", "avif", "heic", "heif", "jxl", "png"}:
            command += ["-quality", str(self.quality_slider.value())]
        command.append(str(output))
        return command

    def _start_processing(self) -> None:
        # Menu shortcuts remain available while the process button is disabled.
        if self.worker and self.worker.isRunning():
            return
        if not self.magick:
            self._show_message(QMessageBox.Icon.Critical, "ImageMagick not found", missing_magick_message())
            return
        if not self.sources:
            self._show_message(QMessageBox.Icon.Information, "No images", "Add at least one image first.")
            return
        if not self._validate_processing_fields():
            return
        output_text = self.output_edit.text().strip()
        if not output_text:
            self._show_message(QMessageBox.Icon.Information, "No output location", "Choose an output file or folder.")
            return
        batch = len(self.sources) > 1
        target_bytes = None
        if self.target_size_check.isChecked():
            try:
                target_bytes = self._current_preset().target_kib * 1024
            except ValueError as exc:
                self._show_message(QMessageBox.Icon.Warning, "Check file-size limit", str(exc))
                return
            if any(self._selected_extension(source) not in TARGET_FORMATS for source in self.sources):
                self._show_message(QMessageBox.Icon.Warning, "Choose a supported format", "File-size limits support JPG, WEBP and AVIF. Choose one of these output formats.")
                return
        try:
            output = Path(output_text).expanduser()
            if batch:
                output.mkdir(parents=True, exist_ok=True)
                used: set[str] = set()
                outputs: list[Path] = []
                for source in self.sources:
                    extension = self._selected_extension(source)
                    candidate = output / f"{source.stem}_optimized.{extension}"
                    counter = 2
                    while str(candidate.resolve()).casefold() in used or candidate.exists():
                        candidate = output / f"{source.stem}_optimized_{counter}.{extension}"
                        counter += 1
                    used.add(str(candidate.resolve()).casefold())
                    outputs.append(candidate)
            else:
                if output.is_dir():
                    raise IsADirectoryError("Choose an output file, rather than an existing folder.")
                self._normalize_output_extension()
                output = Path(self.output_edit.text().strip()).expanduser()
                if output.resolve() == self.sources[0].resolve() or (output.exists() and output.samefile(self.sources[0])):
                    self._show_message(QMessageBox.Icon.Critical, "Unsafe overwrite", "The output file must be different from the input file.")
                    return
                output.parent.mkdir(parents=True, exist_ok=True)
                if output.exists() and not self._confirm("File already exists", f"Overwrite this file?\n\n{output}"):
                    return
                outputs = [output]
        except (OSError, ValueError, RuntimeError) as exc:
            self._show_message(QMessageBox.Icon.Warning, "Could not prepare output", f"Choose a writable output file or folder and try again. Your images and settings are kept.\n\n{exc}")
            return

        jobs = [(self._build_command(source, target), target) for source, target in zip(self.sources, outputs)]
        self._set_processing_state(True)
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.progress.show()
        self._set_status(f"Processing 0 / {len(jobs)}…")
        self.worker = BatchWorker(jobs, output if batch else output.parent, target_bytes)
        self.worker.progress.connect(lambda current, total, name: self._set_progress(current, total, name))
        self.worker.finished.connect(self._processing_finished)
        self.worker.start()

    def _set_progress(self, current: int, total: int, name: str) -> None:
        self.progress.setValue(current)
        if not self.worker or not self.worker.cancel_event.is_set():
            self._set_status(f"Processing {current} / {total}: {name}")

    def _processing_finished(self, report: BatchReport) -> None:
        self.last_report = report
        self._set_processing_state(False)
        self.progress.hide()
        self._set_status(f"{'Cancelled' if report.cancelled else 'Done'}: {len(report.successful)} / {len(report.files)} files")
        self._show_last_report()

    def _show_last_report(self) -> None:
        if self.last_report is not None and not self.processing:
            ReportDialog(self.last_report, self).exec()


def main() -> None:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PixelKit.Desktop")
    app = PixelKitApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    app.setStyle("Fusion")
    window = ImageMagickStudio()
    app.files_opened.connect(window.open_files)
    app.pending_files.extend(Path(argument) for argument in sys.argv[1:])
    QTimer.singleShot(0, app.dispatch_open_files)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
