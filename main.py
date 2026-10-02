from datetime import datetime
import sys
import cv2
import numpy as np
from pathlib import Path


from PySide6.QtWidgets import QApplication, QGridLayout, QGroupBox, QLabel, QMainWindow, QWidget, QTabWidget,QVBoxLayout, QHBoxLayout, QScrollArea
from PySide6.QtCore import Qt, Slot,  QTimer
from PySide6.QtGui import QImage, QPixmap
import pyqtgraph as pg

from internal.CAMs.crosshair import CrosshairOverlay
from internal.CAMs.cameras_control_tab import CamerasControlTab
from internal.turret_control_tab import TurretControlTab

from internal.Spectro.avaspec import *
from internal.Diode_driver.driver_control_tab import DriverControlTab
from internal.arduino_trigger_tab import ArduinoTriggerTab
from internal.Spectro.spectro_tab import SpectroTab
from internal.Spectro.spectro_ctrl import Spectrometer

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
        
        

        self.arduino_trigger_tab = ArduinoTriggerTab(self.queue)
        self.camera_control_tab = CamerasControlTab()
        self.spectro_tab = SpectroTab()
        self.spectrometer = Spectrometer()
        self.spectro_tab.spectrum_ready.connect(self.update_spectrum)
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

        self.right_panel = QGridLayout() #Right panel will containt video feeds and spectrum (and logs?)
        self.live_feeds()

        main_layout.addLayout(self.left_panel, 0, 0)
        main_layout.addLayout(self.right_panel, 0, 1)
        main_layout.setColumnStretch(0, 1)  
        main_layout.setColumnStretch(1, 3)  

        central_widget.setLayout(main_layout)

        # Connect signals
        self.driver_control_tab.protection_activated.connect(self.on_protection_activated)

        self.camera_control_tab.ids_frame_updated.connect(self.update_ids_image)
        self.camera_control_tab.usb_frame_updated.connect(self.update_usb_image)


        self.driver_control_tab.status_update.connect(self.log_tab.update_log)
        self.turret_control_tab.newLogMessage.connect(self.log_tab.update_log)
        self.camera_control_tab.newLogMessage.connect(self.log_tab.update_log)
        self.spectro_tab.newLogMessage.connect(self.log_tab.update_log)

    def live_feeds(self):

        #self.feeds_layout = QVBoxLayout()
        #self.camera_layout = QHBoxLayout()
        ids_box = QGroupBox("IDS Camera")
        usb_box = QGroupBox("USB Camera")
        # --------------------------------------------------------------
        # Live image widgets
        # --------------------------------------------------------------
        self.ids_layout = QVBoxLayout()
        self.idsFeedWidget = QWidget()
        #self.idsFeedWidget.setFixedSize(500, 500)

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
        #self.usbFeedWidget.setFixedSize(500, 500)

        self.usbImageLabel = QLabel(self.usbFeedWidget)
        self.usbImageLabel.setGeometry(self.usbFeedWidget.rect())
        self.usbImageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.usbCrosshair = CrosshairOverlay(self.usbFeedWidget)
        self.usbCrosshair.setGeometry(self.usbFeedWidget.rect())
        self.usb_layout.addWidget(self.usbFeedWidget)
        self.usb_layout.addWidget(self.camera_control_tab.usbStatusBar)
        usb_box.setLayout(self.usb_layout)

        spectro_box = QGroupBox("Spectrometer Data")
        self.spectro_layout = QHBoxLayout()
        self.spectrum_graph = pg.PlotWidget(pen=pg.mkPen('b', width=2), clear=True)
        self.spectrum_plot = self.spectrum_graph.plot([], [], pen=pg.mkPen('b', width=2))
        #self.spectrum_graph.setFixedSize(1100, 400)
        self.spectrum_graph.setLabel('left', 'Intensity', units='counts')
        self.spectrum_graph.setLabel('bottom', 'Wavelength', units='nm')
        self.spectrum_graph.setBackground('w')
        self.spectro_layout.addWidget(self.spectrum_graph)
        spectro_box.setLayout(self.spectro_layout)

        # Add the live feeds widgets to the right panel
        self.right_panel.addWidget(ids_box, 0, 0, 1, 1, alignment=Qt.Alignment())
        self.right_panel.addWidget(usb_box, 0, 1, 1, 1, alignment=Qt.Alignment())
        self.right_panel.addWidget(spectro_box, 1, 0, 1, 2, alignment=Qt.Alignment())
        self.right_panel.setRowStretch(0, 2)
        self.right_panel.setRowStretch(1, 1)



    def on_protection_activated(self, protections):
        """Handle protection activation from driver tab - stop Arduino triggers"""
        if hasattr(self, 'arduino_trigger_tab') and self.arduino_trigger_tab.connected:
            # Check if triggers are running
            if hasattr(self.arduino_trigger_tab, 'trigger_status_text'):
                if self.arduino_trigger_tab.trigger_status_text.text() == "RUNNING":
                    self.log_status(f"⚠️ Stopping Arduino triggers due to protection: {', '.join(protections)}")
                    if self.arduino_trigger_tab.arduino:
                        self.arduino_trigger_tab.arduino.stop()
                        self.arduino_trigger_tab.trigger_status.setStyleSheet("color: red;")
                        self.arduino_trigger_tab.trigger_status_text.setText("STOPPED")
                        self.arduino_trigger_tab.trigger_status_text.setStyleSheet("font-weight: bold; color: red;")
                        self.arduino_trigger_tab.start_btn.setEnabled(True)
                        self.arduino_trigger_tab.stop_btn.setEnabled(False)

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
    @Slot(object, object)        
    def update_spectrum(self, wavelengths, spectrum):
        """Display spectrum from live display mode (no processing)"""

        self.wav = np.array(wavelengths, dtype=np.float32)
        
        self.spec = np.array(spectrum, dtype=np.float32)

        # Update plot
        #self.spectrum_graph.plot([], [])  # Clear previous plot
        self.spectrum_plot.setData(self.wav, self.spec)

        # Update title
        self.spectrum_graph.setTitle('Spectrum - Live Display')

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

        if hasattr(self.spectro_tab, "shutdown"):

            try:
                self.spectro_tab.shutdown()

            except Exception as e:
                print(
                    f"Spectrometer shutdown error: {e}"
                )

        event.accept()

class LogTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)

        self.log_label = QLabel("Log messages will appear here.")
        self.log_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.log_label.setWordWrap(True)

        scroll_area.setWidget(self.log_label)
        layout.addWidget(scroll_area)

                # === RAYOPS LOGO ===
        try:
            logo_label = QLabel()
            base_dir = Path("__main__").resolve().parent
            logo_path = base_dir / "internal" / "GUI_Images" / "logo.png"
            logo_pixmap = QPixmap(logo_path)
            if not logo_pixmap.isNull():
                # Scale logo to fit nicely (max width 300px)
                scaled_logo = logo_pixmap.scaledToWidth(280, Qt.SmoothTransformation)
                logo_label.setPixmap(scaled_logo)
                logo_label.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
                logo_label.setStyleSheet("padding: 5px;")
                layout.addWidget(logo_label)

            else:
                print(f"⚠️ RAYOPS logo not found at: {logo_path}")
        except :
            print(f"⚠️ Could not load logo")
            pass  # Ignore if logo not found or fails to load     

        self.setLayout(layout)

    @Slot(str)
    def update_log(self, message):
        now = datetime.now()
        formatted_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        current_text = self.log_label.text()
        new_text = f"{current_text}\n{formatted_timestamp} : {message}"
        self.log_label.setText(new_text)
        self.log_label.adjustSize()


def main():

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.showMaximized()
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()