"""A short, local introduction; the caller owns first-run persistence."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen, QShowEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


_COLORS = {
    "background": "#151b24",
    "surface": "#202a38",
    "border": "#2b3747",
    "text": "#f4f7fb",
    "muted": "#b3c0d2",
    "accent": "#7452eb",
    "teal": "#32d6c8",
}


def _font(widget: QWidget, points: float, *, bold: bool = False) -> QFont:
    font = QFont(widget.font())
    font.setPointSizeF(points)
    font.setBold(bold)
    return font


class _Illustration(QWidget):
    """Resolution-independent, decorative media and interface illustrations."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(142 if kind == "compression" else 76)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if kind == "compression":
            self.setAccessibleName("Smaller image and video files")
            self.setAccessibleDescription(
                "Illustration of image and video files becoming smaller, ready to share. "
                "Actual file sizes depend on the chosen settings."
            )
        else:
            names = {
                "add": "Add a media file",
                "settings": "Choose a preset or adjust settings",
                "save": "Save a new result",
            }
            self.setAccessibleName(names[kind])
            self.setAccessibleDescription("Decorative preview of this step in PixelKit.")

    def sizeHint(self) -> QSize:
        return QSize(570, 174) if self.kind == "compression" else QSize(160, 86)

    @staticmethod
    def _rect(painter: QPainter, rect: QRectF, color: str, radius: float = 12,
              border: str | None = None) -> None:
        painter.setPen(QPen(QColor(border), 1) if border else Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(rect, radius, radius)

    @staticmethod
    def _line(painter: QPainter, start: QPointF, end: QPointF, color: str,
              width: float = 2) -> None:
        painter.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(start, end)

    @staticmethod
    def _text(painter: QPainter, rect: QRectF, text: str, color: str,
              size: float = 10, *, bold: bool = False) -> None:
        font = QFont(painter.font())
        font.setPointSizeF(size)
        font.setBold(bold)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _media_card(self, painter: QPainter, rect: QRectF, *, video: bool,
                    compact: bool = False) -> None:
        self._rect(painter, rect, "#243244", 12, "#48607a")
        inset = 8 if compact else 10
        preview = rect.adjusted(inset, inset, -inset, -24)
        self._rect(painter, preview, "#302843" if video else "#1b494b", 7)
        if video:
            center = preview.center()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#c0afff"))
            path = QPainterPath()
            path.moveTo(center.x() - 6, center.y() - 9)
            path.lineTo(center.x() + 9, center.y())
            path.lineTo(center.x() - 6, center.y() + 9)
            path.closeSubpath()
            painter.drawPath(path)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#66e3ce"))
            painter.drawEllipse(QPointF(preview.right() - 17, preview.top() + 14), 4, 4)
            path = QPainterPath()
            path.moveTo(preview.left() + 7, preview.bottom() - 8)
            path.lineTo(preview.left() + preview.width() * .36, preview.top() + 15)
            path.lineTo(preview.left() + preview.width() * .59, preview.bottom() - 17)
            path.lineTo(preview.left() + preview.width() * .75, preview.top() + 26)
            path.lineTo(preview.right() - 6, preview.bottom() - 8)
            path.closeSubpath()
            painter.drawPath(path)
        self._text(painter, QRectF(rect.left(), rect.bottom() - 23, rect.width(), 20),
                   "VIDEO" if video else "IMAGE", _COLORS["muted"], 7.5, bold=True)

    def _compression(self, painter: QPainter) -> None:
        # Card size is a visual metaphor, not a promised compression ratio.
        self._rect(painter, QRectF(0, 3, 570, 168), "#101720", 20, _COLORS["border"])
        self._rect(painter, QRectF(28, 21, 202, 132), "#17212e", 18)
        self._media_card(painter, QRectF(45, 35, 109, 94), video=False)
        self._media_card(painter, QRectF(124, 60, 88, 79), video=True, compact=True)

        self._line(painter, QPointF(258, 87), QPointF(310, 87), "#8a75d7", 3)
        self._line(painter, QPointF(302, 79), QPointF(311, 87), "#8a75d7", 3)
        self._line(painter, QPointF(302, 95), QPointF(311, 87), "#8a75d7", 3)

        self._rect(painter, QRectF(340, 21, 202, 132), "#152c2d", 18)
        self._media_card(painter, QRectF(362, 43, 84, 76), video=False, compact=True)
        self._media_card(painter, QRectF(427, 66, 70, 64), video=True, compact=True)
        self._rect(painter, QRectF(499, 33, 25, 25), "#32d6c8", 12)
        self._line(painter, QPointF(506, 45), QPointF(510, 49), "#12322d", 2)
        self._line(painter, QPointF(510, 49), QPointF(517, 41), "#12322d", 2)

    def _step(self, painter: QPainter) -> None:
        self._rect(painter, QRectF(2, 3, 156, 80), "#101720", 10, "#34465b")
        for x, color in ((12, "#7452eb"), (20, "#52617c"), (28, "#52617c")):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(x, 12), 2, 2)
        self._line(painter, QPointF(9, 21), QPointF(151, 21), "#2b3747", 1)
        if self.kind == "add":
            self._rect(painter, QRectF(13, 31, 134, 40), "#24203b", 7, "#7452eb")
            self._line(painter, QPointF(28, 45), QPointF(28, 57), "#c9bbff")
            self._line(painter, QPointF(22, 51), QPointF(34, 51), "#c9bbff")
            self._text(painter, QRectF(40, 35, 98, 31), "Add file…", "#e1d8ff", 9, bold=True)
        elif self.kind == "settings":
            self._text(painter, QRectF(12, 29, 54, 19), "Preset", _COLORS["muted"], 8)
            self._rect(painter, QRectF(67, 29, 80, 20), "#29233f", 5, "#7452eb")
            self._text(painter, QRectF(69, 29, 65, 20), "Balanced", "#e1d8ff", 8)
            self._line(painter, QPointF(137, 37), QPointF(140, 40), "#bdb0ff", 1)
            self._line(painter, QPointF(140, 40), QPointF(143, 37), "#bdb0ff", 1)
            self._line(painter, QPointF(18, 63), QPointF(140, 63), "#52617c", 4)
            self._line(painter, QPointF(18, 63), QPointF(98, 63), "#7452eb", 4)
            painter.setPen(QPen(QColor("#c9bbff"), 1))
            painter.setBrush(QColor("#7452eb"))
            painter.drawEllipse(QPointF(98, 63), 5, 5)
        else:
            self._rect(painter, QRectF(13, 29, 134, 18), "#1c3035", 5)
            self._text(painter, QRectF(14, 29, 132, 18), "New output file", "#9aded5", 7.5)
            self._rect(painter, QRectF(13, 53, 134, 21), "#7452eb", 5)
            self._text(painter, QRectF(13, 53, 134, 21), "Process & save", "#ffffff", 8, bold=True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        canvas_width, canvas_height = (570, 174) if self.kind == "compression" else (160, 86)
        scale = min(self.width() / canvas_width, self.height() / canvas_height)
        painter.translate((self.width() - canvas_width * scale) / 2,
                          (self.height() - canvas_height * scale) / 2)
        painter.scale(scale, scale)
        if self.kind == "compression":
            self._compression(painter)
        else:
            self._step(painter)


class OnboardingDialog(QDialog):
    """Two pages with no file operations or stored preferences.

    After ``exec()`` returns, the caller may open its file chooser when
    ``add_file_requested`` is true. Skip, Escape and window close reject the
    dialog; only the final "Add first file…" action accepts and requests a file.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.add_file_requested = False
        self.setWindowTitle("Welcome to PixelKit")
        self.setObjectName("onboardingDialog")
        self.setModal(True)
        self.setMinimumSize(620, 460)
        self.resize(680, 500)
        self.setAccessibleName("Welcome to PixelKit")
        self.setAccessibleDescription("A two-page introduction. Press Escape to skip at any time.")
        self.setFont(_font(self, 10.5))
        self.setStyleSheet("""
            QDialog#onboardingDialog { background: #151b24; color: #f4f7fb; font-size: 10.5pt; }
            QLabel { color: #f4f7fb; background: transparent; }
            QLabel#onboardingEyebrow { color: #32d6c8; }
            QLabel#onboardingBody, QLabel#onboardingProgress { color: #b3c0d2; }
            QFrame#onboardingPrivacy { background: #172d2d; border: 1px solid #254443; border-radius: 11px; }
            QFrame#onboardingStep { background: #1b2431; border: 1px solid #34465b; border-radius: 12px; }
            QLabel#onboardingStepNumber { color: #c5b5ff; }
            QPushButton { background: #202a38; color: #f4f7fb; border: 1px solid #697d97; border-radius: 9px; padding: 9px 15px; font-size: 10.5pt; font-weight: 600; }
            QPushButton:hover { background: #2a3749; }
            QPushButton:pressed { background: #344257; }
            QPushButton:focus { border-color: #32d6c8; }
            QPushButton#onboardingSkip { background: transparent; color: #b3c0d2; border-color: transparent; }
            QPushButton#onboardingSkip:hover { background: #202a38; color: white; }
            QPushButton#onboardingSkip:focus { border-color: #32d6c8; }
            QPushButton#onboardingPrimary { background: #7452eb; border-color: #7452eb; color: white; }
            QPushButton#onboardingPrimary:hover { background: #7958ee; }
            QPushButton#onboardingPrimary:pressed { background: #6240d4; }
            QPushButton#onboardingPrimary:focus { border-color: #f4f7fb; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(16)
        header = QHBoxLayout()
        eyebrow = self._label("WELCOME TO PIXELKIT", 9, bold=True, name="onboardingEyebrow")
        eyebrow.setWordWrap(False)
        header.addWidget(eyebrow)
        header.addStretch()
        self.progress_label = self._label("Step 1 of 2", 9, name="onboardingProgress")
        self.progress_label.setWordWrap(False)
        self.progress_label.setAccessibleName("Step 1 of 2: Smaller files. Easier sharing.")
        header.addWidget(self.progress_label)
        layout.addLayout(header)

        self.page_stack = QStackedWidget()
        self.page_stack.setAccessibleName("PixelKit introduction pages")
        self.page_stack.addWidget(self._benefits_page())
        self.page_stack.addWidget(self._steps_page())
        layout.addWidget(self.page_stack, 1)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.skip_button = QPushButton("Skip")
        self.skip_button.setObjectName("onboardingSkip")
        self.skip_button.setAutoDefault(False)
        self.skip_button.setShortcut(QKeySequence("Alt+S"))
        self.skip_button.setAccessibleName("Skip introduction")
        self.skip_button.setAccessibleDescription("Close this introduction and use PixelKit. Escape also skips.")
        self.skip_button.clicked.connect(self.reject)
        actions.addWidget(self.skip_button)
        actions.addStretch()
        self.back_button = QPushButton("Back")
        self.back_button.setAutoDefault(False)
        self.back_button.setShortcut(QKeySequence("Alt+B"))
        self.back_button.setAccessibleName("Back to step 1")
        self.back_button.setAccessibleDescription("Return to the first page of the introduction.")
        self.back_button.clicked.connect(lambda: self._show_page(0))
        actions.addWidget(self.back_button)
        self.next_button = QPushButton("Next")
        self.next_button.setObjectName("onboardingPrimary")
        self.next_button.setDefault(True)
        self.next_button.setMinimumWidth(154)
        self.next_button.clicked.connect(self._advance)
        actions.addWidget(self.next_button)
        layout.addLayout(actions)
        self.setTabOrder(self.skip_button, self.back_button)
        self.setTabOrder(self.back_button, self.next_button)
        self._show_page(0)

    def _label(self, text: str, points: float = 10.5, *, bold: bool = False,
               name: str = "") -> QLabel:
        label = QLabel(text)
        label.setFont(_font(self, points, bold=bold))
        # The main window sets a font size on all QWidget descendants. An
        # explicit local rule preserves this hierarchy when it is our parent.
        label.setStyleSheet(f"font-size: {points}pt; font-weight: {700 if bold else 400};")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setObjectName(name)
        label.setAccessibleName(text)
        return label

    def _page(self, title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setAccessibleName(title)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._label(title, 21, bold=True, name="onboardingHeading"))
        layout.addWidget(self._label(description, name="onboardingBody"))
        return page, layout

    def _benefits_page(self) -> QWidget:
        page, layout = self._page(
            "Smaller files. Easier sharing.",
            "Resize and convert images. Compress images and videos, "
            "with a file-size limit when you need one.",
        )
        layout.addWidget(_Illustration("compression"), 1)
        privacy = QFrame()
        privacy.setObjectName("onboardingPrivacy")
        privacy_layout = QVBoxLayout(privacy)
        privacy_layout.setContentsMargins(14, 10, 14, 10)
        privacy_layout.setSpacing(3)
        privacy_layout.addWidget(self._label(
            "Processed on your computer. Originals stay untouched.", 10, bold=True,
        ))
        privacy_layout.addWidget(self._label("Save a new result, ready to share.", 9.5, name="onboardingBody"))
        layout.addWidget(privacy)
        return page

    def _steps_page(self) -> QWidget:
        page, layout = self._page(
            "Start with your first file",
            "Three simple steps take you from original to ready to share.",
        )
        steps = QHBoxLayout()
        steps.setSpacing(12)
        for number, kind, title, description in (
            ("01", "add", "Add file", "Choose an image or video from your computer."),
            ("02", "settings", "Choose settings", "Pick a preset, or adjust size and quality."),
            ("03", "save", "Save result", "Choose an output location, then process and save."),
        ):
            card = QFrame()
            card.setObjectName("onboardingStep")
            card.setAccessibleName(f"Step {number}: {title}")
            card.setAccessibleDescription(description)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 12, 12, 14)
            card_layout.setSpacing(9)
            card_layout.addWidget(self._label(number, 9, bold=True, name="onboardingStepNumber"))
            card_layout.addWidget(_Illustration(kind), 1)
            card_layout.addWidget(self._label(title, 11, bold=True))
            body = self._label(description, 10, name="onboardingBody")
            body.setMinimumHeight(body.fontMetrics().lineSpacing() * 3)
            body.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            card_layout.addWidget(body)
            steps.addWidget(card, 1)
        layout.addLayout(steps, 1)
        return page

    def _show_page(self, index: int) -> None:
        self.page_stack.setCurrentIndex(index)
        self.back_button.setVisible(index == 1)
        self.progress_label.setText(f"Step {index + 1} of 2")
        title = self.page_stack.currentWidget().accessibleName()
        self.progress_label.setAccessibleName(f"Step {index + 1} of 2: {title}")
        self.page_stack.setAccessibleDescription(f"Step {index + 1} of 2. {title}")
        self.next_button.setText("Next" if index == 0 else "Add first file…")
        self.next_button.setShortcut(QKeySequence("Alt+N" if index == 0 else "Alt+A"))
        self.next_button.setAccessibleName("Next: step 2" if index == 0 else "Add first file")
        self.next_button.setAccessibleDescription(
            "Continue to the three steps for using PixelKit."
            if index == 0 else "Close this introduction, then choose an image or video."
        )
        self.next_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _advance(self) -> None:
        if self.page_stack.currentIndex() == 0:
            self._show_page(1)
        else:
            self.add_file_requested = True
            self.accept()

    def reject(self) -> None:
        self.add_file_requested = False
        super().reject()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.next_button.setFocus(Qt.FocusReason.OtherFocusReason)
