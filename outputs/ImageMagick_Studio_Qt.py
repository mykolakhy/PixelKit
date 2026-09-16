from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, QSize, pyqtSignal
from PyQt6.QtGui import QIcon, QImageReader, QIntValidator, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
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


APP_TITLE = "PixelKit"
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
ICON_PATH = APP_DIR / "PixelKit.png"
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".heif", ".ico"}
OUTPUT_FORMATS = ["Автоматично", "JPG", "PNG", "WEBP", "AVIF", "GIF", "BMP", "TIFF"]
NO_WINDOW_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def find_magick() -> str | None:
    bundled = APP_DIR / "imagemagick" / "magick.exe"
    candidates = [
        str(bundled),
        shutil.which("magick"),
        r"C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe",
        r"C:\Program Files\ImageMagick-7.1.1-Q16-HDRI\magick.exe",
    ]
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)


def human_size(value: int) -> str:
    size = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{int(size)} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} Б"


def run_magick(command: list[str], **kwargs):
    """Run ImageMagick without creating a console window on Windows."""
    if NO_WINDOW_FLAGS:
        kwargs["creationflags"] = NO_WINDOW_FLAGS
    return subprocess.run(command, **kwargs)


class DropListWidget(QListWidget):
    files_dropped = pyqtSignal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)

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
                files.extend(sorted(item for item in path.iterdir() if item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES))
            elif path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
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


class PreviewWorker(QThread):
    ready = pyqtSignal(str)
    failed = pyqtSignal()

    def __init__(self, magick: str, source: Path, background: str) -> None:
        super().__init__()
        self.magick = magick
        self.source = source
        self.background = background

    def run(self) -> None:
        preview_path = Path(tempfile.gettempdir()) / f"imagemagick_studio_preview_{self.source.stem}.png"
        command = [
            self.magick,
            str(self.source),
            "-auto-orient",
            "-thumbnail",
            "1000x700^",
            "-gravity",
            "center",
            "-extent",
            "1000x700",
            "-background",
            self.background,
            str(preview_path),
        ]
        try:
            result = run_magick(command, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and preview_path.exists():
                self.ready.emit(str(preview_path))
            else:
                self.failed.emit()
        except (OSError, subprocess.SubprocessError):
            self.failed.emit()


class BatchWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int, int, list, str)

    def __init__(self, jobs: list[tuple[list[str], Path]], output_dir: Path) -> None:
        super().__init__()
        self.jobs = jobs
        self.output_dir = output_dir

    def run(self) -> None:
        completed = 0
        errors: list[tuple[str, str]] = []
        for index, (command, output) in enumerate(self.jobs, start=1):
            source_name = Path(command[1]).name
            try:
                result = run_magick(command, capture_output=True, text=True, timeout=300)
                if result.returncode == 0 and output.exists():
                    completed += 1
                else:
                    error = result.stderr.strip() or result.stdout.strip() or "ImageMagick повернув невідому помилку."
                    errors.append((source_name, error))
            except subprocess.TimeoutExpired:
                errors.append((source_name, "Обробка перевищила ліміт у 5 хвилин."))
            except OSError as exc:
                errors.append((source_name, str(exc)))
            self.progress.emit(index, len(self.jobs), source_name)
        self.finished.emit(completed, len(self.jobs), errors, str(self.output_dir))


class ImageMagickStudio(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.magick = find_magick()
        self.sources: list[Path] = []
        self.default_output = True
        self.preview_thread: PreviewWorker | None = None
        self.worker: BatchWorker | None = None
        self.preview_pixmap = QPixmap()
        self.colors = {
            "bg": "#0d1117",
            "card": "#151b24",
            "card_alt": "#1b2330",
            "input": "#202a38",
            "border": "#2b3747",
            "text": "#f4f7fb",
            "muted": "#93a0b3",
            "accent": "#7c5cff",
            "accent_hover": "#9278ff",
            "teal": "#32d6c8",
        }
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1240, 820)
        self.setMinimumSize(1040, 700)
        self._build_ui()
        self._set_sources([])

        if not self.magick:
            self._show_message(QMessageBox.Icon.Warning, "ImageMagick не знайдено", "Не вдалося знайти magick.exe. Перевір PATH або встановлення ImageMagick.")

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
        subtitle = QLabel("Швидкий workflow для resize, compression та конвертації")
        subtitle.setObjectName("appSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        badge = QLabel("●  QT 6  •  HIGH DPI")
        badge.setObjectName("badge")
        header.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(18)
        left_content = QWidget()
        left_content.setObjectName("leftContent")
        left = QVBoxLayout(left_content)
        left.setContentsMargins(0, 0, 8, 0)
        left.setSpacing(14)
        right = QVBoxLayout()
        right.setSpacing(14)
        left_scroll = QScrollArea()
        left_scroll.setObjectName("leftScroll")
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_content)
        content.addWidget(left_scroll, 5)
        content.addLayout(right, 4)
        outer.addLayout(content, 1)

        left.addWidget(self._source_card())
        left.addWidget(self._resize_card())
        left.addWidget(self._quality_card())
        left.addStretch(1)

        right.addWidget(self._preview_card(), 1)
        right.addWidget(self._output_card())

        footer = QHBoxLayout()
        self.status_label = QLabel("Готово до роботи")
        self.status_label.setObjectName("statusLabel")
        footer.addWidget(self.status_label)
        footer.addStretch(1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(220)
        self.progress.setFixedHeight(8)
        self.progress.hide()
        footer.addWidget(self.progress)
        outer.addLayout(footer)

    def _section_header(self, number: str, title: str, subtitle: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        label = QLabel(f"{number}   {title.upper()}")
        label.setObjectName("sectionTitle")
        note = QLabel(subtitle)
        note.setObjectName("sectionSubtitle")
        layout.addWidget(label)
        layout.addWidget(note)
        return widget

    def _card(self, layout: QVBoxLayout) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 20)
        card_layout.setSpacing(12)
        layout.addWidget(card)
        return card

    def _source_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(self._section_header("01", "Вхідні зображення", "Один файл, кілька файлів або ціла папка"))

        self.source_list = DropListWidget()
        self.source_list.setFixedHeight(145)
        self.source_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.source_list.files_dropped.connect(self._set_sources)
        layout.addWidget(self.source_list)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        one = QPushButton("＋  Один файл")
        one.clicked.connect(self._choose_one)
        many = QPushButton("＋  Кілька файлів")
        many.clicked.connect(self._choose_many)
        folder = QPushButton("▣  Папка")
        folder.clicked.connect(self._choose_folder)
        clear = QPushButton("Очистити")
        clear.setObjectName("subtleButton")
        clear.clicked.connect(lambda: self._set_sources([]))
        buttons.addWidget(one)
        buttons.addWidget(many)
        buttons.addWidget(folder)
        buttons.addStretch(1)
        buttons.addWidget(clear)
        layout.addLayout(buttons)
        self.source_info = QLabel("Вибери зображення або перетягни його сюди")
        self.source_info.setObjectName("infoLabel")
        layout.addWidget(self.source_info)
        return card

    def _resize_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(self._section_header("02", "Розмір", "Залиш поля порожніми, щоб зберегти оригінальний розмір"))
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Режим зміни розміру"))
        self.resize_mode = QComboBox()
        self.resize_mode.addItems(["Ширина × висота", "По довшій стороні"])
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
        self.width_edit.setValidator(QIntValidator(1, 100000, self))
        self.height_edit = QLineEdit()
        self.height_edit.setPlaceholderText("Original")
        self.height_edit.setValidator(QIntValidator(1, 100000, self))
        standard_grid.addWidget(QLabel("Ширина"), 0, 0)
        standard_grid.addWidget(self.width_edit, 0, 1)
        standard_grid.addWidget(QLabel("px"), 0, 2)
        standard_grid.addWidget(QLabel("Висота"), 1, 0)
        standard_grid.addWidget(self.height_edit, 1, 1)
        standard_grid.addWidget(QLabel("px"), 1, 2)
        self.keep_ratio = QCheckBox("Зберігати пропорції")
        self.keep_ratio.setChecked(True)
        standard_grid.addWidget(self.keep_ratio, 0, 3, 2, 1)
        standard_grid.setColumnStretch(1, 1)
        self.resize_stack.addWidget(standard_page)

        long_side_page = QWidget()
        long_side_row = QHBoxLayout(long_side_page)
        long_side_row.setContentsMargins(0, 0, 0, 0)
        long_side_row.addWidget(QLabel("Довша сторона"))
        self.long_side_edit = QLineEdit()
        self.long_side_edit.setPlaceholderText("Напр. 1600")
        self.long_side_edit.setValidator(QIntValidator(1, 100000, self))
        long_side_row.addWidget(self.long_side_edit, 1)
        long_side_row.addWidget(QLabel("px"))
        long_side_note = QLabel("Пропорції зберігаються автоматично")
        long_side_note.setObjectName("infoLabel")
        long_side_row.addSpacing(10)
        long_side_row.addWidget(long_side_note)
        self.resize_stack.addWidget(long_side_page)
        self.resize_mode.currentIndexChanged.connect(self.resize_stack.setCurrentIndex)
        layout.addWidget(self.resize_stack)
        return card

    def _quality_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(self._section_header("03", "Якість і метадані", "Оптимізуй розмір файлу без зайвих налаштувань"))
        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("Якість"))
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(10, 100)
        self.quality_slider.setValue(82)
        self.quality_label = QLabel("82")
        self.quality_label.setObjectName("valueBadge")
        self.quality_label.setFixedWidth(42)
        self.quality_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.quality_slider.valueChanged.connect(lambda value: self.quality_label.setText(str(value)))
        quality_row.addWidget(self.quality_slider, 1)
        quality_row.addWidget(self.quality_label)
        layout.addLayout(quality_row)
        self.strip_metadata = QCheckBox("Видалити EXIF та інші метадані")
        self.strip_metadata.setChecked(True)
        layout.addWidget(self.strip_metadata)
        background_row = QHBoxLayout()
        background_row.addWidget(QLabel("Фон для JPG"))
        self.background_edit = QLineEdit("#ffffff")
        self.background_edit.setMaximumWidth(125)
        background_row.addStretch(1)
        background_row.addWidget(self.background_edit)
        layout.addLayout(background_row)
        return card

    def _preview_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(self._section_header("PREVIEW", "Попередній перегляд", "Перший файл зі списку"))
        heading.addStretch(1)
        self.preview_format = QLabel("READY")
        self.preview_format.setObjectName("valueBadge")
        heading.addWidget(self.preview_format, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(heading)
        self.preview = QLabel("Перетягни зображення сюди\nабо обери файл ліворуч")
        self.preview.setObjectName("preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(360, 300)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.preview, 1)
        return card

    def _output_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(self._section_header("04", "Експорт", "Формат і місце збереження результату"))
        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("Формат"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(OUTPUT_FORMATS)
        self.format_combo.currentTextChanged.connect(self._format_changed)
        format_row.addStretch(1)
        format_row.addWidget(self.format_combo)
        layout.addLayout(format_row)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Файл або папка результатів")
        self.output_button = QPushButton("Обрати…")
        self.output_button.clicked.connect(self._choose_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.output_button)
        layout.addLayout(output_row)
        self.process_button = QPushButton("⚡  Обробити та зберегти")
        self.process_button.setObjectName("primaryButton")
        self.process_button.setMinimumHeight(46)
        self.process_button.clicked.connect(self._start_processing)
        layout.addWidget(self.process_button)
        return card

    def _stylesheet(self) -> str:
        c = self.colors
        return f"""
            QWidget {{ color: {c['text']}; font-family: 'Segoe UI'; font-size: 10pt; }}
            QMainWindow, #page {{ background: {c['bg']}; }}
            QWidget#leftContent, QScrollArea#leftScroll {{ background: transparent; border: none; }}
            QFrame#card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 16px; }}
            QLabel#logo {{ background: {c['accent']}; color: white; border-radius: 14px; font-size: 25px; font-weight: 700; }}
            QLabel#appTitle {{ color: {c['text']}; font-size: 25px; font-weight: 700; }}
            QLabel#appSubtitle {{ color: {c['muted']}; font-size: 10pt; }}
            QLabel#badge {{ background: #172d2d; color: {c['teal']}; border-radius: 12px; padding: 8px 12px; font-size: 9pt; font-weight: 700; }}
            QLabel#sectionTitle {{ color: {c['text']}; font-size: 11pt; font-weight: 700; letter-spacing: 0.5px; }}
            QLabel#sectionSubtitle, QLabel#infoLabel {{ color: {c['muted']}; font-size: 9pt; }}
            QLabel#statusLabel {{ color: {c['muted']}; font-size: 9pt; }}
            QLabel#valueBadge {{ background: #282241; color: #bdb0ff; border-radius: 8px; padding: 5px 8px; font-weight: 700; }}
            QLabel#preview {{ background: {c['card_alt']}; border: 1px dashed #3a4658; border-radius: 12px; color: {c['muted']}; font-size: 11pt; }}
            QLineEdit, QComboBox {{ background: {c['input']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 9px; padding: 8px 10px; selection-background-color: {c['accent']}; }}
            QLineEdit:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
            QComboBox::drop-down {{ border: none; width: 26px; }}
            QComboBox QAbstractItemView {{ background: {c['input']}; color: {c['text']}; selection-background-color: {c['accent']}; border: 1px solid {c['border']}; }}
            QListWidget {{ background: {c['input']}; color: #dce5f0; border: 1px solid {c['border']}; border-radius: 11px; padding: 8px; outline: none; }}
            QListWidget::item {{ padding: 7px 9px; border-radius: 6px; }}
            QListWidget::item:selected {{ background: {c['accent']}; color: white; }}
            QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px 0 2px 2px; }}
            QScrollBar::handle:vertical {{ background: #3a4658; border-radius: 4px; min-height: 28px; }}
            QScrollBar::handle:vertical:hover {{ background: #566783; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            QCheckBox {{ color: #dce5f0; spacing: 8px; }}
            QCheckBox::indicator {{ width: 17px; height: 17px; border-radius: 5px; border: 1px solid #4a5870; background: {c['input']}; }}
            QCheckBox::indicator:checked {{ background: {c['accent']}; border: 1px solid {c['accent']}; }}
            QPushButton {{ background: {c['input']}; color: #dce5f0; border: 1px solid {c['border']}; border-radius: 9px; padding: 9px 13px; font-weight: 600; }}
            QPushButton:hover {{ background: #2a3749; border-color: #4a5870; color: white; }}
            QPushButton:pressed {{ background: #344257; }}
            QPushButton#subtleButton {{ background: transparent; border: none; color: {c['muted']}; }}
            QPushButton#subtleButton:hover {{ color: white; background: #202a38; }}
            QPushButton#primaryButton {{ background: {c['accent']}; color: white; border: none; font-size: 11pt; font-weight: 700; }}
            QPushButton#primaryButton:hover {{ background: {c['accent_hover']}; }}
            QPushButton#primaryButton:disabled {{ background: #413d58; color: #9b98ad; }}
            QSlider::groove:horizontal {{ height: 6px; background: {c['input']}; border-radius: 3px; }}
            QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 3px; }}
            QSlider::handle:horizontal {{ width: 18px; height: 18px; margin: -6px 0; background: white; border: 3px solid {c['accent']}; border-radius: 9px; }}
            QProgressBar {{ background: {c['input']}; border: none; border-radius: 4px; }}
            QProgressBar::chunk {{ background: {c['teal']}; border-radius: 4px; }}
        """

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _show_message(
        self,
        icon: QMessageBox.Icon,
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    ) -> QMessageBox.StandardButton:
        dialog = QDialog(self)
        dialog.setObjectName("messageDialog")
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(420)
        dialog.setMaximumWidth(560)
        dialog.setStyleSheet(
            f"""
            QDialog#messageDialog {{ background: {self.colors['card']}; }}
            QLabel#messageIcon {{ background: {self.colors['accent']}; color: white; border-radius: 23px; font-size: 21pt; font-weight: 500; }}
            QLabel#messageTitle {{ color: {self.colors['text']}; font-size: 12pt; font-weight: 700; }}
            QLabel#messageBody {{ color: #dce5f0; font-size: 10pt; }}
            QPushButton {{ background: {self.colors['input']}; color: {self.colors['text']}; border: 1px solid {self.colors['border']}; border-radius: 8px; min-width: 76px; padding: 8px 15px; }}
            QPushButton:hover {{ background: {self.colors['accent']}; border-color: {self.colors['accent']}; color: white; }}
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
        body_label = QLabel(text)
        body_label.setObjectName("messageBody")
        body_label.setWordWrap(True)
        body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_box.addWidget(title_label)
        text_box.addWidget(body_label)
        content.addLayout(text_box, 1)
        layout.addLayout(content)
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        button_map = (
            (QMessageBox.StandardButton.No, "Ні"),
            (QMessageBox.StandardButton.Yes, "Так"),
            (QMessageBox.StandardButton.Ok, "OK"),
        )
        for button, label in button_map:
            if buttons & button:
                action = QPushButton(label)
                action.setDefault(button == QMessageBox.StandardButton.Ok)
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
        if not unique:
            item = QListWidgetItem("Порожньо — перетягни файли сюди")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.source_list.addItem(item)
            self.source_info.setText("Вибери зображення або перетягни його сюди")
            self.output_edit.clear()
            self.preview_pixmap = QPixmap()
            self.preview.clear()
            self.preview.setText("Перетягни зображення сюди\nабо обери файл ліворуч")
            self.preview_format.setText("READY")
            self._update_output_mode()
            return

        for path in unique:
            self.source_list.addItem(path.name)
        first = unique[0]
        reader = QImageReader(str(first))
        image_size = reader.size()
        dimensions = f"{image_size.width()}x{image_size.height()}" if image_size.isValid() else "розмір невідомий"
        extra = f"  •  та ще {len(unique) - 1} файлів" if len(unique) > 1 else ""
        self.source_info.setText(f"{dimensions}  •  {human_size(first.stat().st_size)}  •  {first.suffix.upper().lstrip('.')}" + extra)
        self.default_output = True
        self._set_default_output()
        self._update_output_mode()
        self._start_preview(first)
        self._set_status(f"Вибрано файлів: {len(unique)}")

    def _choose_one(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Вибери зображення", "", "Зображення (*.jpg *.jpeg *.png *.webp *.gif *.bmp *.tif *.tiff *.avif *.heic *.ico);;Усі файли (*.*)")
        if path:
            self._set_sources([Path(path)])

    def _choose_many(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Вибери зображення", "", "Зображення (*.jpg *.jpeg *.png *.webp *.gif *.bmp *.tif *.tiff *.avif *.heic *.ico);;Усі файли (*.*)")
        if paths:
            self._set_sources([Path(path) for path in paths])

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Вибери папку із зображеннями")
        if folder:
            paths = sorted((path for path in Path(folder).iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES), key=lambda path: path.name.lower())
            if paths:
                self._set_sources(paths)
            else:
                self._show_message(QMessageBox.Icon.Information, "Зображення не знайдено", "У цій папці немає підтримуваних зображень.")

    def _clear_sources(self) -> None:
        if not self.worker or not self.worker.isRunning():
            self._set_sources([])

    def _start_preview(self, source: Path) -> None:
        if not self.magick:
            return
        self.preview.setText("Готую прев’ю…")
        self.preview_thread = PreviewWorker(self.magick, source, self.colors["card_alt"])
        self.preview_thread.ready.connect(self._show_preview)
        self.preview_thread.failed.connect(lambda: self.preview.setText("Не вдалося показати прев’ю"))
        self.preview_thread.start()

    def _show_preview(self, path: str) -> None:
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self.preview_pixmap = pixmap
            self.preview.setScaledContents(False)
            self.preview_format.setText("PREVIEW")
            self._fit_preview()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_preview()

    def _fit_preview(self) -> None:
        if not self.preview_pixmap.isNull():
            available = self.preview.size() - QSize(24, 24)
            scaled = self.preview_pixmap.scaled(max(1, available.width()), max(1, available.height()), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.preview.setPixmap(scaled)

    def _selected_extension(self, source: Path | None = None) -> str:
        selected = self.format_combo.currentText().lower()
        current_source = source or (self.sources[0] if self.sources else None)
        if selected == "автоматично":
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
        self.output_edit.setPlaceholderText("Папка результатів" if batch else "Файл результату")
        self.output_button.setText("Обрати папку…" if batch else "Обрати…")

    def _format_changed(self) -> None:
        if self.default_output:
            self._set_default_output()

    def _choose_output(self) -> None:
        if len(self.sources) > 1:
            folder = QFileDialog.getExistingDirectory(self, "Вибери папку для результатів")
            if folder:
                self.output_edit.setText(folder)
                self.default_output = False
            return
        selected, _ = QFileDialog.getSaveFileName(self, "Зберегти результат", self.output_edit.text(), "Зображення (*.jpg *.jpeg *.png *.webp *.avif *.gif *.bmp *.tif *.tiff);;Усі файли (*.*)")
        if selected:
            self.output_edit.setText(selected)
            self.default_output = False

    def _build_command(self, source: Path, output: Path) -> list[str]:
        command = [self.magick, str(source), "-auto-orient"]
        if self.resize_mode.currentIndex() == 1:
            long_side = self.long_side_edit.text().strip()
            if long_side:
                command += ["-resize", f"{long_side}x{long_side}"]
        else:
            width = self.width_edit.text().strip()
            height = self.height_edit.text().strip()
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
        if not self.magick:
            self._show_message(QMessageBox.Icon.Critical, "ImageMagick не знайдено", "Перевір встановлення ImageMagick та PATH.")
            return
        if not self.sources:
            self._show_message(QMessageBox.Icon.Information, "Немає зображень", "Спочатку додай хоча б одне зображення.")
            return
        output_text = self.output_edit.text().strip()
        if not output_text:
            self._show_message(QMessageBox.Icon.Information, "Немає місця збереження", "Вкажи файл або папку для результату.")
            return
        output = Path(output_text)
        batch = len(self.sources) > 1
        if batch:
            output.mkdir(parents=True, exist_ok=True)
            used: set[Path] = set()
            outputs: list[Path] = []
            for source in self.sources:
                extension = self._selected_extension(source)
                candidate = output / f"{source.stem}_optimized.{extension}"
                counter = 2
                while candidate.resolve() in used or candidate.exists():
                    candidate = output / f"{source.stem}_optimized_{counter}.{extension}"
                    counter += 1
                used.add(candidate.resolve())
                outputs.append(candidate)
            existing = [path for path in outputs if path.exists()]
            if existing and not self._confirm("Файли вже існують", f"У папці вже є {len(existing)} результатів. Перезаписати?"):
                return
        else:
            if not output.suffix:
                output = output.with_suffix(f".{self._selected_extension()}")
                self.output_edit.setText(str(output))
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.resolve() == self.sources[0].resolve():
                self._show_message(QMessageBox.Icon.Critical, "Небезпечне перезаписування", "Файл результату має відрізнятися від вхідного.")
                return
            if output.exists() and not self._confirm("Файл уже існує", f"Перезаписати файл?\n\n{output}"):
                return
            outputs = [output]

        jobs = [(self._build_command(source, target), target) for source, target in zip(self.sources, outputs)]
        self.process_button.setEnabled(False)
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.progress.show()
        self._set_status(f"Обробляю 0 / {len(jobs)}…")
        self.worker = BatchWorker(jobs, output if batch else output.parent)
        self.worker.progress.connect(lambda current, total, name: self._set_progress(current, total, name))
        self.worker.finished.connect(self._processing_finished)
        self.worker.start()

    def _set_progress(self, current: int, total: int, name: str) -> None:
        self.progress.setValue(current)
        self._set_status(f"Обробляю {current} / {total}: {name}")

    def _processing_finished(self, completed: int, total: int, errors: list, output_dir: str) -> None:
        self.process_button.setEnabled(True)
        self.progress.hide()
        self._set_status(f"Готово: {completed} / {total} файлів")
        if errors:
            lines = "\n".join(f"• {name}: {message[:180]}" for name, message in errors[:5])
            if len(errors) > 5:
                lines += f"\n• … та ще {len(errors) - 5} помилок"
            self._show_message(QMessageBox.Icon.Warning, "Обробку завершено з помилками", f"Успішно: {completed} / {total}\n\nРезультати: {output_dir}\n\n{lines}")
        else:
            self._show_message(QMessageBox.Icon.Information, "Готово", f"Оброблено файлів: {completed}\n\nРезультати збережено в:\n{output_dir}")


def main() -> None:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PixelKit.Desktop")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    app.setStyle("Fusion")
    window = ImageMagickStudio()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
