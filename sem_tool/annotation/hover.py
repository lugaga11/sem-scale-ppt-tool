"""An on-demand enlarged image card for the queue's thumbnail hover target."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ..image_widgets import ImageCanvas, load_display_pixmap
from ..widgets import ElidedLabel


class ImageHoverCard(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("imageHoverCard")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.path: Path | None = None
        self.original_size = QSize()
        self.error = ""
        self._signature = None
        self._image_format = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)
        self.title = ElidedLabel()
        self.title.setProperty("role", "heading")
        self.image = ImageCanvas("无法读取图片")
        self.metadata = QLabel()
        self.metadata.setProperty("role", "hoverMetadata")
        self.details = QLabel()
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setWordWrap(True)
        self.details.setProperty("role", "hoverDetail")
        self.location = ElidedLabel()
        self.location.setProperty("role", "muted")
        layout.addWidget(self.title)
        layout.addWidget(self.image)
        layout.addWidget(self.metadata)
        layout.addWidget(self.details)
        layout.addWidget(self.location)

    def show_image(self, path: Path, detail: str, anchor: QRect) -> None:
        screen = QGuiApplication.screenAt(anchor.center()) or self.screen()
        available = screen.availableGeometry().adjusted(10, 10, -10, -10)
        self.setFixedWidth(min(470, available.width()))
        self.image.setFixedHeight(min(320, max(100, available.height() - 220)))
        self.title.setText(path.name)
        self.location.setText(str(path))
        try:
            stat = path.stat()
            signature = (str(path), stat.st_mtime_ns, stat.st_size)
            if signature != self._signature:
                pixmap, size, image_format = load_display_pixmap(path, QSize(900, 640))
                self.image.setPixmap(pixmap)
                self.original_size = size
                self._image_format = image_format
                self._signature = signature
            self.error = ""
        except (OSError, ValueError) as exc:
            self.image.setPixmap(QPixmap())
            self.original_size = QSize()
            self._signature = None
            self.error = str(exc)
            self.metadata.setText("原文件无法读取")
            self.details.setText(self.error)
        else:
            size_text = f"{stat.st_size / 1024:.0f} KB" if stat.st_size < 1024 ** 2 else f"{stat.st_size / 1024 ** 2:.1f} MB"
            self.metadata.setText(
                f"{self.original_size.width()} × {self.original_size.height()} px · {self._image_format} · {size_text}"
            )
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            self.details.setText(f"{detail}\n修改时间：{modified}")
        self.path = path
        self.adjustSize()
        x = anchor.right() + 14
        if x + self.width() > available.right():
            x = anchor.left() - self.width() - 14
        x = max(available.left(), min(x, available.right() - self.width() + 1))
        y = max(available.top(), min(anchor.top() - 12, available.bottom() - self.height() + 1))
        self.move(x, y)
        self.show()
