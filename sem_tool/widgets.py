"""Small reusable controls for the desktop interface."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionFrame,
    QVBoxLayout,
    QWidget,
)

from .theme import icon


def section_heading(text: str, icon_name: str) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(9)
    symbol = QLabel()
    symbol.setProperty("role", "sectionIcon")
    symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
    symbol.setPixmap(icon(icon_name, "#3979e6").pixmap(17, 17))
    symbol.setFixedSize(28, 28)
    label = QLabel(text)
    label.setProperty("role", "heading")
    row.addWidget(symbol)
    row.addWidget(label)
    row.addStretch()
    return row


class ElidedLabel(QLabel):
    """Keep full text available while long file paths fit a narrow column."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().windowText().color())
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideMiddle, self.contentsRect().width())
        painter.drawText(self.contentsRect(), self.alignment(), text)


class EmptyState(QWidget):
    """A passive hint inside a view; drops and selection still reach the view."""

    def __init__(self, viewport: QWidget, title: str, detail: str, icon_name: str) -> None:
        super().__init__(viewport)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(viewport.rect())
        viewport.installEventFilter(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addStretch()
        symbol = QLabel()
        symbol.setProperty("role", "emptyIcon")
        symbol.setFixedSize(64, 64)
        symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        symbol.setPixmap(icon(icon_name, "#6794d8").pixmap(30, 30))
        layout.addWidget(symbol, 0, Qt.AlignmentFlag.AlignHCenter)
        self.title = QLabel(title)
        self.title.setProperty("role", "emptyTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail = QLabel(detail)
        self.detail.setProperty("role", "muted")
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)
        layout.addStretch()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        return super().eventFilter(watched, event)


class SegmentedControl(QFrame):
    def __init__(self, choices: list[str]) -> None:
        super().__init__()
        self.setObjectName("segmentedControl")
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)
        for choice in choices:
            button = QPushButton(choice)
            button.setProperty("variant", "segment")
            button.setCheckable(True)
            self.group.addButton(button)
            self.buttons[choice] = button
            layout.addWidget(button, 1)
        self.setCurrentText(choices[0])
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def currentText(self) -> str:
        button = self.group.checkedButton()
        assert button is not None
        return button.text()

    def setCurrentText(self, text: str) -> None:
        self.buttons[text].setChecked(True)


class ToggleSwitch(QCheckBox):
    def __init__(self, label: str) -> None:
        super().__init__()
        self.setAccessibleName(label)
        self.setFixedSize(42, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(42, 24)

    def hitButton(self, position) -> bool:
        return self.rect().contains(position)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, 2, 40, 20)
        color = "#2878f4" if self.isChecked() else "#ccd4e1"
        if not self.isEnabled():
            color = "#e1e5ec"
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(track, 10, 10)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(QRectF(23 if self.isChecked() else 5, 5, 14, 14))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#8fb7f5"), 1))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)


class PathLineEdit(QLineEdit):
    """Show both ends of long paths until the field is focused for editing."""

    def paintEvent(self, event) -> None:
        if self.hasFocus():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        option = QStyleOptionFrame()
        self.initStyleOption(option)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_PanelLineEdit, option, painter, self)
        painter.setFont(self.font())
        painter.setPen(self.palette().text().color())
        rect = self.rect().adjusted(10, 0, -10, 0)
        if not self.text():
            painter.setPen(self.palette().placeholderText().color())
        text = self.fontMetrics().elidedText(self.text() or self.placeholderText(), Qt.TextElideMode.ElideMiddle, rect.width())
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
