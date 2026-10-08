"""A short, local introduction; the caller owns first-run persistence."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QResizeEvent, QShowEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pixelkit.onboarding_art import OnboardingHeroIllustration
from pixelkit.runtime import resource_path


def _font(widget: QWidget, points: float, *, bold: bool = False) -> QFont:
    font = QFont(widget.font())
    font.setPointSizeF(points)
    font.setBold(bold)
    return font


class _AssuranceIcon(QWidget):
    """Small decorative symbols; the adjacent text carries the meaning."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(24, 24)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#32d6c8"), 1.6, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.kind == "local":
            painter.drawRoundedRect(QRectF(3, 4, 18, 12), 2, 2)
            painter.drawLine(QPointF(12, 16), QPointF(12, 20))
            painter.drawLine(QPointF(8, 20), QPointF(16, 20))
        else:
            path = QPainterPath(QPointF(12, 3))
            path.lineTo(20, 6)
            path.lineTo(19, 13)
            path.quadTo(18, 18, 12, 21)
            path.quadTo(6, 18, 5, 13)
            path.lineTo(4, 6)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawLine(QPointF(8, 11), QPointF(11, 14))
            painter.drawLine(QPointF(11, 14), QPointF(16, 9))


class _Illustration(QWidget):
    """Resolution-independent previews of the three workflow steps."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(110)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        names = {
            "add": "Add a media file",
            "settings": "Choose a preset or adjust settings",
            "save": "Save a new result",
        }
        self.setAccessibleName(names[kind])
        self.setAccessibleDescription("Decorative preview of this step in PixelKit.")

    def sizeHint(self) -> QSize:
        return QSize(160, 86)

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
            self._text(painter, QRectF(12, 29, 54, 19), "Preset", "#b3c0d2", 8)
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
        scale = min(self.width() / 160, self.height() / 86)
        painter.translate((self.width() - 160 * scale) / 2,
                          (self.height() - 86 * scale) / 2)
        painter.scale(scale, scale)
        self._step(painter)


class OnboardingPage(QWidget):
    """An introduction that fills the main window's content stack.

    The caller owns page removal and persistence. ``finished`` emits once when
    Get started, Skip or Escape completes the introduction.
    """

    finished = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._finished = False
        self._layout_growth = -1.0
        self._responsive_labels: list[tuple[QLabel, float, bool]] = []
        self._description_labels: list[QLabel] = []
        self._card_layouts: list[QVBoxLayout] = []
        self.setObjectName("onboardingPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAccessibleName("Welcome to PixelKit")
        self.setAccessibleDescription("A two-page introduction. Press Escape to skip at any time.")
        self.setStyleSheet("""
            QWidget#onboardingPage { background: #0d1117; color: #f4f7fb; }
            QLabel { color: #f4f7fb; background: transparent; }
            QLabel#onboardingEyebrow { color: #32d6c8; }
            QLabel#onboardingBody, QLabel#onboardingProgress { color: #b3c0d2; }
            QFrame#onboardingStep { background: #151b24; border: 1px solid #34465b; border-radius: 16px; }
            QLabel#onboardingStepNumber { color: #c5b5ff; }
            QPushButton { background: #202a38; color: #f4f7fb; border: 1px solid #697d97; border-radius: 10px; padding: 12px 22px; font-size: 12pt; font-weight: 600; }
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

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(40, 28, 40, 28)
        self._layout.setSpacing(22)
        header = QHBoxLayout()
        eyebrow = self._label("WELCOME TO PIXELKIT", 10, bold=True, name="onboardingEyebrow")
        eyebrow.setWordWrap(False)
        header.addWidget(eyebrow)
        header.addStretch()
        self.progress_label = self._label("Step 1 of 2", 10, name="onboardingProgress")
        self.progress_label.setWordWrap(False)
        self.progress_label.setAccessibleName("Step 1 of 2: Smaller files. Easier sharing.")
        header.addWidget(self.progress_label)
        self._layout.addLayout(header)

        self.page_stack = QStackedWidget()
        self.page_stack.setAccessibleName("PixelKit introduction pages")
        self.page_stack.addWidget(self._benefits_page())
        self.page_stack.addWidget(self._steps_page())
        self._layout.addWidget(self.page_stack, 1)

        actions = QHBoxLayout()
        actions.setSpacing(14)
        self.skip_button = QPushButton("Skip")
        self.skip_button.setObjectName("onboardingSkip")
        self.skip_button.setShortcut(QKeySequence("Alt+S"))
        self.skip_button.setAccessibleName("Skip introduction")
        self.skip_button.setAccessibleDescription("Finish this introduction and use PixelKit. Escape also skips.")
        self.skip_button.clicked.connect(self.dismiss)
        actions.addWidget(self.skip_button)
        actions.addStretch()
        self.back_button = QPushButton("Back")
        self.back_button.setShortcut(QKeySequence("Alt+B"))
        self.back_button.setAccessibleName("Back to step 1")
        self.back_button.setAccessibleDescription("Return to the first page of the introduction.")
        self.back_button.clicked.connect(lambda: self._show_page(0))
        actions.addWidget(self.back_button)
        self.next_button = QPushButton("Next")
        self.next_button.setObjectName("onboardingPrimary")
        self.next_button.setMinimumWidth(188)
        self.next_button.clicked.connect(self._advance)
        actions.addWidget(self.next_button)
        self._layout.addLayout(actions)
        self.setTabOrder(self.skip_button, self.back_button)
        self.setTabOrder(self.back_button, self.next_button)

        self._escape_action = QAction(self)
        self._escape_action.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        self._escape_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._escape_action.triggered.connect(self.dismiss)
        self.addAction(self._escape_action)
        self._return_action = QAction(self)
        self._return_action.setShortcuts([QKeySequence(Qt.Key.Key_Return), QKeySequence(Qt.Key.Key_Enter)])
        self._return_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._return_action.triggered.connect(self._activate_keyboard_action)
        self.addAction(self._return_action)
        self._show_page(0)

    def _label(self, text: str, points: float = 12, *, bold: bool = False,
               name: str = "") -> QLabel:
        label = QLabel(text)
        self._set_label_font(label, points, bold)
        self._responsive_labels.append((label, points, bold))
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setObjectName(name)
        label.setAccessibleName(text)
        return label

    def _set_label_font(self, label: QLabel, points: float, bold: bool) -> None:
        label.setFont(_font(self, points, bold=bold))
        # The main window sets a font size on QWidget descendants. Explicit
        # point sizes preserve the hierarchy and Qt's normal DPI scaling.
        label.setStyleSheet(f"font-size: {points:.2f}pt; font-weight: {700 if bold else 400};")

    def _page(self, title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setAccessibleName(title)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._label(title, 28, bold=True, name="onboardingHeading"))
        layout.addWidget(self._label(description, name="onboardingBody"))
        return page, layout

    def _benefits_page(self) -> QWidget:
        page = QWidget()
        page.setAccessibleName("Smaller files. Easier sharing.")
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(28)
        copy = QWidget()
        copy_layout = QVBoxLayout(copy)
        copy_layout.setContentsMargins(0, 0, 0, 0)
        copy_layout.setSpacing(0)
        copy_layout.addStretch()

        identity = QHBoxLayout()
        identity.setSpacing(12)
        logo = QLabel()
        logo.setObjectName("onboardingLogo")
        logo.setAccessibleName("PixelKit logo")
        logo.setFixedSize(44, 44)
        logo.setPixmap(QIcon(str(resource_path("PixelKit.png"))).pixmap(QSize(44, 44), self.devicePixelRatioF()))
        identity.addWidget(logo)
        identity.addWidget(self._label("PixelKit", 19, bold=True))
        identity.addStretch()
        copy_layout.addLayout(identity)
        copy_layout.addSpacing(16)
        heading = self._label("Smaller files.\nEasier sharing.", 36, bold=True, name="onboardingHeading")
        copy_layout.addWidget(heading)
        copy_layout.addSpacing(16)
        copy_layout.addWidget(self._label("Resize and convert images.\nCompress images and videos.", 12, name="onboardingBody"))
        copy_layout.addSpacing(8)
        copy_layout.addWidget(self._label("Set a file-size limit when you need one.", 12, name="onboardingBody"))
        copy_layout.addSpacing(22)

        for kind, text in (("local", "Processed on your computer"), ("originals", "Originals stay untouched")):
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(_AssuranceIcon(kind))
            assurance = self._label(text, 11.5, bold=True, name="onboardingBody")
            assurance.setWordWrap(False)
            row.addWidget(assurance)
            row.addStretch()
            copy_layout.addLayout(row)
            if kind == "local":
                copy_layout.addSpacing(8)
        copy_layout.addStretch()
        layout.addWidget(copy, 43)
        layout.addWidget(OnboardingHeroIllustration(), 57)
        return page

    def _steps_page(self) -> QWidget:
        page, layout = self._page(
            "Start with your first file",
            "Three simple steps take you from original to ready to share.",
        )
        steps = QHBoxLayout()
        steps.setSpacing(20)
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
            card_layout.setContentsMargins(20, 18, 20, 20)
            card_layout.setSpacing(14)
            self._card_layouts.append(card_layout)
            card_layout.addWidget(self._label(number, 10, bold=True, name="onboardingStepNumber"))
            card_layout.addWidget(_Illustration(kind), 1)
            card_layout.addWidget(self._label(title, 14, bold=True))
            body = self._label(description, 11.5, name="onboardingBody")
            body.setMinimumHeight(body.fontMetrics().lineSpacing() * 3)
            body.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self._description_labels.append(body)
            card_layout.addWidget(body)
            steps.addWidget(card, 1)
        layout.addLayout(steps, 1)
        return page

    def _show_page(self, index: int) -> None:
        if self._finished:
            return
        self.page_stack.setCurrentIndex(index)
        self.back_button.setVisible(index == 1)
        self.progress_label.setText(f"Step {index + 1} of 2")
        title = self.page_stack.currentWidget().accessibleName()
        self.progress_label.setAccessibleName(f"Step {index + 1} of 2: {title}")
        self.page_stack.setAccessibleDescription(f"Step {index + 1} of 2. {title}")
        self.next_button.setText("Next" if index == 0 else "Get started")
        self.next_button.setShortcut(QKeySequence("Alt+N" if index == 0 else "Alt+G"))
        self.next_button.setAccessibleName("Next: step 2" if index == 0 else "Get started")
        self.next_button.setAccessibleDescription(
            "Continue to the three steps for using PixelKit."
            if index == 0 else "Finish this introduction and use PixelKit."
        )
        self.next_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _activate_keyboard_action(self) -> None:
        focus = QApplication.focusWidget()
        if focus in (self.skip_button, self.back_button, self.next_button) and focus.isVisible():
            focus.click()
        else:
            self.next_button.click()

    def _advance(self) -> None:
        if self._finished:
            return
        if self.page_stack.currentIndex() == 0:
            self._show_page(1)
        else:
            self.dismiss()

    def dismiss(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.finished.emit()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        growth = round(max(0.0, min(1.0, (self.width() - 1040) / 360)), 2)
        if growth == self._layout_growth:
            return
        self._layout_growth = growth
        horizontal = round(40 + growth * 24)
        vertical = round(28 + growth * 12)
        self._layout.setContentsMargins(horizontal, vertical, horizontal, vertical)
        for label, points, bold in self._responsive_labels:
            self._set_label_font(label, points * (1 + growth * .12), bold)
        for label in self._description_labels:
            label.setMinimumHeight(label.fontMetrics().lineSpacing() * 3)
        for card_layout in self._card_layouts:
            inset = round(20 + growth * 8)
            card_layout.setContentsMargins(inset, inset, inset, inset)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._finished:
            self.next_button.setFocus(Qt.FocusReason.OtherFocusReason)
