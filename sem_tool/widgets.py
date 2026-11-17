"""Small reusable controls for the desktop interface."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyleOptionFrame,
    QVBoxLayout,
    QWidget,
)

from .files import open_path, reveal_path
from .theme import CARD_MARGIN, COLORS, CONTROL_HEIGHT, ICON_SIZE, RADIUS_SMALL, SPACING, icon


def open_file_or_warn(parent: QWidget, path: Path) -> None:
    """Open a file with its default application; a moved file or a launcher error is reported."""
    if not path.exists():
        QMessageBox.warning(parent, "文件不存在", str(path))
        return
    try:
        open_path(path)
    except OSError as exc:
        QMessageBox.warning(parent, "无法打开文件", str(exc))


def reveal_file_or_warn(parent: QWidget, path: Path) -> None:
    """Show a file in the system file manager; when the file is gone its folder is opened instead."""
    try:
        reveal_path(path)
    except OSError as exc:
        QMessageBox.warning(parent, "无法定位文件", str(exc))


def section_heading(text: str, icon_name: str) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(9)
    symbol = QLabel()
    symbol.setProperty("role", "sectionIcon")
    symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
    symbol.setPixmap(icon(icon_name, COLORS.primary).pixmap(17, 17))
    symbol.setFixedSize(28, 28)
    label = QLabel(text)
    label.setProperty("role", "heading")
    row.addWidget(symbol)
    row.addWidget(label)
    row.addStretch()
    return row


def card() -> tuple[QFrame, QVBoxLayout]:
    """A white rounded panel with the padding every page uses."""
    frame = QFrame()
    frame.setProperty("panel", True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(CARD_MARGIN, CARD_MARGIN - 2, CARD_MARGIN, CARD_MARGIN - 2)
    layout.setSpacing(SPACING)
    return frame, layout


def make_icon_button(button: QAbstractButton, icon_name: str, tooltip: str, color: str = COLORS.icon) -> None:
    """Turn a button into a square, icon-only one that explains itself on hover."""
    button.setProperty("shape", "icon")
    button.setIcon(icon(icon_name, color))
    button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    button.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)


def describe(widget: QWidget, text: str) -> None:
    """Explain a control on hover and to assistive technology with the same words."""
    widget.setToolTip(text)
    widget.setAccessibleDescription(text)


def restyle(widget: QWidget) -> None:
    """Re-evaluate the stylesheet after a dynamic property changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


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
        layout.setSpacing(SPACING)
        layout.addStretch()
        symbol = QLabel()
        symbol.setProperty("role", "emptyIcon")
        symbol.setFixedSize(64, 64)
        symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        symbol.setPixmap(icon(icon_name, COLORS.focus).pixmap(30, 30))
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
        color = COLORS.primary if self.isChecked() else COLORS.border_strong
        if not self.isEnabled():
            color = COLORS.border
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(track, 10, 10)
        painter.setBrush(QColor(COLORS.surface))
        painter.drawEllipse(QRectF(23 if self.isChecked() else 5, 5, 14, 14))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(COLORS.focus), 1))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)


class ColorSwatchButton(QPushButton):
    """A square button filled with a colour, or marked "?" while the colour text is not usable."""

    def __init__(self, tooltip: str) -> None:
        super().__init__()
        self.color: str | None = None
        self.setProperty("shape", "icon")
        self.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)

    def set_color(self, color: str | None) -> None:
        self.color = color
        self.setText("" if color else "?")
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.color is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(COLORS.border_strong), 1))
        painter.setBrush(QColor(self.color))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(5.5, 5.5, -5.5, -5.5), RADIUS_SMALL, RADIUS_SMALL)


class HelpPopover(QFrame):
    """Usage notes shown under a "?" button; a click anywhere else closes it."""

    def __init__(self, html: str, parent: QWidget) -> None:
        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        super().__init__(parent, flags)
        self.setObjectName("helpPopover")
        # Only the rounded card is painted; the corners outside it stay see-through.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_MARGIN, SPACING, CARD_MARGIN, SPACING)
        text = QLabel(html)
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        text.setFixedWidth(340)
        layout.addWidget(text)

    def show_below(self, anchor: QWidget) -> None:
        self.adjustSize()
        available = anchor.screen().availableGeometry()
        position = anchor.mapToGlobal(QPoint(anchor.width() - self.width(), anchor.height() + 6))
        position.setX(max(available.left(), min(position.x(), available.right() - self.width())))
        position.setY(max(available.top(), min(position.y(), available.bottom() - self.height())))
        self.move(position)
        self.show()


# A spin box draws its number flush with the frame; this lines it up with the text of a line edit.
SPIN_TEXT_MARGIN = 10


class NoWheelComboBox(QComboBox):
    """Inputs inside a scrolling panel must not change while the user scrolls past them."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def __init__(self) -> None:
        super().__init__()
        self.lineEdit().setTextMargins(SPIN_TEXT_MARGIN, 0, 0, 0)

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def __init__(self) -> None:
        super().__init__()
        self.lineEdit().setTextMargins(SPIN_TEXT_MARGIN, 0, 0, 0)

    def wheelEvent(self, event) -> None:
        event.ignore()


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
