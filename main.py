import sys

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QTabWidget,QVBoxLayout, QHBoxLayout


from Camera_turret.camera_control.IDS_camera import IDSCamera
from Camera_turret.camera_control.RGB_CAM import USBCamera, USBWorker
from Camera_turret.camera_control.camera_controller import CameraController
from Camera_turret.camera_control.camera_status import CameraStatusBar
from Camera_turret.camera_control.crosshair import CrosshairOverlay
from Camera_turret.cameras_control_tab import CamerasControlTab



from Spectro_diode.src.avaspec import *


from Spectro_diode.src.driver_control_tab import DriverControlTab


from Spectro_diode.src.arduino_trigger_tab import ArduinoTriggerTab

from Spectro_diode.src.spectro_tab import SpectroTab
from queue import Queue


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RAYOPS - Interaction Chamber Control Software")

        # Create a queue for inter-thread communication
        self.queue = Queue()


        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout()
        
        self.left_panel = QVBoxLayout() #Left panel will containt tabs
        self.tabs = QTabWidget()

        # Create the tabs

        self.driver_control_tab = DriverControlTab(self.queue)
        #self.driver_control_tab.status_update.connect(self.log_status)
        #self.driver_control_tab.protection_activated.connect(self.on_protection_activated)

        self.arduino_trigger_tab = ArduinoTriggerTab(self.queue)

        self.camera_control_tab = CamerasControlTab()

        self.spectro_tab = SpectroTab()

        self.tabs.addTab(self.spectro_tab, "Spectrometer")
        self.tabs.addTab(self.driver_control_tab, "Driver Control")
        self.tabs.addTab(self.arduino_trigger_tab, "Trigger Sync")
        self.tabs.addTab(self.camera_control_tab, "CAMs Controls")

        # Add the tabs to the left panel
        self.left_panel.addWidget(self.tabs)

        self.right_panel = QVBoxLayout() #Right panel will containt video feeds and spectrum (and logs?)


        main_layout.addLayout(self.left_panel)
        main_layout.addLayout(self.right_panel)

        central_widget.setLayout(main_layout)


def main():

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.showMaximized()
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()