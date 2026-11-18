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
from .theme import COLORS, CONTROL_HEIGHT, ICON_SIZE, RADIUS_LARGE, RADIUS_SMALL, SPACE, icon


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
    row.setSpacing(SPACE.sm)
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
    layout.setContentsMargins(SPACE.lg, SPACE.md, SPACE.lg, SPACE.md)
    layout.setSpacing(SPACE.md)
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


def set_semantic_state(widget: QWidget, state: str) -> None:
    if (widget.property("state") or "") != state:
        widget.setProperty("state", state)
        restyle(widget)


class StatusLabel(QLabel):
    def set_status(self, text: str, state: str = "") -> None:
        set_semantic_state(self, state)
        self.setText(text)


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
        layout.setContentsMargins(SPACE.lg, SPACE.lg, SPACE.lg, SPACE.lg)
        layout.setSpacing(SPACE.md)
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
        layout.setContentsMargins(SPACE.xs, SPACE.xs, SPACE.xs, SPACE.xs)
        layout.setSpacing(SPACE.xs)
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
        track = QRectF(self.rect())
        color = COLORS.primary if self.isChecked() else COLORS.border_strong
        if not self.isEnabled():
            color = COLORS.border
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(track, RADIUS_LARGE, RADIUS_LARGE)
        painter.setBrush(QColor(COLORS.surface))
        diameter = track.height() - 2 * SPACE.xs
        left = track.width() - SPACE.xs - diameter if self.isChecked() else SPACE.xs
        painter.drawEllipse(QRectF(left, SPACE.xs, diameter, diameter))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(COLORS.focus), 1))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS_LARGE, RADIUS_LARGE)


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
        inset = SPACE.xs + 0.5
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(inset, inset, -inset, -inset), RADIUS_SMALL, RADIUS_SMALL)


class PopupCard(QWidget):
    """A transparent window with a rounded, opaque content frame."""

    def __init__(self, parent: QWidget, window_type: Qt.WindowType) -> None:
        super().__init__(parent, window_type)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setProperty("popupSurface", True)
        shell.addWidget(frame)
        self.body = QVBoxLayout(frame)
        self.body.setContentsMargins(SPACE.lg, SPACE.md, SPACE.lg, SPACE.md)
        self.body.setSpacing(SPACE.sm)


class HelpPopover(PopupCard):
    """Usage notes shown under a "?" button; a click anywhere else closes it."""

    def __init__(self, title: str, paragraphs: tuple[str, ...], note: str, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAccessibleName(title)
        heading = QLabel(title)
        heading.setProperty("role", "heading")
        self.body.addWidget(heading)
        for paragraph in paragraphs:
            label = QLabel(paragraph)
            label.setWordWrap(True)
            self.body.addWidget(label)
        footer = QLabel(note)
        footer.setProperty("role", "status")
        footer.setWordWrap(True)
        self.body.addWidget(footer)

    def show_below(self, anchor: QWidget) -> None:
        available = anchor.screen().availableGeometry().adjusted(SPACE.sm, SPACE.sm, -SPACE.sm, -SPACE.sm)
        self.ensurePolished()
        self.setFixedWidth(min(400, available.width()))
        self.resize(self.width(), self.heightForWidth(self.width()))
        top_left = anchor.mapToGlobal(QPoint())
        x = top_left.x() + anchor.width() - self.width()
        y = top_left.y() + anchor.height() + SPACE.xs
        if y + self.height() > available.bottom() + 1:
            y = top_left.y() - self.height() - SPACE.xs
        self.move(max(available.left(), min(x, available.right() - self.width() + 1)),
                  max(available.top(), min(y, available.bottom() - self.height() + 1)))
        self.show()


class NoWheelComboBox(QComboBox):
    """Inputs inside a scrolling panel must not change while the user scrolls past them."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def __init__(self) -> None:
        super().__init__()
        self.lineEdit().setTextMargins(SPACE.sm, 0, 0, 0)

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def __init__(self) -> None:
        super().__init__()
        self.lineEdit().setTextMargins(SPACE.sm, 0, 0, 0)

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
        rect = self.rect().adjusted(SPACE.sm, 0, -SPACE.sm, 0)
        if not self.text():
            painter.setPen(self.palette().placeholderText().color())
        text = self.fontMetrics().elidedText(self.text() or self.placeholderText(), Qt.TextElideMode.ElideMiddle, rect.width())
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
