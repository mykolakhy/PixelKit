"""An on-demand before/after viewer for completed conversions."""
from __future__ import annotations

import math
from pathlib import Path
from threading import Event

from PyQt6.QtCore import QPointF, QRectF, Qt, QThread, QTimer
from PyQt6.QtGui import QColor, QColorSpace, QImage, QImageReader, QPainter, QPen
from PyQt6.QtWidgets import QButtonGroup, QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget

from pixelkit.runtime import ProcessingCancelled, find_magick, run_magick

MAX_PIXELS = 32000000


def load_image(path: Path, cancelled=lambda: False) -> QImage:
    if cancelled():
        raise ProcessingCancelled()
    if not path.is_file():
        raise ValueError(f"Image no longer exists:\n{path}")
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and size.width() * size.height() > MAX_PIXELS:
        raise ValueError("This image is too large for full-resolution comparison (maximum 32 megapixels).")
    image = reader.read()
    if image.isNull():
        magick = find_magick()
        if not magick:
            raise ValueError(f"Cannot display this image: {reader.errorString()}")
        # Qt does not ship all of ImageMagick's codecs, including HEIC/AVIF.
        # Inspect first so a fallback cannot decode an unbounded full image.
        source = str(path) + "[0]"
        dimensions = run_magick([magick, "identify", "-format", "%w %h", source], capture_output=True, text=True, timeout=30, cancel_requested=cancelled)
        try:
            width, height = map(int, dimensions.stdout.strip().split())
        except ValueError:
            raise ValueError("Cannot read this image's dimensions.") from None
        if dimensions.returncode or width < 1 or height < 1 or width * height > MAX_PIXELS:
            raise ValueError("Cannot display this image, or it exceeds the 32-megapixel comparison limit.")
        decoded = run_magick([magick, source, "-auto-orient", "-colorspace", "sRGB", "-depth", "8", "png:-"], capture_output=True, timeout=30, cancel_requested=cancelled)
        if decoded.returncode:
            raise ValueError("ImageMagick could not decode this image for comparison.")
        image = QImage.fromData(decoded.stdout)
        if image.isNull():
            raise ValueError("Cannot display the decoded image.")
    if image.colorSpace().isValid():
        image = image.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    return image


class ComparisonLoader(QThread):
    def __init__(self, source: Path, output: Path, parent=None) -> None:
        super().__init__(parent)
        self.source, self.output = source, output
        self.cancelled = Event()
        self.images = None
        self.error = None

    def run(self) -> None:
        try:
            self.images = (load_image(self.source, self.cancelled.is_set), load_image(self.output, self.cancelled.is_set))
        except Exception as exc:
            self.error = str(exc)


class ComparisonCanvas(QWidget):
    def __init__(self, original: QImage, result: QImage) -> None:
        super().__init__()
        self.original = original
        self.result = result
        self.position = 50
        self.scale = 1.0
        self.setAccessibleName("Original on the left, processed result on the right")

    def set_scale(self, scale: float) -> None:
        self.scale = scale
        self.resize(max(1, math.ceil(self.result.width() * scale)), max(1, math.ceil(self.result.height() * scale)))
        self.update()

    def set_position(self, value: int) -> None:
        self.position = value
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        # Restrict checkerboard work to the exposed viewport at 100% zoom.
        area = event.rect()
        tile = 16
        for y in range(area.top() // tile * tile, area.bottom() + 1, tile):
            for x in range(area.left() // tile * tile, area.right() + 1, tile):
                color = QColor("#253140" if (x // tile + y // tile) % 2 else "#354254")
                painter.fillRect(x, y, tile, tile, color)
        rect = QRectF(0, 0, self.result.width() * self.scale, self.result.height() * self.scale)
        split = rect.width() * self.position / 100
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.width() * self.devicePixelRatioF() < self.result.width())
        painter.save()
        painter.setClipRect(QRectF(0, 0, split, self.height()))
        painter.drawImage(rect, self.original)
        painter.restore()
        painter.save()
        painter.setClipRect(QRectF(split, 0, self.width() - split, self.height()))
        painter.drawImage(rect, self.result)
        painter.restore()
        if 0 < self.position < 100:
            painter.setPen(QPen(QColor("#32d6c8"), 2))
            painter.drawLine(QPointF(split, 0), QPointF(split, self.height()))


class ComparisonDialog(QDialog):
    def __init__(self, source: Path, output: Path, parent=None) -> None:
        super().__init__(parent)
        self.source, self.output = source, output
        self.closing = False
        self.setWindowTitle("Compare original and result")
        self.setObjectName("comparisonDialog")
        self.resize(960, 700)
        self.setMinimumSize(620, 420)
        self.setStyleSheet("QDialog#comparisonDialog { background: #151b24; } QScrollArea#comparisonScroll { background: #0d1117; border: 1px solid #697d97; border-radius: 8px; } QPushButton:checked { background: #473582; border-color: #987cff; color: #ffffff; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        self.loading = QLabel("Loading images…")
        self.loading.setTextFormat(Qt.TextFormat.PlainText)
        self.loading.setWordWrap(True)
        layout.addWidget(self.loading, 1)
        self.loading_close = QPushButton("Close")
        self.loading_close.clicked.connect(self.reject)
        layout.addWidget(self.loading_close)
        self.loader = ComparisonLoader(source, output, self)
        self.loader.finished.connect(self._loaded)
        self.loader.start()

    def _loaded(self) -> None:
        if self.closing:
            super().reject()
            return
        if self.loader.error is not None:
            self.loading.setText("Could not open image comparison:\n" + self.loader.error)
            return
        original, result = self.loader.images
        source, output = self.source, self.output
        layout = self.layout()
        for widget in (self.loading, self.loading_close):
            layout.removeWidget(widget)
            widget.deleteLater()
        names = QHBoxLayout()
        for label, path, image in (("Original", source, original), ("Result", output, result)):
            info = QLabel(f"{label} · {image.width()} × {image.height()}")
            info.setToolTip(str(path))
            names.addWidget(info)
            names.addStretch()
        layout.addLayout(names)
        self.canvas = ComparisonCanvas(original, result)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("comparisonScroll")
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.canvas)
        layout.addWidget(self.scroll, 1)
        note = QLabel("Original is rescaled to the result dimensions for alignment. 100% shows one result pixel per display pixel." if original.size() != result.size() else "100% shows one image pixel per display pixel. Scroll to inspect details.")
        note.setWordWrap(True)
        note.setObjectName("infoLabel")
        layout.addWidget(note)
        if reader_animation(source) or reader_animation(output):
            layout.addWidget(QLabel("Animated images: comparing the first frame only."))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(50)
        self.slider.setAccessibleName("Before/after divider: zero shows result, 100 shows original")
        self.slider.valueChanged.connect(self.canvas.set_position)
        row = QHBoxLayout()
        row.addWidget(QLabel("More result"))
        row.addWidget(self.slider, 1)
        row.addWidget(QLabel("More original"))
        layout.addLayout(row)
        controls = QHBoxLayout()
        self.fit_button = QPushButton("Fit to window")
        self.actual_button = QPushButton("100%")
        group = QButtonGroup(self)
        for button in (self.fit_button, self.actual_button):
            button.setCheckable(True)
            group.addButton(button)
            controls.addWidget(button)
        self.fit_button.setChecked(True)
        self.fit_button.clicked.connect(self._update_scale)
        self.actual_button.clicked.connect(self._update_scale)
        controls.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        controls.addWidget(close)
        layout.addLayout(controls)
        QTimer.singleShot(0, self._update_scale)

    def reject(self) -> None:
        if self.loader.isRunning():
            self.closing = True
            self.loader.cancelled.set()
            self.loading.setText("Closing…")
        else:
            super().reject()

    def closeEvent(self, event) -> None:
        if self.loader.isRunning():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)

    def _update_scale(self) -> None:
        if self.actual_button.isChecked():
            scale = 1 / self.canvas.devicePixelRatioF()
        else:
            size = self.scroll.viewport().size()
            scale = min(size.width() / self.canvas.result.width(), size.height() / self.canvas.result.height(), 1 / self.canvas.devicePixelRatioF())
        self.canvas.set_scale(scale)
        if self.actual_button.isChecked():
            for bar in (self.scroll.horizontalScrollBar(), self.scroll.verticalScrollBar()):
                bar.setValue(bar.maximum() // 2)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "fit_button") and self.fit_button.isChecked():
            QTimer.singleShot(0, self._update_scale)


def reader_animation(path: Path) -> bool:
    return QImageReader(str(path)).supportsAnimation()
