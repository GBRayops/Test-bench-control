"""
main_window.py

Main application window for RAYOPS.

Contains:
    - CameraPage
    - TurretPage
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow,
    QTabWidget,
)


from camera_page import CameraPage
from turret_page import TurretPage


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("RAYOPS")

        # ------------------------------------------------------------
        # Main window size
        # ------------------------------------------------------------

        self.resize(1600, 1000)
        self.setMinimumSize(1200, 800)

        # ------------------------------------------------------------
        # Central widget
        # ------------------------------------------------------------

        self.tabs = QTabWidget()

        self.tabs.setTabPosition(
            QTabWidget.TabPosition.North
        )

        self.tabs.setDocumentMode(False)

        self.setCentralWidget(self.tabs)

        # ------------------------------------------------------------
        # Camera page
        # ------------------------------------------------------------

        self.camera_page = CameraPage()

        self.tabs.addTab(
            self.camera_page,
            "Camera"
        )

        # ------------------------------------------------------------
        # Turret page
        # ------------------------------------------------------------

        self.turret_page = TurretPage()

        self.tabs.addTab(
            self.turret_page,
            "Tourelle"
        )

        # ------------------------------------------------------------
        # Start on camera page
        # ------------------------------------------------------------

        self.tabs.setCurrentWidget(
            self.camera_page
        )

        self.turret_page.azimuthChanged.connect(
            self.camera_page.set_turret_azimuth)

        self.turret_page.elevationChanged.connect(
            self.camera_page.set_turret_elevation)

    # ================================================================
    # Window closing
    # ================================================================

    def closeEvent(self, event):

        # ------------------------------------------------------------
        # Shut down camera page
        # ------------------------------------------------------------

        if hasattr(self.camera_page, "shutdown"):

            try:
                self.camera_page.shutdown()

            except Exception as e:
                print(
                    f"Camera shutdown error: {e}"
                )

        # ------------------------------------------------------------
        # Shut down turret page
        # ------------------------------------------------------------

        if hasattr(self.turret_page, "shutdown"):

            try:
                self.turret_page.shutdown()

            except Exception as e:
                print(
                    f"Turret shutdown error: {e}"
                )

        event.accept()