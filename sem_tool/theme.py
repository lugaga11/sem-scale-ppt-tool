"""Shared, compact desktop styling for the application and its dialogs.

Every colour, radius, layout spacing, control height and font size of the interface is defined once in this module.
The stylesheet is a template over these tokens and custom-painted widgets read the same values, so a
new control picks a token instead of introducing another literal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from string import Template

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QFrame, QProxyStyle, QStyle, QWidget

ASSETS = Path(__file__).resolve().parent / "assets"


@dataclass(frozen=True)
class Palette:
    # Brand colour and its states; "tint" backs everything selected or softly emphasised.
    primary: str = "#2878f4"
    primary_hover: str = "#216be0"
    primary_pressed: str = "#1a5dc9"
    primary_disabled: str = "#acc9f7"
    tint: str = "#edf4ff"
    tint_strong: str = "#dceaff"
    # Surfaces, from the window background up to the controls drawn on a card.
    window: str = "#f2f5fa"
    surface: str = "#ffffff"
    input: str = "#fafcff"
    subtle: str = "#f4f7fb"
    hover: str = "#e9eff7"
    pressed: str = "#dee9f7"
    disabled_surface: str = "#f6f7f9"
    # Lines.
    border: str = "#dfe6f0"
    border_strong: str = "#cbd6e4"
    divider: str = "#edf0f5"
    focus: str = "#91b7f3"
    # Text.
    heading: str = "#20314c"
    text: str = "#344054"
    secondary: str = "#536179"
    muted: str = "#7b899f"
    disabled: str = "#aeb7c5"
    success: str = "#226c42"
    success_surface: str = "#def2e5"
    success_border: str = "#a8d2b7"
    success_hover: str = "#ccebd8"
    warning: str = "#b15e35"
    warning_surface: str = "#fff0e7"
    warning_border: str = "#e6c1ae"
    warning_hover: str = "#ffe3d1"
    error: str = "#a62c28"
    error_surface: str = "#fff1f0"
    error_border: str = "#ce6a64"
    # The colour the SVG assets are drawn in until icon() recolours them.
    icon: str = "#667b9a"


@dataclass(frozen=True)
class Spacing:
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16


COLORS = Palette()
SPACE = Spacing()

RADIUS_SMALL = 4  # tags and check boxes
RADIUS = 8  # buttons, inputs, list rows
RADIUS_LARGE = 12  # cards and pop-ups
CONTROL_HEIGHT = 32  # buttons and inputs
COMPACT_HEIGHT = 28  # controls inside the image editors and table rows
PRIMARY_HEIGHT = 40  # the one main action of a page
NAV_BAR_HEIGHT = 52  # three main page tabs, including the surrounding space
ICON_SIZE = 16
CHECK_SIZE = 18
FONT_CAPTION = 12
FONT_CONTROL = 13
FONT_BODY = 14
FONT_TITLE = 16

STYLESHEET = Template("""
QWidget { font-size: ${f_body}px; }
QMainWindow, QDialog, QWidget#annotationPanel, QWidget#searchPanel, QWidget#logPanel { background: $window; }
QFrame[panel="true"] { background: $surface; border: 1px solid $border; border-radius: ${radius_l}px; }
QWidget#inspectorContents { background: $surface; }
QMenu, QComboBox QAbstractItemView, QFrame[popupSurface="true"], QToolTip {
    background: $surface; color: $text; border: 1px solid $border_strong; border-radius: ${radius_l}px;
}
QToolTip { padding: ${space_xs}px ${space_sm}px; font-size: ${f_caption}px; }

QLabel { background: transparent; border: none; color: $text; }
QLabel[role="heading"] { color: $heading; font-size: ${f_title}px; font-weight: 600; }
QLabel[role="muted"] { color: $muted; font-size: ${f_caption}px; }
QLabel[role="status"] { color: $secondary; font-size: ${f_control}px; }
QLabel[role="badge"] { background: $tint; color: $primary_hover; border-radius: ${radius_s}px; padding: ${space_xs}px ${space_sm}px; font-size: ${f_caption}px; }
QLabel[role="sectionIcon"] { background: $tint; border-radius: ${radius}px; }
QLabel[role="emptyIcon"] { background: $subtle; border: 1px solid $border; border-radius: ${radius_l}px; }
QLabel[role="emptyTitle"] { color: $secondary; font-size: ${f_title}px; font-weight: 500; }
QLabel[role="resultTitle"] { color: $heading; font-size: ${f_body}px; font-weight: 600; }
QLabel[role="metrics"] { color: $muted; font-size: ${f_caption}px; }
QLabel[role="confidence"] { background: $tint; color: $primary_hover; border-radius: ${radius_s}px; padding: ${space_xs}px ${space_sm}px; font-size: ${f_caption}px; }
QLabel[role="confidence"][level="medium"] { background: $subtle; color: $secondary; }
QLabel[role="confidence"][level="low"] { background: $warning_surface; color: $warning; }
QLabel[role="hoverMetadata"] { color: $secondary; font-size: ${f_control}px; font-weight: 500; }
QLabel[role="hoverDetail"] { color: $muted; font-size: ${f_caption}px; }
QLabel[role="editorStatus"] { color: $secondary; font-size: ${f_caption}px; }
QLabel[role="editorValue"] { color: $primary_hover; font-size: ${f_control}px; font-weight: 600; }
QLabel[state="success"] { color: $success; }
QLabel[state="warning"] { color: $warning; }
QLabel[state="error"] { color: $error; }

QTabWidget { background: $surface; }
QTabWidget::pane { border: none; border-top: 1px solid $divider; }
QTabBar { background: $surface; }
QTabBar::tab {
    background: $surface; color: $muted; height: ${h_tab_content}px; padding: ${space_sm}px ${space_lg}px;
    margin: ${space_sm}px ${space_xs}px; border: 1px solid transparent; border-radius: ${radius}px; font-weight: 500;
}
QTabBar::tab:selected { color: $primary_hover; background: $tint; font-weight: 600; }
QTabBar::tab:hover:!selected { background: $subtle; color: $secondary; }

QPushButton, QToolButton {
    background: $surface; color: $text; border: 1px solid $border_strong;
    border-radius: ${radius}px; padding: 0px ${space_md}px; min-height: ${h_control}px; font-size: ${f_control}px;
}
QPushButton:hover, QToolButton:hover { background: $hover; }
QPushButton:pressed, QToolButton:pressed { background: $pressed; }
QPushButton:checked { background: $tint; border-color: $focus; color: $primary_hover; }
QPushButton:focus, QToolButton:focus { border-color: $focus; }
QPushButton:disabled, QToolButton:disabled { color: $disabled; background: $disabled_surface; border-color: $divider; }
QPushButton[variant="primary"] { color: $surface; background: $primary; border: 1px solid $primary; font-weight: 600; }
QPushButton[variant="primary"]:hover { background: $primary_hover; border-color: $primary_hover; }
QPushButton[variant="primary"]:pressed { background: $primary_pressed; border-color: $primary_pressed; }
QPushButton[variant="primary"]:disabled { color: $surface; background: $primary_disabled; border-color: $primary_disabled; }
QPushButton#generateButton { min-height: ${h_primary}px; font-size: ${f_body}px; }
QPushButton[variant="soft"] { background: $tint; color: $primary_hover; border-color: $tint; font-weight: 500; }
QPushButton[variant="soft"]:hover { background: $tint_strong; border-color: $tint_strong; }
QPushButton[variant="soft"]:disabled { background: $disabled_surface; border-color: $disabled_surface; color: $disabled; }
QPushButton[variant="added"] { background: $success_surface; color: $success; border-color: $success_border; }
QPushButton[variant="added"]:hover, QPushButton[variant="added"]:pressed { background: $success_hover; }
QPushButton[variant="toolbar"], QToolButton[variant="toolbar"] { border-color: transparent; background: transparent; }
QPushButton[variant="toolbar"]:hover, QToolButton[variant="toolbar"]:hover { background: $hover; }
QPushButton[variant="toolbar"]:pressed, QToolButton[variant="toolbar"]:pressed { background: $pressed; }
QPushButton[variant="menu"] { padding-right: ${space_lg}px; }
QPushButton[variant="menu"]::menu-indicator {
    image: url("$assets/chevron-down.svg"); width: 12px; height: 12px;
    subcontrol-origin: padding; subcontrol-position: center right; right: ${space_xs}px;
}
QToolButton::menu-indicator { image: none; }
QPushButton[shape="icon"], QToolButton[shape="icon"] { padding: 0px; }
QPushButton[variant="measurement"] {
    padding: 0px ${space_sm}px; min-height: ${h_compact}px; font-size: ${f_caption}px;
    background: $subtle; color: $secondary;
}
QPushButton[variant="measurement"]:hover { background: $hover; }
QPushButton[variant="measurement"]:pressed { background: $pressed; }
QPushButton[variant="measurement"]:disabled { background: $disabled_surface; color: $disabled; border-color: $divider; }
QPushButton[status="marked"] { background: $success_surface; color: $success; border-color: $success_border; }
QPushButton[status="marked"]:hover, QPushButton[status="marked"]:pressed { background: $success_hover; }
QPushButton[status="missing"] { background: $warning_surface; color: $warning; border-color: $warning_border; }
QPushButton[status="missing"]:hover, QPushButton[status="missing"]:pressed { background: $warning_hover; }
QFrame#segmentedControl { background: $subtle; border: none; border-radius: ${radius}px; }
QPushButton[variant="segment"] {
    background: transparent; border: 1px solid transparent; padding: 0px ${space_sm}px;
    min-height: ${h_segment}px; color: $muted; border-radius: ${radius_s}px;
}
QPushButton[variant="segment"]:checked { background: $surface; border-color: $border; color: $primary_hover; font-weight: 600; }
QPushButton[variant="segment"]:hover:!checked { background: $hover; color: $secondary; }
QPushButton[variant="segment"]:disabled { color: $disabled; background: transparent; border-color: transparent; }
QToolButton#advancedToggle {
    border: none; border-top: 1px solid $divider; border-radius: 0; background: transparent;
    padding: 0px; min-height: 36px; color: $muted; font-size: ${f_caption}px;
}
QToolButton#advancedToggle:hover { color: $primary; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: $input; color: $text; border: 1px solid $border; border-radius: ${radius}px;
    padding: 0px ${space_sm}px; min-height: ${h_control}px; max-height: ${h_control}px;
    selection-background-color: $tint_strong; selection-color: $heading;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: $border_strong; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { background: $surface; border-color: $focus; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { background: $disabled_surface; color: $disabled; }
QLineEdit[state="error"] { background: $error_surface; border-color: $error_border; color: $error; }
QComboBox { padding-right: ${space_lg}px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: ${combo_arrow_width}px; border: none; background: transparent; }
QComboBox::down-arrow { image: url("$assets/chevron-down.svg"); width: 12px; height: 12px; }
QComboBox QAbstractItemView {
    outline: 0; padding: ${space_xs}px; selection-background-color: $tint; selection-color: $primary_hover;
}
QComboBox QAbstractItemView::item { min-height: ${h_compact}px; padding: ${space_xs}px ${space_sm}px; border-radius: ${radius}px; }
QComboBox QAbstractItemView::item:selected { background: $tint; color: $primary_hover; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { width: 20px; border: none; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("$assets/chevron-up.svg"); width: 12px; height: 12px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("$assets/chevron-down.svg"); width: 12px; height: 12px; }
QCheckBox { spacing: ${space_sm}px; color: $secondary; background: transparent; }
QCheckBox::indicator, QTableWidget::indicator { width: ${check}px; height: ${check}px; }

QTableWidget {
    border: none; background: $surface; alternate-background-color: $surface;
    selection-background-color: $tint; selection-color: $heading; gridline-color: $divider; outline: none;
}
QTableWidget::item { border-bottom: 1px solid $divider; padding: 0; }
QTableWidget::item:hover { background: $subtle; }
QTableWidget::item:selected { background: $tint; color: $heading; }
QHeaderView::section {
    background: $subtle; color: $muted; border: none; border-bottom: 1px solid $divider;
    padding: ${space_sm}px; font-size: ${f_caption}px; font-weight: 500;
}
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: ${space_sm}px; margin: ${space_xs}px 0; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: ${radius_s}px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: $disabled; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: ${space_sm}px; margin: 0 ${space_xs}px; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: ${radius_s}px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: $disabled; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QSplitter::handle { background: $window; }
QSplitter::handle:hover { background: $tint_strong; border-radius: ${radius_s}px; }
QPlainTextEdit { border: none; background: $surface; color: $text; selection-background-color: $tint_strong; selection-color: $heading; }
QListWidget#searchResults { border: none; border-radius: 0; padding: 0; background: $surface; outline: none; }
QListWidget#searchResults::item, QListWidget#searchResults::item:selected, QListWidget#searchResults::item:hover {
    padding: 0; border: none; background: transparent;
}
QFrame#searchResult { border: 1px solid $border; border-radius: ${radius}px; background: $surface; }
QFrame#searchResult:hover { border-color: $border_strong; background: $input; }
QFrame#searchResult[selected="true"] { border-color: $focus; background: $tint; }
QProgressBar#searchProgress { height: 3px; min-height: 3px; max-height: 3px; padding: 0; background: $divider; border: none; border-radius: 0; }
QProgressBar#searchProgress::chunk { background: $primary; border-radius: 0; }
QMenu { padding: ${space_xs}px; }
QMenu::item { padding: ${space_sm}px ${space_lg}px ${space_sm}px ${space_md}px; border-radius: ${radius}px; color: $text; }
QMenu::item:selected { background: $tint; color: $primary_hover; }
QMenu::item:disabled { color: $disabled; }
QMenu::separator { height: 1px; background: $divider; margin: ${space_xs}px ${space_sm}px; }

QFrame[editorBar="true"] { background: $surface; border: 1px solid $border; border-radius: ${radius}px; }
QFrame[role="editorSeparator"] { background: $border; border: none; min-width: 1px; max-width: 1px; }
QDialog[imageEditor="true"] QPushButton, QDialog[imageEditor="true"] QToolButton { padding: 0px ${space_sm}px; min-height: ${h_compact}px; }
QDialog[imageEditor="true"] QPushButton[variant="segment"] { min-height: ${h_segment}px; }
QDialog[imageEditor="true"] QToolButton[shape="icon"] { padding: 0px; }
QDialog[imageEditor="true"] QLineEdit { padding: 0px ${space_sm}px; min-height: ${h_compact}px; max-height: ${h_compact}px; }
""").substitute(
    asdict(COLORS),
    assets=ASSETS.as_posix(),
    radius_s=RADIUS_SMALL,
    radius=RADIUS,
    radius_l=RADIUS_LARGE,
    space_xs=SPACE.xs, space_sm=SPACE.sm, space_md=SPACE.md, space_lg=SPACE.lg,
    combo_arrow_width=ICON_SIZE + 2 * SPACE.xs,
    # A stylesheet height excludes the 1 px border on each side.
    h_control=CONTROL_HEIGHT - 2,
    h_compact=COMPACT_HEIGHT - 2,
    h_primary=PRIMARY_HEIGHT - 2,
    h_tab_content=NAV_BAR_HEIGHT - 4 * SPACE.sm - 2,  # margins, padding and border
    h_segment=CONTROL_HEIGHT - 2 * SPACE.xs - 2,
    check=CHECK_SIZE,
    f_caption=FONT_CAPTION,
    f_control=FONT_CONTROL,
    f_body=FONT_BODY,
    f_title=FONT_TITLE,
)


class DesktopStyle(QProxyStyle):
    """Keep popup lists and checkbox states consistent across desktop platforms."""

    def __init__(self) -> None:
        super().__init__("Fusion")

    def polish(self, target):
        if isinstance(target, QWidget) and target.windowType() in (Qt.WindowType.Popup, Qt.WindowType.ToolTip):
            flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
            target.setWindowFlags(target.windowFlags() | flags)
            target.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        return super().polish(target)

    def pixelMetric(self, metric, option=None, widget=None) -> int:
        if metric in (QStyle.PixelMetric.PM_LayoutHorizontalSpacing, QStyle.PixelMetric.PM_LayoutVerticalSpacing):
            return SPACE.sm
        if metric in (QStyle.PixelMetric.PM_LayoutLeftMargin, QStyle.PixelMetric.PM_LayoutRightMargin,
                      QStyle.PixelMetric.PM_LayoutTopMargin, QStyle.PixelMetric.PM_LayoutBottomMargin):
            return SPACE.md
        return super().pixelMetric(metric, option, widget)

    def styleHint(self, hint, option=None, widget=None, returnData=None) -> int:
        # Fusion's menu delegate bypasses the popup list's item styling.
        if hint == QStyle.StyleHint.SH_ComboBox_Popup:
            return 0
        if hint == QStyle.StyleHint.SH_ComboBox_PopupFrameStyle:
            return QFrame.Shape.NoFrame
        return super().styleHint(hint, option, widget, returnData)

    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        if element in (QStyle.PrimitiveElement.PE_IndicatorCheckBox, QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = option.rect.adjusted(1, 1, -1, -1)
            checked = bool(option.state & (QStyle.StateFlag.State_On | QStyle.StateFlag.State_NoChange))
            enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
            color = QColor(COLORS.primary if enabled else COLORS.primary_disabled)
            painter.setPen(QPen(color if checked else QColor(COLORS.border_strong), 1))
            painter.setBrush(color if checked else QColor(COLORS.surface))
            painter.drawRoundedRect(rect, RADIUS_SMALL, RADIUS_SMALL)
            if checked:
                painter.setPen(QPen(QColor(COLORS.surface), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                if option.state & QStyle.StateFlag.State_NoChange:
                    painter.drawLine(rect.left() + 3, rect.center().y(), rect.right() - 3, rect.center().y())
                else:
                    path = QPainterPath(QPointF(rect.left() + 3, rect.center().y()))
                    path.lineTo(rect.left() + rect.width() * 0.43, rect.bottom() - 3)
                    path.lineTo(rect.right() - 2, rect.top() + 3)
                    painter.drawPath(path)
            painter.restore()
            return
        super().drawPrimitive(element, option, painter, widget)


@lru_cache(maxsize=96)
def icon(name: str, color: str = COLORS.icon) -> QIcon:
    source = (ASSETS / f"{name}.svg").read_bytes()
    renderer = QSvgRenderer(source.replace(COLORS.icon.encode("ascii"), QColor(color).name().encode("ascii")))
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def apply_theme(app: QApplication) -> None:
    """Style the application once, before its windows are created.

    Replacing the style later re-polishes every live widget, so repeated calls are ignored.
    """
    if app.property("semThemeApplied"):
        return
    app.setStyle(DesktopStyle())
    font = QFont()
    font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI"])
    font.setPixelSize(FONT_BODY)
    app.setFont(font)
    palette = app.style().standardPalette()
    for role, color in {
        QPalette.ColorRole.Window: COLORS.window,
        QPalette.ColorRole.WindowText: COLORS.text,
        QPalette.ColorRole.Base: COLORS.surface,
        QPalette.ColorRole.AlternateBase: COLORS.subtle,
        QPalette.ColorRole.Text: COLORS.text,
        QPalette.ColorRole.PlaceholderText: COLORS.disabled,
        QPalette.ColorRole.Button: COLORS.surface,
        QPalette.ColorRole.ButtonText: COLORS.text,
        QPalette.ColorRole.ToolTipBase: COLORS.surface,
        QPalette.ColorRole.ToolTipText: COLORS.text,
        QPalette.ColorRole.Highlight: COLORS.primary,
        QPalette.ColorRole.HighlightedText: COLORS.surface,
    }.items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(COLORS.disabled))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
    app.setProperty("semThemeApplied", True)
