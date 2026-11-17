"""Qt image display helpers; source pixels are never modified."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from .imaging import image_to_display_rgb
from .theme import COLORS, RADIUS, icon


def qimage_from_pil(image: Image.Image) -> QImage:
    rgb = image_to_display_rgb(image)
    width, height = rgb.size
    return QImage(rgb.tobytes(), width, height, width * 3, QImage.Format.Format_RGB888).copy()


def load_display_pixmap(path: Path, bounds: QSize) -> tuple[QPixmap, QSize, str]:
    with closing(Image.open(path)) as source:
        size = QSize(*source.size)
        image_format = source.format or path.suffix.lstrip(".").upper()
        display = qimage_from_pil(source)
    if display.width() > bounds.width() or display.height() > bounds.height():
        display = display.scaled(bounds, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return QPixmap.fromImage(display), size, image_format


class ImageCanvas(QWidget):
    """Contain a complete image, including its scale bar, in a rounded surface."""

    def __init__(self, placeholder: str = "") -> None:
        super().__init__()
        self.pixmap = QPixmap()
        self.placeholder = placeholder
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(60, 60)

    def setPixmap(self, pixmap: QPixmap) -> None:
        self.pixmap = pixmap
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(COLORS.border), 1, Qt.PenStyle.DashLine if self.pixmap.isNull() else Qt.PenStyle.SolidLine))
        painter.setBrush(QColor(COLORS.subtle))
        painter.drawRoundedRect(bounds, RADIUS, RADIUS)
        if self.pixmap.isNull():
            center = self.rect().center()
            icon("image", COLORS.disabled).paint(painter, center.x() - 16, center.y() - 30, 32, 32)
            painter.setPen(QColor(COLORS.muted))
            painter.drawText(self.rect().adjusted(8, 40, -8, 0), Qt.AlignmentFlag.AlignCenter, self.placeholder)
            return
        area = self.rect().adjusted(6, 6, -6, -6)
        size = self.pixmap.size().scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF(0, 0, size.width(), size.height())
        target.moveCenter(QRectF(area).center())
        clip = QPainterPath()
        clip.addRoundedRect(bounds, RADIUS, RADIUS)
        painter.setClipPath(clip)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(target.toRect(), self.pixmap)
