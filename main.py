from datetime import datetime
import sys
import cv2
import numpy as np

from PySide6.QtWidgets import QApplication, QGridLayout, QGroupBox, QLabel, QMainWindow, QWidget, QTabWidget,QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Qt, Slot,  QTimer
from PySide6.QtGui import QImage, QPixmap


from Camera_turret.camera_control.crosshair import CrosshairOverlay
from Camera_turret.cameras_control_tab import CamerasControlTab
from Camera_turret.turret_control_tab import TurretControlTab

from Spectro_diode.src.avaspec import *
from Spectro_diode.src.driver_control_tab import DriverControlTab
from Spectro_diode.src.arduino_trigger_tab import ArduinoTriggerTab

from Spectro_diode.src.spectro_tab import SpectroTab
from queue import Queue


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RAYOPS - Interaction Chamber Control Software")
        screen_geometry = QApplication.primaryScreen().availableGeometry()
        self.setGeometry(screen_geometry)
        # Create a queue for inter-thread communication
        self.queue = Queue()


        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QGridLayout()
        
        self.left_panel = QVBoxLayout() #Left panel will containt tabs
        self.tabs = QTabWidget()

        # Create the tabs

        self.driver_control_tab = DriverControlTab(self.queue)
        #self.driver_control_tab.status_update.connect(self.log_status)
        #self.driver_control_tab.protection_activated.connect(self.on_protection_activated)

        self.arduino_trigger_tab = ArduinoTriggerTab(self.queue)

        self.camera_control_tab = CamerasControlTab()

        self.spectro_tab = SpectroTab()

        self.turret_control_tab = TurretControlTab()

        self.log_tab = LogTab()

        
        

        self.tabs.addTab(self.spectro_tab, "Spectrometer Control")
        self.tabs.addTab(self.driver_control_tab, "Driver Control")
        self.tabs.addTab(self.arduino_trigger_tab, "Trigger Sync")
        self.tabs.addTab(self.camera_control_tab, "CAMs Control")
        self.tabs.addTab(self.turret_control_tab, "Turret Control")
        self.tabs.addTab(self.log_tab, "Logs")

        # Add the tabs to the left panel
        self.left_panel.addWidget(self.tabs)

        self.right_panel = QVBoxLayout() #Right panel will containt video feeds and spectrum (and logs?)
        self.live_feeds()

        main_layout.addLayout(self.left_panel, 0, 0)
        main_layout.addLayout(self.right_panel, 0, 1)
        main_layout.setColumnStretch(0, 1)  
        main_layout.setColumnStretch(1, 3)  

        central_widget.setLayout(main_layout)


        # Connect signals
        self.camera_control_tab.ids_frame_updated.connect(self.update_ids_image)
        self.camera_control_tab.usb_frame_updated.connect(self.update_usb_image)
        
        self.turret_control_tab.newLogMessage.connect(self.log_tab.update_log)
        self.camera_control_tab.newLogMessage.connect(self.log_tab.update_log)

    def live_feeds(self):

        self.feeds_layout = QVBoxLayout()
        self.camera_layout = QHBoxLayout()
        ids_box = QGroupBox("IDS Camera")
        usb_box = QGroupBox("USB Camera")
        # --------------------------------------------------------------
        # Live image widgets
        # --------------------------------------------------------------
        self.ids_layout = QVBoxLayout()
        self.idsFeedWidget = QWidget()
        self.idsFeedWidget.setMinimumSize(500, 200)

        self.idsImageLabel = QLabel(self.idsFeedWidget)
        self.idsImageLabel.setGeometry(self.idsFeedWidget.rect())
        self.idsImageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.idsCrosshair = CrosshairOverlay(self.idsFeedWidget)
        self.idsCrosshair.setGeometry(self.idsFeedWidget.rect())
        self.ids_layout.addWidget(self.idsFeedWidget)
        self.ids_layout.addWidget(self.camera_control_tab.idsStatusBar)
        ids_box.setLayout(self.ids_layout)

        self.usb_layout = QVBoxLayout()
        self.usbFeedWidget = QWidget()
        self.usbFeedWidget.setMinimumSize(500, 200)

        self.usbImageLabel = QLabel(self.usbFeedWidget)
        self.usbImageLabel.setGeometry(self.usbFeedWidget.rect())
        self.usbImageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.usbCrosshair = CrosshairOverlay(self.usbFeedWidget)
        self.usbCrosshair.setGeometry(self.usbFeedWidget.rect())
        self.usb_layout.addWidget(self.usbFeedWidget)
        self.usb_layout.addWidget(self.camera_control_tab.usbStatusBar)
        usb_box.setLayout(self.usb_layout)

        spectro_box = QGroupBox("Live Spectrum Data")
        spectro_layout = QVBoxLayout()
        self.spectroFeedWidget = QWidget()
        self.spectroFeedWidget.setFixedSize(1080, 400)
        self.spectroImageLabel = QLabel(self.spectroFeedWidget)
        self.spectroImageLabel.setGeometry(self.spectroFeedWidget.rect())
        self.spectroImageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spectroCrosshair = CrosshairOverlay(self.spectroFeedWidget)
        self.spectroCrosshair.setGeometry(self.spectroFeedWidget.rect())
        spectro_box.setLayout(spectro_layout)

        # Add the live feeds widget to the right panel
        self.camera_layout.addWidget(ids_box)
        self.camera_layout.addWidget(usb_box)
        self.feeds_layout.addLayout(self.camera_layout)
        self.feeds_layout.addWidget(spectro_box)
        self.right_panel.addLayout(self.feeds_layout)


    @Slot(np.ndarray, float)
    def update_ids_image(self, frame, timestamp):
            self.current_ids_frame = frame
            self.current_ids_timestamp = timestamp
    
            h, w = frame.shape
    
            image = QImage(
                frame.data,
                w,
                h,
                frame.strides[0],
                QImage.Format.Format_Grayscale8,
            )
    
            pixmap = QPixmap.fromImage(image)
    
            self.idsImageLabel.setPixmap(
                pixmap.scaled(
                    self.idsImageLabel.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
    
            
            #if (self.recording and self.ids_videoWriter is not None and self.usb_video_writer is not None
            #    and self.current_usb_frame is not None):
            #    self.ids_videoWriter.write(frame)
            #    self.usb_video_writer.write(self.current_usb_frame)

    
    @Slot(np.ndarray, float)
    def update_usb_image(self, frame, timestamp):
            self.current_usb_frame = frame
            self.current_usb_timestamp = timestamp
    
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    
            h, w, channels = rgb_frame.shape
            bytes_per_line = channels * w
    
            image = QImage(
                rgb_frame.data,
                w,
                h,
                bytes_per_line,
                QImage.Format.Format_RGB888,
            )
    
            pixmap = QPixmap.fromImage(image)
    
            self.usbImageLabel.setPixmap(
                pixmap.scaled(
                    self.usbImageLabel.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
    def closeEvent(self, event):

        # ------------------------------------------------------------
        # Shut down camera page
        # ------------------------------------------------------------

        if hasattr(self.camera_control_tab, "shutdown"):

            try:
                self.camera_control_tab.shutdown()

            except Exception as e:
                print(
                    f"Camera shutdown error: {e}"
                )

        # ------------------------------------------------------------
        # Shut down turret page
        # ------------------------------------------------------------

        if hasattr(self.turret_control_tab, "shutdown"):

            try:
                self.turret_control_tab.shutdown()

            except Exception as e:
                print(
                    f"Turret shutdown error: {e}"
                )

        event.accept()

class LogTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)

        self.log_label = QLabel("Log messages will appear here.")
        self.log_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.log_label.setWordWrap(True)

        layout.addWidget(self.log_label)

    @Slot(str)
    def update_log(self, message):
        now = datetime.now()
        formatted_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        current_text = self.log_label.text()
        new_text = f"{current_text}\n{formatted_timestamp} : {message}"
        self.log_label.setText(new_text)


def main():

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    #window.showMaximized()
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()