from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel
)

class CameraStatusBar(QFrame):

    def __init__(self, parent=None):

        super().__init__(parent)

        self.setFrameShape(
            QFrame.Shape.StyledPanel
        )

        self.setFrameShadow(
            QFrame.Shadow.Sunken
        )

        layout = QHBoxLayout(self)

        layout.setContentsMargins(
            8, 4, 8, 4
        )

        layout.setSpacing(20)

        self.stateLabel = QLabel("State: --")

        self.fpsLabel = QLabel(
            "FPS: --"
        )

        self.resolutionLabel = QLabel(
            "Resolution: -- x --"
        )

        self.formatLabel = QLabel(
            "Format: --"
        )

        layout.addWidget(self.stateLabel)

        layout.addWidget(
            self.fpsLabel
        )

        layout.addWidget(
            self.resolutionLabel
        )

        layout.addWidget(
            self.formatLabel
        )

        layout.addStretch()

    def update_status(
        self,
        state,
        fps,
        width,
        height,
        image_format,
    ):

        self.stateLabel.setText(
            f"State: {state}"
        )


        self.fpsLabel.setText(
            f"FPS: {fps:.1f}"
        )

        self.resolutionLabel.setText(
            f"Resolution: "
            f"{width} x {height}"
        )

        self.formatLabel.setText(
            f"Format: {image_format}"
        )