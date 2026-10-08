"""Editable, resolution-independent artwork for the welcome screen."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import QSizePolicy, QWidget


class OnboardingHeroIllustration(QWidget):
    """Original media travels through PixelKit into a separate, shareable result.

    The artwork uses a fixed design canvas and a proportional fit. It illustrates
    the workflow rather than a particular output size or compression ratio.
    """

    _CANVAS_WIDTH = 600.0
    _CANVAS_HEIGHT = 430.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(240)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName("Images and videos, ready to share")
        self.setAccessibleDescription(
            "Original image and video files pass through PixelKit to create a "
            "separate result. The originals stay unchanged. This illustration "
            "does not represent a specific file size or compression ratio."
        )

    def sizeHint(self) -> QSize:
        return QSize(600, 430)

    @staticmethod
    def _color(value: str, alpha: int = 255) -> QColor:
        color = QColor(value)
        color.setAlpha(alpha)
        return color

    @staticmethod
    def _rounded(painter: QPainter, rect: QRectF, fill, radius: float,
                 border: str | None = None) -> None:
        painter.setBrush(fill)
        painter.setPen(QPen(QColor(border), 1.2) if border else Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, radius, radius)

    @staticmethod
    def _text(painter: QPainter, rect: QRectF, text: str, color: str,
              pixels: int = 13, *, bold: bool = False) -> None:
        font = QFont(painter.font())
        font.setPixelSize(pixels)
        font.setBold(bold)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    @staticmethod
    def _stroke(painter: QPainter, path: QPainterPath, color: QColor,
                width: float = 1.5) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)

    def _glow(self, painter: QPainter, center: QPointF, radius: float,
              color: str, strength: int) -> None:
        glow = QRadialGradient(center, radius)
        glow.setColorAt(0, self._color(color, strength))
        glow.setColorAt(.55, self._color(color, strength // 3))
        glow.setColorAt(1, self._color(color, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, radius, radius)

    def _backdrop(self, painter: QPainter) -> None:
        self._glow(painter, QPointF(225, 192), 205, "#7452eb", 35)
        self._glow(painter, QPointF(454, 235), 174, "#32d6c8", 26)
        # The quiet pixel field and crop corners echo the application mark.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color("#61738e", 52))
        for x in range(326, 568, 24):
            for y in (48, 72, 376, 400):
                painter.drawEllipse(QPointF(x, y), 1, 1)
        for origin, directions, color in (
            (QPointF(29, 63), (1, 1), "#9c85ed"),
            (QPointF(578, 382), (-1, -1), "#5db5b0"),
        ):
            path = QPainterPath(origin + QPointF(0, 24 * directions[1]))
            path.lineTo(origin)
            path.lineTo(origin + QPointF(24 * directions[0], 0))
            self._stroke(painter, path, self._color(color, 90), 1.7)

    def _landscape(self, painter: QPainter, rect: QRectF) -> None:
        """A small geometric dusk print, repeated on the saved result."""
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, 9, 9)
        painter.setClipPath(clip)
        painter.translate(rect.topLeft())
        scale = max(rect.width() / 240, rect.height() / 144)
        painter.translate((rect.width() - 240 * scale) / 2,
                          (rect.height() - 144 * scale) / 2)
        painter.scale(scale, scale)

        sky = QLinearGradient(0, 0, 185, 144)
        sky.setColorAt(0, QColor("#5557a5"))
        sky.setColorAt(.5, QColor("#38496f"))
        sky.setColorAt(1, QColor("#266566"))
        painter.fillRect(QRectF(0, 0, 240, 144), sky)
        sun_glow = QRadialGradient(QPointF(169, 45), 53)
        sun_glow.setColorAt(0, self._color("#88f5df", 85))
        sun_glow.setColorAt(1, self._color("#88f5df", 0))
        painter.setBrush(sun_glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(169, 45), 53, 53)
        painter.setBrush(QColor("#a2f4df"))
        painter.drawEllipse(QPointF(169, 45), 22, 22)

        distant = QPainterPath(QPointF(-10, 110))
        distant.lineTo(47, 53)
        distant.lineTo(88, 95)
        distant.lineTo(127, 72)
        distant.lineTo(192, 119)
        distant.lineTo(250, 91)
        distant.lineTo(250, 154)
        distant.lineTo(-10, 154)
        distant.closeSubpath()
        painter.setBrush(QColor("#9290c5"))
        painter.drawPath(distant)

        ridge = QPainterPath(QPointF(-10, 137))
        ridge.lineTo(69, 95)
        ridge.lineTo(108, 113)
        ridge.lineTo(179, 79)
        ridge.lineTo(250, 113)
        ridge.lineTo(250, 154)
        ridge.lineTo(-10, 154)
        ridge.closeSubpath()
        middle = QLinearGradient(60, 80, 200, 144)
        middle.setColorAt(0, QColor("#295777"))
        middle.setColorAt(1, QColor("#2c8b89"))
        painter.setBrush(middle)
        painter.drawPath(ridge)

        foreground = QPainterPath(QPointF(-10, 136))
        foreground.cubicTo(41, 122, 89, 145, 137, 134)
        foreground.cubicTo(170, 128, 196, 117, 250, 133)
        foreground.lineTo(250, 154)
        foreground.lineTo(-10, 154)
        foreground.closeSubpath()
        painter.setBrush(QColor("#143d53"))
        painter.drawPath(foreground)
        painter.restore()

    def _shadow(self, painter: QPainter, rect: QRectF, radius: float) -> None:
        for spread, alpha in ((9, 18), (5, 24), (2, 36)):
            self._rounded(painter, rect.adjusted(-spread, 5, spread, 10 + spread),
                          self._color("#03060c", alpha), radius + spread)

    def _original(self, painter: QPainter, rect: QRectF, angle: float,
                  *, video: bool = False) -> None:
        painter.save()
        painter.translate(rect.center())
        painter.rotate(angle)
        card = QRectF(-rect.width() / 2, -rect.height() / 2, rect.width(), rect.height())
        self._shadow(painter, card, 15)
        surface = QLinearGradient(card.topLeft(), card.bottomRight())
        surface.setColorAt(0, QColor("#2b344a"))
        surface.setColorAt(1, QColor("#1a2636"))
        self._rounded(painter, card, surface, 15, "#4c5d78")
        preview = card.adjusted(8, 8, -8, -35)
        self._landscape(painter, preview)
        if video:
            center = preview.center()
            self._rounded(painter, QRectF(center.x() - 22, center.y() - 22, 44, 44),
                          self._color("#1b1732", 205), 22)
            play = QPainterPath(QPointF(center.x() - 5, center.y() - 10))
            play.lineTo(center.x() + 11, center.y())
            play.lineTo(center.x() - 5, center.y() + 10)
            play.closeSubpath()
            painter.setBrush(QColor("#f0eaff"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(play)
        self._text(painter, QRectF(card.left() + 12, card.bottom() - 29, 95, 21),
                   "Video" if video else "Image", "#d2dbeb", 13, bold=True)
        # Abstract media marks have no quantitative meaning.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color("#a599d5" if video else "#74c9c1", 150))
        for index, bar_width in enumerate((19, 10, 6)):
            painter.drawRoundedRect(QRectF(card.right() - 52 + index * 17,
                                          card.bottom() - 20, bar_width, 3), 1.5, 1.5)
        painter.restore()

    def _connections(self, painter: QPainter) -> None:
        for start, first, second, end in (
            (QPointF(265, 155), QPointF(275, 155), QPointF(276, 181), QPointF(293, 181)),
            (QPointF(270, 286), QPointF(308, 286), QPointF(313, 258), QPointF(324, 229)),
        ):
            route = QPainterPath(start)
            route.cubicTo(first, second, end)
            self._stroke(painter, route, self._color("#9d83f5", 150), 2)
        route = QPainterPath(QPointF(365, 193))
        route.cubicTo(381, 193, 378, 238, 391, 238)
        self._stroke(painter, route, self._color("#5cd4c8", 190), 2)
        # Small pixels turn the route into a brand-specific transformation.
        for rect, color in (
            (QRectF(370, 173, 6, 6), "#b8a3ff"),
            (QRectF(378, 201, 5, 5), "#97dedc"),
            (QRectF(381, 225, 7, 7), "#52d8ca"),
        ):
            self._rounded(painter, rect, QColor(color), 1.8)

    def _processor(self, painter: QPainter) -> None:
        self._glow(painter, QPointF(329, 193), 73, "#9168ff", 75)
        rect = QRectF(293, 157, 72, 72)
        self._shadow(painter, rect, 20)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, QColor("#b594ff"))
        gradient.setColorAt(.45, QColor("#8459f0"))
        gradient.setColorAt(1, QColor("#5635c9"))
        self._rounded(painter, rect, gradient, 20, "#bc9fff")
        # Four crop corners and three rising pixels mirror the PixelKit icon.
        for origin, directions in (
            (QPointF(308, 172), (1, 1)), (QPointF(350, 172), (-1, 1)),
            (QPointF(308, 214), (1, -1)), (QPointF(350, 214), (-1, -1)),
        ):
            corner = QPainterPath(origin + QPointF(0, directions[1] * 10))
            corner.lineTo(origin)
            corner.lineTo(origin + QPointF(directions[0] * 10, 0))
            self._stroke(painter, corner, QColor("#f4f0ff"), 4.5)
        for rect, color in (
            (QRectF(317, 198, 5, 5), "#e9e2ff"),
            (QRectF(324, 189, 7, 7), "#f4efff"),
            (QRectF(334, 179, 10, 10), "#66efdb"),
        ):
            self._rounded(painter, rect, QColor(color), 2)

    def _result(self, painter: QPainter) -> None:
        card = QRectF(391, 121, 180, 231)
        self._shadow(painter, card, 18)
        surface = QLinearGradient(card.topLeft(), card.bottomRight())
        surface.setColorAt(0, QColor("#243e48"))
        surface.setColorAt(1, QColor("#183136"))
        self._rounded(painter, card, surface, 18, "#457d7d")
        self._landscape(painter, QRectF(403, 133, 156, 94))
        self._text(painter, QRectF(406, 241, 151, 23), "New result", "#f1f7f8", 17, bold=True)
        self._text(painter, QRectF(406, 266, 151, 22), "Ready to share", "#a8cec9", 13)
        divider = QPainterPath(QPointF(406, 302))
        divider.lineTo(555, 302)
        self._stroke(painter, divider, self._color("#83beb8", 55), 1)
        self._text(painter, QRectF(406, 312, 151, 22), "Saved separately", "#bed3d7", 12)

        badge = QRectF(546, 106, 35, 35)
        self._rounded(painter, badge.adjusted(-4, -4, 4, 4), QColor("#10272c"), 22)
        self._rounded(painter, badge, QColor("#45ddcc"), 17.5)
        check = QPainterPath(QPointF(556, 123))
        check.lineTo(562, 129)
        check.lineTo(572, 118)
        self._stroke(painter, check, QColor("#123b3b"), 2.7)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        scale = min(self.width() / self._CANVAS_WIDTH, self.height() / self._CANVAS_HEIGHT)
        painter.translate((self.width() - self._CANVAS_WIDTH * scale) / 2,
                          (self.height() - self._CANVAS_HEIGHT * scale) / 2)
        painter.scale(scale, scale)
        self._backdrop(painter)
        self._text(painter, QRectF(48, 39, 200, 25), "ORIGINAL FILES", "#9eabc1", 13, bold=True)
        self._connections(painter)
        self._original(painter, QRectF(42, 82, 228, 171), -6)
        self._original(painter, QRectF(88, 257, 188, 116), 5, video=True)
        self._processor(painter)
        self._result(painter)
