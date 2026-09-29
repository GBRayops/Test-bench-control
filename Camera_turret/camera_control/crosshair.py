from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QPen 
from PySide6.QtCore import Qt, QPoint

class CrosshairOverlay(QWidget):

    def __init__(self, parent=None):

        super().__init__(parent)

        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )

        # Normalized position:
        # 0.0 = left/top
        # 1.0 = right/bottom

        self.x = 0.5
        self.y = 0.5

        self.setVisible(True)

    def set_position(self, x, y):

        self.x = max(
            0.0,
            min(1.0, float(x))
        )

        self.y = max(
            0.0,
            min(1.0, float(y))
        )

        self.update()

    def paintEvent(self, event):

        if not self.isVisible():
            return

        painter = QPainter(self)

        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing
        )

        pen = QPen(
            Qt.GlobalColor.red
        )

        pen.setWidth(2)

        painter.setPen(pen)

        x = int(self.x * self.width())
        y = int(self.y * self.height())

        # Crosshair size

        size = 15

        painter.drawLine(
            x - size,
            y,
            x + size,
            y
        )

        painter.drawLine(
            x,
            y - size,
            x,
            y + size
        )

        # Optional center circle

        painter.drawEllipse(
            QPoint(x, y),
            4,
            4
        )