"""Shared, compact desktop styling for the application and its dialogs."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QFrame, QProxyStyle, QStyle

STYLESHEET = """
QWidget { font-size: 14px; }
QMainWindow, QDialog, QWidget#annotationPanel, QWidget#searchPanel { background: #f2f5fa; }
QFrame[panel="true"] { background: white; border: 1px solid #dfe6f0; border-radius: 12px; }
QWidget#inspectorContents { background: white; }
QLabel { background: transparent; border: none; color: #344054; }
QLabel[role="heading"] { color: #20314c; font-size: 15px; font-weight: 600; }
QLabel[role="muted"] { color: #7b899f; font-size: 12px; }
QLabel[role="status"] { color: #536c8e; font-size: 13px; }
QLabel[role="badge"] { background: #edf3ff; color: #3973d3; border-radius: 6px; padding: 3px 8px; font-size: 12px; }
QLabel[role="sectionIcon"] { background: #eef4ff; border-radius: 8px; }
QLabel[role="emptyIcon"] { background: #f0f5fc; border: 1px solid #e5edf8; border-radius: 18px; }
QLabel[role="emptyTitle"] { color: #536780; font-size: 16px; font-weight: 500; }
QFrame[editorBar="true"] { background: white; border: 1px solid #dce4ef; border-radius: 8px; }
QFrame[role="editorSeparator"] { background: #dfe6f0; border: none; min-width: 1px; max-width: 1px; }
QLabel[role="editorStatus"] { color: #62748e; font-size: 12px; }
QLabel[role="editorValue"] { color: #285c9b; font-size: 13px; font-weight: 600; }
QDialog[imageEditor="true"] QPushButton, QDialog[imageEditor="true"] QToolButton {
    padding: 4px 10px; min-height: 20px;
}
QDialog[imageEditor="true"] QLineEdit { padding: 4px 8px; min-height: 20px; }
QTabWidget { background: white; }
QTabWidget::pane { border: none; border-top: 1px solid #e7ebf1; }
QTabBar { background: white; }
QTabBar::tab {
    background: white; color: #75849b; height: 20px; padding: 7px 16px;
    margin: 7px 4px; border: 1px solid transparent; border-radius: 7px; font-weight: 500;
}
QTabBar::tab:selected { color: #216ae2; background: #edf4ff; border-color: #e7effb; font-weight: 600; }
QTabBar::tab:hover:!selected { background: #f6f9fe; color: #495b75; }
QPushButton, QToolButton {
    background: white; color: #3f526d; border: 1px solid #cbd6e4;
    border-radius: 7px; padding: 7px 12px; min-height: 18px; font-size: 13px;
}
QPushButton:hover, QToolButton:hover { background: #edf3fb; border-color: #9fb5d2; }
QPushButton:pressed, QToolButton:pressed { background: #dee9f7; border-color: #86a4ca; }
QPushButton:checked { background: #eef4ff; border-color: #cbdcf7; color: #246fdd; }
QPushButton:focus, QToolButton:focus { border-color: #91b7f3; }
QPushButton:disabled, QToolButton:disabled { color: #aeb7c5; background: #f7f8fa; border-color: #edf0f4; }
QPushButton[variant="primary"] {
    color: white; background: #2878f4; border: 1px solid #2878f4; font-weight: 600;
}
QPushButton[variant="primary"]:hover { background: #216be0; border-color: #216be0; }
QPushButton[variant="primary"]:pressed { background: #1a5dc9; }
QPushButton[variant="primary"]:disabled { color: #f1f6ff; background: #acc9f7; border-color: #acc9f7; }
QPushButton#generateButton {
    min-height: 28px; padding: 10px 16px; border-radius: 8px; font-size: 15px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3586fa, stop:1 #2671ed);
}
QPushButton#generateButton:hover { background: #246fe8; }
QPushButton#generateButton:disabled { background: #acc9f7; border-color: #acc9f7; }
QPushButton[variant="soft"] { background: #edf4ff; color: #276de0; border-color: #edf4ff; font-weight: 500; }
QPushButton[variant="soft"]:hover { background: #e3eeff; border-color: #dce9ff; }
QPushButton[variant="soft"]:disabled { background: #f4f6fa; border-color: #f4f6fa; color: #a4b0c3; }
QPushButton[variant="added"], QPushButton[variant="added"]:disabled { background: #def2e5; color: #226c42; border-color: #a8d2b7; }
QPushButton[variant="toolbar"], QToolButton[variant="toolbar"] { border-color: transparent; background: transparent; }
QPushButton[variant="toolbar"]:hover, QToolButton[variant="toolbar"]:hover { background: #f0f4fa; }
QPushButton[variant="measurement"] { padding: 5px 9px; font-size: 12px; min-height: 18px; background: #edf2f8; color: #455c78; border-color: #c9d5e4; }
QPushButton[status="marked"] { background: #def2e5; color: #226c42; border-color: #a8d2b7; }
QPushButton[status="missing"] { background: #fff0e7; color: #b15e35; border-color: #e6c1ae; }
QPushButton[variant="measurement"]:hover { background: #e1ebf8; border-color: #9eb6d7; }
QPushButton[variant="measurement"]:pressed { background: #d3e1f3; border-color: #7f9ec7; }
QPushButton[status="marked"]:hover { background: #ccebd8; color: #1c623b; border-color: #7fbf97; }
QPushButton[status="marked"]:pressed { background: #bbe0ca; color: #1c623b; border-color: #69ad84; }
QPushButton[status="missing"]:hover { background: #ffe3d1; border-color: #d7a180; }
QPushButton[status="missing"]:pressed { background: #f8d3bc; border-color: #c88c68; }
QPushButton[variant="measurement"]:disabled { background: #f3f5f8; color: #9ba7b7; border-color: #e0e5ec; }
QFrame#segmentedControl { background: #f1f4f8; border: none; border-radius: 7px; }
QPushButton[variant="segment"] { background: transparent; border: 1px solid transparent; padding: 4px 8px; min-height: 18px; color: #8994a5; font-size: 13px; border-radius: 5px; }
QPushButton[variant="segment"]:checked { background: white; border-color: #e3e9f1; color: #256fdc; font-weight: 600; }
QPushButton[variant="segment"]:hover:!checked { background: #e9eef6; color: #5c718e; }
QPushButton[variant="segment"]:disabled { color: #b5bdca; background: #f1f4f8; border-color: transparent; }
QToolButton#advancedToggle { border: none; border-top: 1px solid #edf0f5; border-radius: 0; padding: 12px 0; color: #7c8ba1; font-size: 12px; }
QToolButton#advancedToggle:hover { color: #2878f4; background: #f7faff; }
QToolButton::menu-indicator { image: none; }
QToolButton#queueMoreButton { padding: 4px; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #fafcff; color: #304462; border: 1px solid #dfe6f1;
    border-radius: 7px; padding: 6px 10px; min-height: 20px;
    selection-background-color: #dceaff; selection-color: #184a92;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: #d4deec; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { background: white; border-color: #91b7f3; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { background: #f6f7f9; color: #afb8c6; }
QComboBox {
    background: white; border: 1px solid #cfd8e6; border-radius: 8px;
    min-height: 24px; padding: 3px 32px 3px 8px;
}
QComboBox:focus { border-color: #7fb0f1; }
QComboBox::drop-down {
    subcontrol-origin: border; subcontrol-position: top right; width: 26px;
    border-left: 1px solid #cfd8e6; border-top-right-radius: 7px; border-bottom-right-radius: 7px;
    background: #e9eff7;
}
QComboBox::drop-down:hover { background: #dbeafe; }
QComboBox::drop-down:pressed { background: #bfdbfe; }
QComboBox::drop-down:disabled { background: #f0f3f7; }
QComboBox::down-arrow { image: url("@ASSETS@/select-arrow.svg"); width: 8px; height: 5px; }
QComboBox QAbstractItemView {
    border: 1px solid #cfd8e6; border-radius: 6px; background: white; color: #172235;
    outline: 0; padding: 4px; selection-background-color: #e8f1ff; selection-color: #172235;
}
QComboBox QAbstractItemView::item { min-height: 30px; padding: 4px 8px; border-radius: 4px; }
QComboBox QAbstractItemView::item:selected { background: #e8f1ff; color: #172235; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { width: 20px; border: none; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("@ASSETS@/chevron-up.svg"); width: 12px; height: 12px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("@ASSETS@/chevron-down.svg"); width: 12px; height: 12px; }
QCheckBox { spacing: 7px; color: #536179; background: transparent; }
QCheckBox::indicator { width: 17px; height: 17px; }
QTableWidget::indicator { width: 17px; height: 17px; }
QTableWidget { border: none; background: white; alternate-background-color: white; selection-background-color: #eef5ff; selection-color: #243f68; gridline-color: #f0f3f7; outline: none; }
QTableWidget::item { border-bottom: 1px solid #f0f3f7; padding: 0; }
QTableWidget::item:hover { background: #f8faff; }
QTableWidget::item:selected { background: #eef5ff; color: #243f68; }
QHeaderView::section { background: #f5f8fc; color: #71829c; border: none; border-bottom: 1px solid #e8eef6; padding: 8px 10px; font-size: 12px; font-weight: 500; }
QTableCornerButton::section { background: #f7f9fc; border: none; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #d2dbe8; border-radius: 2px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #aebed5; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal { background: #d2dbe8; border-radius: 2px; min-width: 24px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QSplitter::handle { background: #f2f5fa; }
QSplitter::handle:hover { background: #d9e5f8; border-radius: 3px; }
QListWidget, QPlainTextEdit { border: 1px solid #e3e8ef; border-radius: 10px; background: white; padding: 6px; }
QListWidget::item:selected { background: #eef5ff; border-radius: 7px; color: #234f8d; }
QListWidget#searchResults { border: none; border-radius: 0; padding: 0; background: white; outline: none; }
QListWidget#searchResults::item { padding: 0; border: none; }
QFrame#searchResult { border: 1px solid #e3eaf3; border-radius: 10px; background: #ffffff; }
QFrame#searchResult:hover { border-color: #bbd2f3; background: #fbfdff; }
QFrame#searchResult[selected="true"] { border-color: #93b9f1; background: #f2f7ff; }
QLabel[role="resultTitle"] { color: #263a57; font-size: 14px; font-weight: 600; }
QLabel[role="metrics"] { color: #6d7f99; font-size: 12px; }
QLabel[role="confidence"] { background: #ecf4ff; color: #3473cf; border-radius: 5px; padding: 3px 7px; font-size: 11px; }
QLabel[role="confidence"][level="low"] { background: #fff5e8; color: #a67632; }
QFrame#imageHoverCard { background: white; border: 1px solid #cddcf0; border-radius: 12px; }
QLabel[role="hoverMetadata"] { color: #3e597b; font-size: 13px; font-weight: 500; }
QLabel[role="hoverDetail"] { color: #708098; font-size: 12px; }
QProgressBar { border: none; border-radius: 3px; background: #eaf0f9; color: #8495ac; text-align: center; height: 14px; font-size: 11px; }
QProgressBar::chunk { background: #aac9fa; border-radius: 3px; }
QProgressBar#searchProgress { height: 3px; min-height: 3px; max-height: 3px; padding: 0; background: #edf2fa; border: none; border-radius: 1px; }
QProgressBar#searchProgress::chunk { background: #347ef0; border-radius: 1px; }
QMenu { background: white; border: 1px solid #e1e6ee; padding: 6px; }
QMenu::item { padding: 8px 28px 8px 12px; border-radius: 5px; }
QMenu::item:selected { background: #edf4ff; color: #276de0; }
QMenu::item:disabled { color: #aab5c5; }
QMenu::separator { height: 1px; background: #edf0f5; margin: 5px 8px; }
QStatusBar { background: #f2f5fa; color: #8391a7; font-size: 11px; }
QStatusBar::item { border: none; }
""".replace("@ASSETS@", (Path(__file__).resolve().parent / "assets").as_posix())


class DesktopStyle(QProxyStyle):
    """Keep popup lists and checkbox states consistent across desktop platforms."""

    def __init__(self) -> None:
        super().__init__("Fusion")

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
            color = QColor("#2477f2" if enabled else "#9fb8df")
            painter.setPen(QPen(color if checked else QColor("#b4c4dc"), 1))
            painter.setBrush(color if checked else QColor("#ffffff"))
            painter.drawRoundedRect(rect, 3, 3)
            if checked:
                painter.setPen(QPen(QColor("white"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
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
def icon(name: str, color: str = "#667b9a") -> QIcon:
    source = (Path(__file__).resolve().parent / "assets" / f"{name}.svg").read_bytes()
    renderer = QSvgRenderer(source.replace(b"#667b9a", QColor(color).name().encode("ascii")))
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def apply_theme(app: QApplication) -> None:
    if app.property("semThemeApplied"):
        return
    app.setStyle(DesktopStyle())
    font = QFont()
    font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI"])
    font.setPointSize(10)
    app.setFont(font)
    palette = app.style().standardPalette()
    for role, color in {
        QPalette.ColorRole.Window: "#f2f5fa",
        QPalette.ColorRole.WindowText: "#344054",
        QPalette.ColorRole.Base: "#ffffff",
        QPalette.ColorRole.AlternateBase: "#f6f8fc",
        QPalette.ColorRole.Text: "#253449",
        QPalette.ColorRole.Button: "#ffffff",
        QPalette.ColorRole.ButtonText: "#35455c",
        QPalette.ColorRole.Highlight: "#2477f2",
        QPalette.ColorRole.HighlightedText: "#ffffff",
    }.items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#9ca9bb"))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
    app.setProperty("semThemeApplied", True)
