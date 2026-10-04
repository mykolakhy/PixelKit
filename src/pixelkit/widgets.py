"""Shared dark dropdowns for PixelKit's processing controls."""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QAbstractItemView, QComboBox, QFrame, QListView, QProxyStyle, QStyle, QStyledItemDelegate

DETAIL_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class DropdownStyle(QProxyStyle):
    def __init__(self, parent) -> None:
        # Own a separate base style; never take ownership of the app's style.
        super().__init__("Fusion")
        self.setParent(parent)

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_ComboBox_Popup:
            # Use a list popup, without the native menu's pale scroll bands.
            return 0
        return super().styleHint(hint, option, widget, returnData)


class DropdownDelegate(QStyledItemDelegate):
    def __init__(self, combo, colors) -> None:
        super().__init__(combo)
        self.combo = combo
        self.colors = colors

    @staticmethod
    def is_separator(index) -> bool:
        return index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator"

    def sizeHint(self, option, index) -> QSize:
        if self.is_separator(index):
            return QSize(100, 12)
        detail = index.data(DETAIL_ROLE)
        width = QFontMetrics(option.font).horizontalAdvance(str(index.data() or "")) + 64
        return QSize(width, 54 if detail else 38)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(1, 1, -1, -1)
        if self.is_separator(index):
            painter.setPen(QPen(QColor(self.colors["border"]), 1))
            painter.drawLine(rect.left() + 12, rect.center().y(), rect.right() - 12, rect.center().y())
            painter.restore()
            return

        highlighted = bool(option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver))
        current = index.row() == self.combo.currentIndex()
        if highlighted:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#302843"))
            painter.drawRoundedRect(rect, 7, 7)
        elif current:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#202a38"))
            painter.drawRoundedRect(rect, 7, 7)

        text_rect = rect.adjusted(12, 0, -38, 0)
        detail = index.data(DETAIL_ROLE)
        font = QFont(option.font)
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold if current else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(self.colors["text"]))
        title = QFontMetrics(font).elidedText(str(index.data() or ""), Qt.TextElideMode.ElideRight, text_rect.width())
        title_rect = QRect(text_rect.left(), rect.top() + 6, text_rect.width(), 22) if detail else text_rect
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)

        if detail:
            font.setPixelSize(12)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(QColor(self.colors["muted"]))
            subtitle = QFontMetrics(font).elidedText(str(detail), Qt.TextElideMode.ElideRight, text_rect.width())
            painter.drawText(QRect(text_rect.left(), rect.top() + 27, text_rect.width(), 19), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, subtitle)

        if current:
            pen = QPen(QColor(self.colors["teal"]), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            x, y = rect.right() - 23, rect.center().y()
            painter.drawPolyline([QPoint(x - 4, y), QPoint(x - 1, y + 3), QPoint(x + 5, y - 4)])
        painter.restore()


class DropdownComboBox(QComboBox):
    def __init__(self, colors, parent=None) -> None:
        super().__init__(parent)
        self.colors = colors
        self.dropdown_style = DropdownStyle(self)
        self.setStyle(self.dropdown_style)
        self.setMaxVisibleItems(8)
        view = QListView()
        view.setObjectName("dropdownList")
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setMouseTracking(True)
        view.setSpacing(2)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setView(view)
        self.setItemDelegate(DropdownDelegate(self, colors))
        view.setStyleSheet(f"""
            QListView#dropdownList {{
                background: {colors['card']}; color: {colors['text']};
                border: none; padding: 6px; outline: none;
            }}
        """)
        popup = view.parentWidget()
        self.popup = popup
        popup.setObjectName("dropdownPopup")
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        popup.setStyleSheet(f"""
            QFrame#dropdownPopup {{
                background: {colors['card']}; border: 1px solid {colors['control_border']};
                border-radius: 12px; padding: 4px;
            }}
        """)
        popup.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.popup and event.type() == QEvent.Type.Paint:
            # QComboBox's container paints a menu panel itself, bypassing the
            # stylesheet's rounded QFrame background on some platforms.
            painter = QPainter(watched)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor(self.colors["card"]))
            painter.setPen(QPen(QColor(self.colors["control_border"]), 1))
            painter.drawRoundedRect(watched.rect().adjusted(0, 0, -1, -1), 12, 12)
            painter.end()
            return True
        return super().eventFilter(watched, event)

    def showPopup(self) -> None:
        # Wide preset selectors still open a compact, easy-to-scan menu.
        has_details = any(self.itemData(row, DETAIL_ROLE) for row in range(self.count()))
        width = 390 if has_details else max(220, min(320, self.width()))
        popup = self.view().window()
        popup.setFixedWidth(width)
        super().showPopup()
        popup.setFixedWidth(width)
        available = self.screen().availableGeometry()
        position = self.mapToGlobal(QPoint(0, self.height() + 6))
        x = max(available.left() + 8, min(position.x(), available.right() - width - 8))
        y = position.y()
        if y + popup.height() > available.bottom() - 8:
            y = self.mapToGlobal(QPoint(0, 0)).y() - popup.height() - 6
        popup.move(x, max(available.top() + 8, y))
