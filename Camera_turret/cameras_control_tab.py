from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
                               QComboBox, QGroupBox, QGridLayout, QDoubleSpinBox, 
                               QSpinBox, QStatusBar, QSlider, QLineEdit, QStyle, QMessageBox, QFileDialog)
from PySide6.QtCore import Signal, Qt, QThread, QSettings, QObject, Slot
from PySide6.QtGui import  QAction, QPixmap, QImage

import os
import cv2
import numpy as np
import time
import os
import re

from Camera_turret.camera_control.camera_controller import CameraController
from Camera_turret.camera_control.IDS_camera import IDSCamera
from Camera_turret.camera_control.RGB_CAM import USBCamera, USBWorker
from Camera_turret.camera_control.camera_status import CameraStatusBar

class CamerasControlTab(QWidget):
    ids_frame_updated = Signal(np.ndarray, float)
    usb_frame_updated = Signal(np.ndarray, float)
    def __init__(self):
            super().__init__()
    
            self.ids_camera = IDSCamera()
            self.usb_camera = USBCamera()
    
            self.worker = None
            self.worker_thread = None
    
            self.usb_worker = None
            self.usb_worker_thread = None
    
            self.current_ids_frame = None
            self.current_ids_timestamp = None
    
            self.current_usb_frame = None
            self.current_usb_timestamp = None
    
            self.camera_controller = CameraController(
                self.ids_camera,
                self.usb_camera,
            )
    
            self.save_path = None
            self.settings = QSettings("RAYOPS", "CameraViewer")
            self.status = QStatusBar()
    
            self._create_actions()
            self._create_widgets()
            self._connect_signals()
    
            self.recording = False
    
            self.load_settings()

    def _create_actions(self):
        self.openAction = QAction("Open Camera", self)
        self.closeAction = QAction("Close Camera", self)
        self.captureAction = QAction("Capture", self)
        self.exitAction = QAction("Exit", self)

    def _create_widgets(self):    
        # --------------------------------------------------------------
        # Buttons
        # --------------------------------------------------------------
        self.startButton = QPushButton("Start")
        self.stopButton = QPushButton("Stop")
        self.captureButton = QPushButton("Capture")
        self.recordButton = QPushButton("Record")

        self.idsStatusBar = CameraStatusBar()
        self.usbStatusBar = CameraStatusBar()
        # --------------------------------------------------------------
        # IDS controls
        # --------------------------------------------------------------
        self.exposureSpin = QDoubleSpinBox()
        self.exposureSpin.setRange(0.01, 1000)
        self.exposureSpin.setDecimals(2)
        self.exposureSpin.setSuffix(" ms")
   
        self.gainSlider = QSlider(Qt.Orientation.Horizontal)
        self.gainSlider.setRange(0, 100)
  
        self.gainSpin = QSpinBox()
        self.gainSpin.setRange(0, 100)
    
        self.fpsSpin = QDoubleSpinBox()
        self.fpsSpin.setRange(1, 500)
        self.fpsSpin.setDecimals(2)
        self.fpsSpin.setSuffix(" FPS")
    
        self.pixelClockSpin = QSpinBox()
        self.pixelClockSpin.setRange(5, 35)
        self.pixelClockSpin.setSuffix(" MHz")
    
        # --------------------------------------------------------------
        # Save path
        # --------------------------------------------------------------
        save_box = QGroupBox("Save Path")
        path_layout = QVBoxLayout()
        path_row = QHBoxLayout()
    
        save_box.setMinimumWidth(200)
        save_box.setMaximumWidth(800)
   
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
   
        self.browse_btn = QPushButton()
        self.browse_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
            )
        self.browse_btn.setFixedSize(28, 28)
        self.browse_btn.clicked.connect(self.select_folder)
    
        path_row.addWidget(self.path_edit)
        path_row.addWidget(self.browse_btn)
    
        path_layout.addLayout(path_row)
        save_box.setLayout(path_layout)
    
            # --------------------------------------------------------------
            # Actions
            # --------------------------------------------------------------
        buttonBox = QGroupBox("Actions")
        buttonLayout = QHBoxLayout()
    
        buttonLayout.addWidget(self.startButton)
        buttonLayout.addWidget(self.stopButton)
        buttonLayout.addWidget(self.captureButton)
        buttonLayout.addWidget(self.recordButton)
    
        buttonBox.setLayout(buttonLayout)
   
        # --------------------------------------------------------------
        # IDS control panel
        # --------------------------------------------------------------
    
        ids_control_panel = QGroupBox("IDS Camera Controls")
        grid = QGridLayout()
   
        grid.addWidget(QLabel("Exposure"), 0, 0)
        grid.addWidget(self.exposureSpin, 0, 1)
    
        grid.addWidget(QLabel("Gain"), 1, 0)
        grid.addWidget(self.gainSlider, 1, 1)
        grid.addWidget(self.gainSpin, 1, 2)
    
        grid.addWidget(QLabel("Frame Rate"), 2, 0)
        grid.addWidget(self.fpsSpin, 2, 1)
    
        grid.addWidget(QLabel("Pixel Clock"), 3, 0)
        grid.addWidget(self.pixelClockSpin, 3, 1)
    
        ids_control_panel.setLayout(grid)

        ids_layout = QVBoxLayout()
        ids_layout.addWidget(ids_control_panel)
    
    
    # --------------------------------------------------------------
    # USB control panel
    # --------------------------------------------------------------
        usb_ctrl = QVBoxLayout()
    
        self.usb_control_panel = self.create_usb_controls()
        usb_ctrl.addWidget(self.usb_control_panel)
    
        actions_layout = QHBoxLayout()
        actions_layout.addWidget(buttonBox)
        actions_layout.addWidget(save_box)
    
    
        # --------------------------------------------------------------
        # Main page layout
        # --------------------------------------------------------------
        layout = QVBoxLayout(self)
        layout.addLayout(actions_layout)
        layout.addLayout(ids_layout)
        layout.addLayout(usb_ctrl)
        layout.addStretch()
    
    def create_usb_controls(self):
            usb_box = QGroupBox("RGB USB Camera Controls")
            usb_layout = QVBoxLayout()
    
            port_selection = QHBoxLayout()
            port_label = QLabel("Select USB Camera:")
    
            self.usbCameraCombo = QComboBox()
            self.usbCameraCombo.addItem("USB Camera 0", 0)
            self.usbCameraCombo.addItem("USB Camera 1", 1)
            self.usbCameraCombo.addItem("USB Camera 2", 2)
    
            port_selection.addWidget(port_label)
            port_selection.addWidget(self.usbCameraCombo)
            usb_layout.addLayout(port_selection)
    
            resolution_layout = QHBoxLayout()
            resolution_label = QLabel("Resolution:")
    
            self.usb_resolution_combo = QComboBox()
            self.usb_resolution_combo.addItems([
                "640 x 480",
                "1280 x 720",
                "1920 x 1080"])
    
            resolution_layout.addWidget(resolution_label)
            resolution_layout.addWidget(self.usb_resolution_combo)
            usb_layout.addLayout(resolution_layout)
    
            fps_layout = QHBoxLayout()
            fps_label = QLabel("FPS:")
    
            self.usb_fps_spin = QDoubleSpinBox()
            self.usb_fps_spin.setRange(1.0, 120.0)
            self.usb_fps_spin.setDecimals(1)
            self.usb_fps_spin.setSingleStep(10.0)
            self.usb_fps_spin.setValue(30.0)
    
            fps_layout.addWidget(fps_label)
            fps_layout.addWidget(self.usb_fps_spin)
            usb_layout.addLayout(fps_layout)
    
            brightness_layout = QHBoxLayout()
            brightness_label = QLabel("Brightness (max. 64):")
    
            self.usb_brightness_spin = QDoubleSpinBox()
            self.usb_brightness_spin.setRange(0.0, 255.0)
            self.usb_brightness_spin.setSingleStep(1.0)
    
            brightness_layout.addWidget(brightness_label)
            brightness_layout.addWidget(self.usb_brightness_spin)
            usb_layout.addLayout(brightness_layout)
    
            contrast_layout = QHBoxLayout()
            contrast_label = QLabel("Contrast (max. 64):")
    
            self.usb_contrast_spin = QDoubleSpinBox()
            self.usb_contrast_spin.setRange(0.0, 255.0)
            self.usb_contrast_spin.setSingleStep(1.0)
    
            contrast_layout.addWidget(contrast_label)
            contrast_layout.addWidget(self.usb_contrast_spin)
            usb_layout.addLayout(contrast_layout)
    
            saturation_layout = QHBoxLayout()
            saturation_label = QLabel("Saturation (max. 128):")
    
            self.usb_saturation_spin = QDoubleSpinBox()
            self.usb_saturation_spin.setRange(0.0, 255.0)
            self.usb_saturation_spin.setSingleStep(1.0)
    
            saturation_layout.addWidget(saturation_label)
            saturation_layout.addWidget(self.usb_saturation_spin)
            usb_layout.addLayout(saturation_layout)
    
            exposure_layout = QHBoxLayout()
            exposure_label = QLabel("Exposure (log scale):")
    
            self.usb_exposure_spin = QDoubleSpinBox()
            self.usb_exposure_spin.setRange(-13.0, 0.0)
            self.usb_exposure_spin.setSingleStep(0.5)
    
            exposure_layout.addWidget(exposure_label)
            exposure_layout.addWidget(self.usb_exposure_spin)
            usb_layout.addLayout(exposure_layout)
    
            usb_box.setLayout(usb_layout)
            return usb_box

    def _connect_signals(self):
        self.startButton.clicked.connect(self.start_cameras)
        self.stopButton.clicked.connect(self.stop_cameras)
        self.captureButton.clicked.connect(self.capture_images)

        self.openAction.triggered.connect(self.start_cameras)
        self.closeAction.triggered.connect(self.stop_cameras)
        self.captureAction.triggered.connect(self.capture_images)

        self.gainSlider.valueChanged.connect(self.gainSpin.setValue)
        self.gainSpin.valueChanged.connect(self.gainSlider.setValue)
        self.gainSpin.valueChanged.connect(self.change_gain)

        self.exposureSpin.valueChanged.connect(self.change_exposure)
        self.fpsSpin.valueChanged.connect(self.change_fps)
        self.pixelClockSpin.valueChanged.connect(self.change_pixel_clock)

        self.recordButton.clicked.connect(self.toggle_recording)

        self.usb_fps_spin.valueChanged.connect(self.change_usb_fps)
        self.usb_brightness_spin.valueChanged.connect(self.change_usb_brightness)
        self.usb_contrast_spin.valueChanged.connect(self.change_usb_contrast)
        self.usb_saturation_spin.valueChanged.connect(self.change_usb_saturation)
        self.usb_exposure_spin.valueChanged.connect(self.change_usb_exposure)
        self.usb_resolution_combo.currentTextChanged.connect(
            self.change_usb_resolution
        )
        self.usbCameraCombo.currentIndexChanged.connect(self.change_usb_index)

    def start_cameras(self):
        try:
            self.camera_controller.set_ids_settings(
                exposure=self.exposureSpin.value(),
                gain=self.gainSpin.value(),
                fps=self.fpsSpin.value(),
                pixel_clock=self.pixelClockSpin.value(),
            )

            usb_index = self.usbCameraCombo.currentData()
            self.usb_camera.set_device_index(usb_index)

            self.camera_controller.open()
            self.camera_controller.start()

            self.usbCameraCombo.setEnabled(False)

            self._start_worker()
            self._start_usb_worker()

        except Exception as e:
            QMessageBox.critical(self, "Camera Error", str(e))

    def stop_cameras(self):
        try:
            self._stop_worker()
            self._stop_usb_worker()

            self.camera_controller.stop()

            self.usbCameraCombo.setEnabled(True)

        except Exception as e:
            QMessageBox.warning(self, "Camera", str(e))

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def get_next_filename(self, prefix, extension):
        if not self.save_path:
            raise ValueError("No save folder selected")

        os.makedirs(self.save_path, exist_ok=True)

        pattern = re.compile(
            rf"^{re.escape(prefix)}_(\d+){re.escape(extension)}$",
            re.IGNORECASE,
        )

        highest_number = -1

        for filename in os.listdir(self.save_path):
            match = pattern.match(filename)

            if match:
                number = int(match.group(1))
                highest_number = max(highest_number, number)

        next_number = highest_number + 1

        return os.path.join(
            self.save_path,
            f"{prefix}_{next_number:03d}{extension}",
        )

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Save Folder",
        )

        if folder:
            self.save_path = folder
            self.path_edit.setText(folder)

    def capture_images(self):
        ids_frame = self.current_ids_frame
        usb_frame = self.current_usb_frame

        if ids_frame is None:
            print("No IDS frame available for capture.")
            return

        if usb_frame is None:
            print("No USB frame available for capture.")
            return

        if self.save_path is None:
            QMessageBox.warning(
                self,
                "Capture",
                "No save folder selected.",
            )
            return

        ids_time = self.current_ids_timestamp
        usb_time = self.current_usb_timestamp

        print(
            "Capture time difference:",
            abs(ids_time - usb_time),
            "seconds",
        )

        ids_filename = os.path.join(
            self.save_path,
            f"img_mono_{str(ids_time)}.jpg",
        )
        usb_filename = os.path.join(
            self.save_path,
            f"img_rgb_{str(usb_time)}.jpg",
        )

        cv2.imwrite(ids_filename, self.current_ids_frame)
        cv2.imwrite(usb_filename, self.current_usb_frame)

    def start_recording(self):
        if self.current_ids_frame is None:
            QMessageBox.information(
                self,
                "Recording",
                "No IDS image available.",
            )
            return

        if self.current_usb_frame is None:
            QMessageBox.information(
                self,
                "Recording",
                "No USB image available.",
            )
            return

        if self.save_path is None:
            QMessageBox.warning(
                self,
                "Recording",
                "No save folder selected.",
            )
            return

        self.recording_start_time = time.perf_counter()

        ids_filename = os.path.join(
            self.save_path,
            f"video_mono_{str(self.recording_start_time)}.avi",
        )
        usb_filename = os.path.join(
            self.save_path,
            f"video_rgb_{str(self.recording_start_time)}.avi",
        )

        height, width = self.current_ids_frame.shape[:2]
        fps = max(1, int(self.ids_camera.get_framerate()))

        fourcc = cv2.VideoWriter_fourcc(*"MJPG")

        self.ids_videoWriter = cv2.VideoWriter(
            ids_filename,
            fourcc,
            fps,
            (width, height),
            len(self.current_ids_frame.shape) == 3,
        )

        usb_resolution = self.change_usb_resolution(
            self.usb_resolution_combo.currentText()
        )

        if usb_resolution is None:
            usb_resolution = (
                self.usb_camera.width,
                self.usb_camera.height,
            )

        self.usb_video_writer = cv2.VideoWriter(
            usb_filename,
            fourcc,
            max(1.0, float(self.usb_camera.get_framerate())),
            usb_resolution,
            len(self.current_usb_frame.shape) == 3,
        )

        if (
            not self.ids_videoWriter.isOpened()
            or not self.usb_video_writer.isOpened()
        ):
            QMessageBox.warning(
                self,
                "Recording",
                "Unable to create video.",
            )

            self.ids_videoWriter = None
            self.usb_video_writer = None
            return

        self.recording = True
        self.recordButton.setText("Stop Recording")

    def stop_recording(self):
        if self.ids_videoWriter is not None:
            self.ids_videoWriter.release()
            self.ids_videoWriter = None

        if self.usb_video_writer is not None:
            self.usb_video_writer.release()
            self.usb_video_writer = None

        self.recording = False

        self.idsStatusBar.stateLabel.setText("State: Running")
        self.usbStatusBar.stateLabel.setText("State: Running")
        self.recordButton.setText("Record")
    
    def toggle_recording(self):
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()


    # ------------------------------------------------------------------
    # Camera settings
    # ------------------------------------------------------------------

    def change_exposure(self, value=None):
        if not self.ids_camera.initialized:
            return

        try:
            self.ids_camera.set_exposure(self.exposureSpin.value())
        except Exception as e:
            QMessageBox.warning(self, "Exposure", str(e))

    def change_gain(self, value=None):
        if not self.ids_camera.initialized:
            return

        try:
            self.ids_camera.set_gain(self.gainSpin.value())
        except Exception as e:
            QMessageBox.warning(self, "Gain", str(e))

    def change_fps(self, value=None):
        if not self.ids_camera.initialized:
            return

        try:
            self.ids_camera.set_framerate(self.fpsSpin.value())
        except Exception as e:
            QMessageBox.warning(self, "Frame Rate", str(e))

    def change_pixel_clock(self, value=None):
        if not self.ids_camera.initialized:
            return

        try:
            self.ids_camera.set_pixel_clock(self.pixelClockSpin.value())
        except Exception as e:
            QMessageBox.warning(self, "Pixel Clock", str(e))

    # ------------------------------------------------------------------
    # USB settings
    # ------------------------------------------------------------------

    def change_usb_index(self, index):
        if self.usb_camera.running:
            QMessageBox.information(
                self,
                "USB Camera Running",
                "Cannot change USB camera while running",
            )
            return

        try:
            self.usb_camera.close()
            self.usb_camera.set_device_index(index)
            self.usb_camera.open()
        except Exception as e:
            QMessageBox.warning(self, "USB Camera", str(e))

    def change_usb_fps(self, value):
        if not self.usb_camera.initialized:
            return

        brightness, contrast, sat, exposure = self.save_usb_settings()
        was_running = self.usb_camera.running

        try:
            if was_running:
                self._stop_usb_worker()
                self.usb_camera.stop()

            self.usb_camera.set_framerate(value)
            actual = self.usb_camera.get_framerate()

            self.usb_fps_spin.blockSignals(True)
            self.usb_fps_spin.setValue(actual)
            self.usb_fps_spin.blockSignals(False)

            if was_running:
                self.usb_camera.start()
                self._start_usb_worker()
                self.usb_camera.set_saturation(sat)
                self.usb_camera.set_brightness(brightness)
                self.usb_camera.set_contrast(contrast)
                self.usb_camera.set_exposure(exposure)

        except Exception:
            pass

    def save_usb_settings(self):
        brightness = self.usb_camera.get_brightness()
        contrast = self.usb_camera.get_contrast()
        sat = self.usb_camera.get_saturation()
        exposure = self.usb_camera.get_exposure()

        return brightness, contrast, sat, exposure

    def change_usb_brightness(self, value):
        if not self.usb_camera.initialized:
            return

        try:
            self.usb_camera.set_brightness(value)
            actual = self.usb_camera.get_brightness()

            self.usb_brightness_spin.blockSignals(True)
            self.usb_brightness_spin.setValue(actual)
            self.usb_brightness_spin.blockSignals(False)
        except Exception:
            pass

    def change_usb_contrast(self, value):
        if not self.usb_camera.initialized:
            return

        try:
            self.usb_camera.set_contrast(value)
            actual = self.usb_camera.get_contrast()

            self.usb_contrast_spin.blockSignals(True)
            self.usb_contrast_spin.setValue(actual)
            self.usb_contrast_spin.blockSignals(False)
        except Exception:
            pass

    def change_usb_saturation(self, value):
        if not self.usb_camera.initialized:
            return

        try:
            self.usb_camera.set_saturation(value)
            actual = self.usb_camera.get_saturation()

            self.usb_saturation_spin.blockSignals(True)
            self.usb_saturation_spin.setValue(actual)
            self.usb_saturation_spin.blockSignals(False)
        except Exception:
            pass

    def change_usb_exposure(self, value):
        if not self.usb_camera.initialized:
            return

        try:
            self.usb_camera.set_exposure(value)
            actual = self.usb_camera.get_exposure()

            self.usb_exposure_spin.blockSignals(True)
            self.usb_exposure_spin.setValue(actual)
            self.usb_exposure_spin.blockSignals(False)
        except Exception:
            pass

    def change_usb_resolution(self, text):
        if not self.usb_camera.initialized:
            return None

        was_running = self.usb_camera.running

        try:
            width, height = map(int, text.split(" x "))

            if was_running:
                self._stop_usb_worker()
                self.usb_camera.stop()

            actual_width, actual_height = self.usb_camera.set_resolution(
                width,
                height,
            )

            if was_running:
                self.usb_camera.start()
                self._start_usb_worker()

            return actual_width, actual_height

        except Exception as e:
            print(f"USB resolution error: {e}")
            return None


    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def load_settings(self):
        # QWidget does not have saveGeometry/restoreGeometry semantics for
        # the application's main window. Camera settings are still restored.
        exposure = self.settings.value("exposure", 5.0, type=float)
        gain = self.settings.value("gain", 0, type=int)
        fps = self.settings.value("fps", 24.0, type=float)
        pixel = self.settings.value("pixelClock", 34, type=int)

        self.exposureSpin.setValue(exposure)
        self.gainSpin.setValue(gain)
        self.fpsSpin.setValue(fps)
        self.pixelClockSpin.setValue(pixel)

    def save_settings(self):
        self.settings.setValue("exposure", self.exposureSpin.value())
        self.settings.setValue("gain", self.gainSpin.value())
        self.settings.setValue("fps", self.fpsSpin.value())
        self.settings.setValue("pixelClock", self.pixelClockSpin.value())

    # ------------------------------------------------------------------
    # Page shutdown
    # ------------------------------------------------------------------

    def shutdown(self):
        """Cleanly stop workers/cameras before the host application exits."""
        self.stop_laser_test()

        if self.recording:
            self.stop_recording()

        try:
            self._stop_worker()
            self._stop_usb_worker()
        except Exception as e:
            print(f"Error stopping camera workers: {e}")

        try:
            if self.ids_camera.running:
                self.ids_camera.stop()
        except Exception as e:
            print(f"Error stopping IDS camera: {e}")

        try:
            if self.usb_camera.running:
                self.usb_camera.stop()
        except Exception as e:
            print(f"Error stopping USB camera: {e}")

        try:
            if self.ids_camera.initialized:
                self.ids_camera.close()
        except Exception as e:
            print(f"Error closing IDS camera: {e}")

        try:
            if self.usb_camera.initialized:
                self.usb_camera.close()
        except Exception as e:
            print(f"Error closing USB camera: {e}")

        self.save_settings()

    

    

    # ------------------------------------------------------------------
    # Camera settings
    # ------------------------------------------------------------------

    def change_exposure(self, value=None):
        if not self.ids_camera.initialized:
            return

        try:
            self.ids_camera.set_exposure(self.exposureSpin.value())
        except Exception as e:
            QMessageBox.warning(self, "Exposure", str(e))

    def change_gain(self, value=None):
        if not self.ids_camera.initialized:
            return

        try:
            self.ids_camera.set_gain(self.gainSpin.value())
        except Exception as e:
            QMessageBox.warning(self, "Gain", str(e))

    def change_fps(self, value=None):
        if not self.ids_camera.initialized:
            return

        try:
            self.ids_camera.set_framerate(self.fpsSpin.value())
        except Exception as e:
            QMessageBox.warning(self, "Frame Rate", str(e))

    def change_pixel_clock(self, value=None):
        if not self.ids_camera.initialized:
            return

        try:
            self.ids_camera.set_pixel_clock(self.pixelClockSpin.value())
        except Exception as e:
            QMessageBox.warning(self, "Pixel Clock", str(e))

    # ------------------------------------------------------------------
    # USB settings
    # ------------------------------------------------------------------

    def change_usb_index(self, index):
        if self.usb_camera.running:
            QMessageBox.information(
                self,
                "USB Camera Running",
                "Cannot change USB camera while running",
            )
            return

        try:
            self.usb_camera.close()
            self.usb_camera.set_device_index(index)
            self.usb_camera.open()
        except Exception as e:
            QMessageBox.warning(self, "USB Camera", str(e))

    def change_usb_fps(self, value):
        if not self.usb_camera.initialized:
            return

        brightness, contrast, sat, exposure = self.save_usb_settings()
        was_running = self.usb_camera.running

        try:
            if was_running:
                self._stop_usb_worker()
                self.usb_camera.stop()

            self.usb_camera.set_framerate(value)
            actual = self.usb_camera.get_framerate()

            self.usb_fps_spin.blockSignals(True)
            self.usb_fps_spin.setValue(actual)
            self.usb_fps_spin.blockSignals(False)

            if was_running:
                self.usb_camera.start()
                self._start_usb_worker()
                self.usb_camera.set_saturation(sat)
                self.usb_camera.set_brightness(brightness)
                self.usb_camera.set_contrast(contrast)
                self.usb_camera.set_exposure(exposure)

        except Exception:
            pass

    def save_usb_settings(self):
        brightness = self.usb_camera.get_brightness()
        contrast = self.usb_camera.get_contrast()
        sat = self.usb_camera.get_saturation()
        exposure = self.usb_camera.get_exposure()

        return brightness, contrast, sat, exposure

    def change_usb_brightness(self, value):
        if not self.usb_camera.initialized:
            return

        try:
            self.usb_camera.set_brightness(value)
            actual = self.usb_camera.get_brightness()

            self.usb_brightness_spin.blockSignals(True)
            self.usb_brightness_spin.setValue(actual)
            self.usb_brightness_spin.blockSignals(False)
        except Exception:
            pass

    def change_usb_contrast(self, value):
        if not self.usb_camera.initialized:
            return

        try:
            self.usb_camera.set_contrast(value)
            actual = self.usb_camera.get_contrast()

            self.usb_contrast_spin.blockSignals(True)
            self.usb_contrast_spin.setValue(actual)
            self.usb_contrast_spin.blockSignals(False)
        except Exception:
            pass

    def change_usb_saturation(self, value):
        if not self.usb_camera.initialized:
            return

        try:
            self.usb_camera.set_saturation(value)
            actual = self.usb_camera.get_saturation()

            self.usb_saturation_spin.blockSignals(True)
            self.usb_saturation_spin.setValue(actual)
            self.usb_saturation_spin.blockSignals(False)
        except Exception:
            pass

    def change_usb_exposure(self, value):
        if not self.usb_camera.initialized:
            return

        try:
            self.usb_camera.set_exposure(value)
            actual = self.usb_camera.get_exposure()

            self.usb_exposure_spin.blockSignals(True)
            self.usb_exposure_spin.setValue(actual)
            self.usb_exposure_spin.blockSignals(False)
        except Exception:
            pass

    def change_usb_resolution(self, text):
        if not self.usb_camera.initialized:
            return None

        was_running = self.usb_camera.running

        try:
            width, height = map(int, text.split(" x "))

            if was_running:
                self._stop_usb_worker()
                self.usb_camera.stop()

            actual_width, actual_height = self.usb_camera.set_resolution(
                width,
                height,
            )

            if was_running:
                self.usb_camera.start()
                self._start_usb_worker()

            return actual_width, actual_height

        except Exception as e:
            print(f"USB resolution error: {e}")
            return None
    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @Slot(np.ndarray, float)
    def update_image(self, frame, timestamp):   
        self.ids_frame_updated.emit(frame, timestamp)

    @Slot(np.ndarray, float)
    def update_usb_image(self, frame, timestamp):
        self.usb_frame_updated.emit(frame, timestamp)

    @Slot(float)
    def update_usb_fps(self, fps):
        self.usbStatusBar.fpsLabel.setText(f"FPS: {fps:.1f}")

    @Slot(float)
    def update_ids_fps(self, fps):
        self.idsStatusBar.fpsLabel.setText(f"FPS: {fps:.1f}")

    @Slot(str)
    def worker_error(self, message):
        QMessageBox.critical(self, "Camera Error", message)

    @Slot(str)
    def usb_worker_error(self, error):
        print(f"USB Camera Error: {error}")


    def _start_worker(self):
        if self.worker is not None:
            return

        self.worker = CameraWorker(self.ids_camera)
        self.worker_thread = QThread(self)

        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.finished.connect(self.worker_finished)

        self.worker.frameReady.connect(self.update_image)
        self.worker.fpsUpdated.connect(self.update_ids_fps)
        self.worker.errorOccurred.connect(self.worker_error)

        self.worker_thread.start()

    def _start_usb_worker(self):
        if self.usb_worker is not None:
            return

        self.usb_worker = USBWorker(self.usb_camera)
        self.usb_worker_thread = QThread(self)

        self.usb_worker.moveToThread(self.usb_worker_thread)

        self.usb_worker_thread.started.connect(self.usb_worker.run)

        self.usb_worker.finished.connect(self.usb_worker_thread.quit)
        self.usb_worker.finished.connect(self.usb_worker.deleteLater)
        self.usb_worker_thread.finished.connect(
            self.usb_worker_thread.deleteLater
        )

        self.usb_worker.frameReady.connect(self.update_usb_image)
        self.usb_worker.fpsUpdated.connect(self.update_usb_fps)
        self.usb_worker.errorOccurred.connect(self.usb_worker_error)

        self.usb_worker_thread.start()

    def _stop_worker(self):
        if self.worker is None:
            return

        self.worker.stop()

        if self.worker_thread is not None:
            self.worker_thread.quit()
            self.worker_thread.wait()

        self.worker = None
        self.worker_thread = None

    def _stop_usb_worker(self):
        if self.usb_worker is None:
            return

        self.usb_worker.stop()

        if self.usb_worker_thread is not None:
            self.usb_worker_thread.quit()
            self.usb_worker_thread.wait()

        self.usb_worker = None
        self.usb_worker_thread = None

    def worker_finished(self):
        self.worker = None
        self.worker_thread = None


class CameraWorker(QObject):

    frameReady = Signal(np.ndarray, float)
    fpsUpdated = Signal(float)
    errorOccurred = Signal(str)
    finished = Signal()

    def __init__(self, camera):
        super().__init__()
        self.ids_camera = camera
        self.running = False

    @Slot()
    def run(self):
        self.running = True

        frame_counter = 0
        fps_timer = time.perf_counter()

        try:
            camera_fps = self.ids_camera.get_framerate()

            if camera_fps <= 0:
                camera_fps = 30.0

        except Exception:
            camera_fps = 25.0

        polling_period = 1.0 / camera_fps

        while self.running:
            loop_start = time.perf_counter()

            try:
                frame = self.ids_camera.get_frame()
                timestamp = time.perf_counter()

                if frame is not None:
                    self.frameReady.emit(frame, timestamp)
                    frame_counter += 1

            except Exception as e:
                self.errorOccurred.emit(str(e))
                break

            now = time.perf_counter()
            elapsed = now - fps_timer

            if elapsed >= 1.0:
                fps = frame_counter / elapsed
                self.fpsUpdated.emit(fps)

                frame_counter = 0
                fps_timer = now

                try:
                    camera_fps = self.ids_camera.get_framerate()

                    if camera_fps > 0:
                        polling_period = 1.0 / camera_fps

                except Exception:
                    pass

            elapsed = time.perf_counter() - loop_start
            remaining = polling_period - elapsed

            if remaining > 0:
                time.sleep(remaining)

        self.finished.emit()

    def stop(self):
        self.running = False

