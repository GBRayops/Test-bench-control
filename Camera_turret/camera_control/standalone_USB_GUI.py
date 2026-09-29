import sys
import time
import cv2
import numpy as np
import os

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QSlider,
    QGroupBox,
    QLineEdit,
    QFileDialog,
    QStyle,
    QSpinBox
)

from PyQt6.QtCore import (
    Qt,
    pyqtSignal,
    pyqtSlot,
    QSize,
    QSettings,
    QThread,    
)

# --------------------------------------------------------
# Camera
# Replace VideoCapture with your industrial camera SDK
# --------------------------------------------------------



# USB webcam
camera = cv2.VideoCapture(0)
camera.set(cv2.CAP_PROP_SETTINGS, 1)
camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0)   # Manual mode on many webcams
#camera.set(cv2.CAP_PROP_EXPOSURE, -8)
camera.set(cv2.CAP_PROP_FPS, 30)


class CameraRGB(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Camera VIS RGB")
        self.resize(1300, 750)

        # -------------------------
        # Live camera display
        # -------------------------
        self.image_label = QLabel()
        self.image_label.setMinimumSize(800, 600)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet(
            "background-color: black; border: 1px solid gray;"
        )

        # -------------------------
        # Left control panel
        # -------------------------
        control_box = QGroupBox("Camera Controls")
        control_layout = QVBoxLayout()

        self.create_slider(control_layout, "Exposure", 1, 100, 1, 'ms')
        self.create_slider(control_layout, "Gain", 0, 24, 0, 'dB')
        #self.create_slider(control_layout, "Frame Rate", 1, 120, 30, 'fps')
        #self.create_slider(control_layout, "Brightness", 0, 100, 50, None)
        #self.create_slider(control_layout, "Contrast", 0, 100, 50, None)
        self.create_slider(control_layout, "Gamma", 1, 300, 1, None)



        # Buttons
        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")

        self.start_button.clicked.connect(self.start_camera)
        self.stop_button.clicked.connect(self.stop_camera)


        control_layout.addWidget(self.start_button)
        control_layout.addWidget(self.stop_button)

        
        
        #control_layout.addStretch()
        control_box.setLayout(control_layout)


        save_box = QGroupBox("Save Images")
        path_layout = QVBoxLayout()
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.browse_btn = QPushButton()
        self.browse_btn.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self.browse_btn.setFixedSize(28, 28)
        self.browse_btn.clicked.connect(self.select_folder)


        self.save_btn = QPushButton("Snapshot")
        self.save_btn.clicked.connect(self.save_image)
        self.burst_btn = QPushButton("Save Burst")
        self.burst_btn.clicked.connect(self.save_burst)

        burst_row = QHBoxLayout()

        burst_label = QLabel("Burst frames:")

        self.burst_count = QSpinBox()
        self.burst_count.setRange(1, 1000)
        self.burst_count.setValue(1)

        burst_row.addWidget(burst_label)
        burst_row.addWidget(self.burst_count)


        path_row.addWidget(self.path_edit)
        path_row.addWidget(self.browse_btn)
        path_layout.addLayout(path_row)
        path_layout.addLayout(burst_row)
        path_layout.addWidget(self.save_btn)
        path_layout.addWidget(self.burst_btn)
        #path_layout.addStretch()
        save_box.setLayout(path_layout)
        
        # -------------------------
        # Main Layout
        # -------------------------
        left_layout = QVBoxLayout()
        left_layout.addWidget(control_box)
        left_layout.addWidget(save_box)
        left_layout.addStretch()

        main_layout = QHBoxLayout()
        main_layout.addLayout(left_layout)
        main_layout.addWidget(self.image_label, stretch=1)

        self.setLayout(main_layout)
        

    # -------------------------------------------------
    # Create one slider
    # -------------------------------------------------
    def create_slider(self, layout, name, minimum, maximum, value, unit):
        if unit is not None:
            label = QLabel(f"{name} ({unit}): {value} ")
        if unit is None:
            label = QLabel(f"{name}: {value} ")

        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(minimum)
        slider.setMaximum(maximum)
        slider.setValue(value)

        slider.valueChanged.connect(
            lambda v, l=label, n=name: self.slider_changed(l, n, v)
        )

        layout.addWidget(label)
        layout.addWidget(slider)

    def slider_changed(self, label, name, value):

        label.setText(f"{name}: {value}")

        if name == "Brightness":
            camera.set(cv2.CAP_PROP_BRIGHTNESS, value / 100)

        elif name == "Contrast":
            camera.set(cv2.CAP_PROP_CONTRAST, value / 100)

        elif name == "Gamma":
            camera.set(cv2.CAP_PROP_GAMMA, value)

        elif name == "Gain":
            camera.set(cv2.CAP_PROP_GAIN, value)

        elif name == "Exposure":
            # Many webcams require manual exposure mode first
            #camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0)
            camera.set(cv2.CAP_PROP_EXPOSURE, int(np.log2(value/1000)))

        elif name == "Frame Rate":
            camera.set(cv2.CAP_PROP_FPS, value)
    
    # -------------------------------------------------
    # Camera functions
    # -------------------------------------------------
    def start_camera(self):
        #
        # Set camera default parameters
        #
        camera.set(cv2.CAP_PROP_EXPOSURE, int(np.log2(5/1000)))  # Set exposure to 5 ms
        camera.set(cv2.CAP_PROP_FPS, 30)  # Set frame rate to 30 FPS
        camera.set(cv2.CAP_PROP_GAIN, 0)  # Set gain to 0 dB
        camera.set(cv2.CAP_PROP_GAMMA, 1)  # Set gamma to 1

        
        

    def stop_camera(self):
        self.timer.stop()
    
    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Save Folder"
        )

        if folder:
            self.save_path = folder
            self.path_edit.setText(folder)


    def save_image(self):
        if self.current_frame is None:
            return

        filename = os.path.join(
                self.save_path,
                f"image_000.png"
            )

        if filename:
            cv2.imwrite(filename, self.current_frame)
        

    def save_burst(self):
        if self.current_frame is None:
            return

        count = self.burst_count.value()

        for i in range(count):
            filename = os.path.join(
                self.save_path,
                f"image_{i:03d}.png"
            )

            cv2.imwrite(filename, self.current_frame)

            QApplication.processEvents()

    def update_frame(self):

        ret, frame = camera.read()
        
        if not ret:
            return
        self.current_frame = frame.copy()
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        h, w, ch = frame.shape

        image = QImage(
            frame.data,
            w,
            h,
            ch * w,
            QImage.Format_RGB888,
        )
        
        pixmap = QPixmap.fromImage(image)
        
        self.image_label.setPixmap(
            pixmap.scaled(
                self.image_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

class RGBWorker(QObject):
        ####################################################################
    # Signals
    ####################################################################

    frameReady = pyqtSignal(np.ndarray)

    fpsUpdated = pyqtSignal(float)

    temperatureUpdated = pyqtSignal(float)

    errorOccurred = pyqtSignal(str)

    finished = pyqtSignal()

    ####################################################################
    # Constructor
    ####################################################################

    def __init__(self, camera):

        super().__init__()

        self.camera = camera

        self.running = False

    ####################################################################
    # Acquisition loop
    ####################################################################

    @pyqtSlot()
    def run(self):

        self.running = True

        frame_counter = 0

        fps_timer = time.perf_counter()

        #
        # Initial polling period (seconds)
        #
        # If the camera reports an invalid FPS,
        # fall back to 25 FPS.
        #

        try:

            camera_fps = self.rgb_camera.get_framerate()
            

            if camera_fps <= 0:
                camera_fps = 25.0

        except Exception:

            camera_fps = 25.0
        
        
        polling_period = 1.0 / camera_fps

        #
        # Acquisition loop
        #

        while self.running:

            loop_start = time.perf_counter()

            try:

                frame = self.camera.get_frame()

                if frame is not None:

                    self.frameReady.emit(frame)

                    frame_counter += 1

            except Exception as e:

                self.errorOccurred.emit(str(e))

                break

            #
            # FPS update every second
            #

            now = time.perf_counter()

            elapsed = now - fps_timer

            if elapsed >= 1.0:

                fps = frame_counter / elapsed

                self.fpsUpdated.emit(fps)

                frame_counter = 0

                fps_timer = now

                #
                # Refresh polling period in case
                # the user changed the frame rate.
                #

                try:

                    camera_fps = self.camera.get_framerate()

                    if camera_fps > 0:

                        polling_period = 1.0 / camera_fps

                except Exception:

                    pass

            #
            # Sleep only for the remaining time
            # in the frame period.
            #

            elapsed = time.perf_counter() - loop_start

            remaining = polling_period - elapsed

            if remaining > 0:

                time.sleep(remaining)

        self.finished.emit()

    ####################################################################
    # Stop acquisition
    ####################################################################

    def stop(self):

        self.running = False
# --------------------------------------------------------
# Main
# --------------------------------------------------------
if __name__ == "__main__":

    app = QApplication(sys.argv)

    window = CameraRGB()
    window.show()

    app.exec()

    camera.release()