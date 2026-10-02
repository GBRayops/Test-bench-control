import os
import time
import numpy as np
from pathlib import Path
import pandas as pd
from datetime import datetime


from PySide6.QtCore import Signal, Slot, Qt, QSettings
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QGridLayout,
    QDoubleSpinBox,
    QSpinBox,
    QLineEdit,
    QFileDialog,
    QCheckBox,
    QProgressBar,
    QMessageBox,
)

from internal.Spectro.spectro_ctrl import Spectrometer


class SpectroTab(QWidget):
    """
    Spectrometer control tab.

    Responsibilities:
        - Configure spectrometer acquisition parameters
        - Connect/disconnect the spectrometer
        - Start/stop live acquisition
        - Forward spectrum data to the MainWindow
        - Provide basic acquisition settings/UI

    The Spectrometer class owns:
        - Avantes SDK
        - Device connection
        - Acquisition thread
        - Live spectrum acquisition
    """

    # Forwarded spectrum signal:
    # wavelengths, spectrum
    spectrum_ready = Signal(object, object)

    # Log messages for MainWindow
    newLogMessage = Signal(str)

    # Optional connection-state signals
    connected_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)

        # ---------------------------------------------------------
        # Spectrometer backend
        # ---------------------------------------------------------
        self.spectrometer = Spectrometer(self)
        self.measurement_running = False
        self.measurement_target = 0
        self.measurement_count = 0
        self.measurement_scans = []

        # Forward backend signals
        self.spectrometer.spectrum_ready.connect(
            self._on_spectrum_received
        )

        self.spectrometer.status_update.connect(
            self.newLogMessage.emit
        )

        self.spectrometer.error.connect(
            self._on_spectrometer_error
        )

        self.spectrometer.connected.connect(
            self._on_connected
        )

        self.spectrometer.disconnected.connect(
            self._on_disconnected
        )

        # ---------------------------------------------------------
        # Local state
        # ---------------------------------------------------------
        self.connected = False
        self.isMeasuring = False

        self.settings = QSettings(
            "RAYOPS",
            "AvaSpecGUI"
        )
       

        self._build_ui()
        self._load_settings()
        self.calib_path = Path("calibration_factor.csv").resolve()
        self.settings.setValue("calibration_file", str(self.calib_path))
    # ============================================================
    # UI
    # ============================================================

    def _build_ui(self):

        main_panel = QVBoxLayout(self)

        # ========================================================
        # CONNECTION
        # ========================================================

        conn_group = QGroupBox("Connection")
        conn_layout = QVBoxLayout()

        self.connect_btn = QPushButton(
            "Connect to Spectrometer"
        )
        self.connect_btn.clicked.connect(
            self.connect_spectrometer
        )

        conn_layout.addWidget(self.connect_btn)

        self.device_info = QLabel("Not connected")
        self.device_info.setWordWrap(True)

        conn_layout.addWidget(self.device_info)

        conn_group.setLayout(conn_layout)
        main_panel.addWidget(conn_group)

        # ========================================================
        # ACQUISITION PARAMETERS
        # ========================================================

        acq_group = QGroupBox("Acquisition Parameters")
        acq_layout = QGridLayout()

        row = 0

        # --------------------------------------------------------
        # Integration time
        # --------------------------------------------------------

        acq_layout.addWidget(
            QLabel("Integration Time (ms):"),
            row,
            0
        )

        self.integration_time = QDoubleSpinBox()
        self.integration_time.setRange(
            0.03,
            60000.0
        )
        self.integration_time.setValue(10.0)
        self.integration_time.setDecimals(2)

        acq_layout.addWidget(
            self.integration_time,
            row,
            1
        )

        row += 1

        # --------------------------------------------------------
        # Start pixel
        # --------------------------------------------------------

        acq_layout.addWidget(
            QLabel("Start Pixel:"),
            row,
            0
        )

        start_pixel_layout = QHBoxLayout()

        self.start_pixel = QSpinBox()
        self.start_pixel.setRange(0, 4095)
        self.start_pixel.setValue(0)
        self.start_pixel.setEnabled(False)

        self.start_pixel.valueChanged.connect(
            self.update_wavelength_labels
        )

        start_pixel_layout.addWidget(
            self.start_pixel
        )

        self.start_wavelength_label = QLabel(
            "(connect first)"
        )

        self.start_wavelength_label.setStyleSheet(
            "color: #999;"
            "font-size: 20px;"
            "font-style: italic;"
        )

        start_pixel_layout.addWidget(
            self.start_wavelength_label
        )

        start_pixel_layout.addStretch()

        acq_layout.addLayout(
            start_pixel_layout,
            row,
            1
        )

        row += 1

        # --------------------------------------------------------
        # Stop pixel
        # --------------------------------------------------------

        acq_layout.addWidget(
            QLabel("Stop Pixel:"),
            row,
            0
        )

        stop_pixel_layout = QHBoxLayout()

        self.stop_pixel = QSpinBox()
        self.stop_pixel.setRange(0, 4095)
        self.stop_pixel.setValue(4095)
        self.stop_pixel.setEnabled(False)

        self.stop_pixel.valueChanged.connect(
            self.update_wavelength_labels
        )

        stop_pixel_layout.addWidget(
            self.stop_pixel
        )

        self.stop_wavelength_label = QLabel(
            "(connect first)"
        )

        self.stop_wavelength_label.setStyleSheet(
            "color: #999;"
            "font-size: 20px;"
            "font-style: italic;"
        )

        stop_pixel_layout.addWidget(
            self.stop_wavelength_label
        )

        stop_pixel_layout.addStretch()

        acq_layout.addLayout(
            stop_pixel_layout,
            row,
            1
        )

        row += 1

        # --------------------------------------------------------
        # Number of averages
        # --------------------------------------------------------

        acq_layout.addWidget(
            QLabel("Number of Averages:"),
            row,
            0
        )

        self.num_averages = QSpinBox()
        self.num_averages.setRange(1, 10000)
        self.num_averages.setValue(1)

        acq_layout.addWidget(
            self.num_averages,
            row,
            1
        )

        row += 1

        # --------------------------------------------------------
        # Number of scans
        # --------------------------------------------------------

        acq_layout.addWidget(
            QLabel("Number of Scans:"),
            row,
            0
        )

        self.num_scans = QSpinBox()
        self.num_scans.setRange(0, 10000)
        self.num_scans.setSpecialValueText("Continuous")
        self.num_scans.setValue(1)

        acq_layout.addWidget(
            self.num_scans,
            row,
            1
        )

        acq_group.setLayout(acq_layout)
        main_panel.addWidget(acq_group)

        # ========================================================
        # DATA SAVING
        # ========================================================

        file_group = QGroupBox("Data Saving")
        file_layout = QVBoxLayout()

        self.save_enable = QCheckBox(
            "Auto-save scans to file"
        )
        self.save_enable.setChecked(True)

        self.save_enable.stateChanged.connect(
            self.update_save_controls
        )

        file_layout.addWidget(
            self.save_enable
        )

        path_layout = QHBoxLayout()

        self.save_path = QLineEdit("./data")
        path_layout.addWidget(
            self.save_path
        )

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(
            self.browse_folder
        )

        path_layout.addWidget(
            self.browse_btn
        )

        file_layout.addLayout(
            path_layout
        )

        file_group.setLayout(file_layout)
        main_panel.addWidget(file_group)

        # ========================================================
        # POST PROCESSING
        # ========================================================

        postproc_group = QGroupBox(
            "Post-Processing (Calibration & Analysis)"
        )

        postproc_layout = QVBoxLayout()

        self.postproc_enable = QCheckBox(
            "Enable automatic post-processing"
        )
        self.postproc_enable.setChecked(False)

        postproc_layout.addWidget(
            self.postproc_enable
        )

        calib_layout = QHBoxLayout()

        calib_layout.addWidget(
            QLabel("Calibration file:")
        )

        self.calib_path = QLineEdit()
        self.calib_path.setText(
            self.settings.value(
                "calibration_file")
        )
        calib_layout.addWidget(
            self.calib_path
        )

        self.browse_calib_btn = QPushButton(
            "Browse..."
        )

        self.browse_calib_btn.clicked.connect(
            self.browse_calibration
        )

        calib_layout.addWidget(
            self.browse_calib_btn
        )

        postproc_layout.addLayout(
            calib_layout
        )

        self.analyze_btn = QPushButton(
            "Analyze Last Sequence"
        )

        self.analyze_btn.setEnabled(False)

        self.analyze_btn.setStyleSheet(
            "background-color: #FF9800;"
            "color: white;"
            "font-weight: bold;"
            "padding: 8px;"
        )

        postproc_layout.addWidget(
            self.analyze_btn
        )

        self.load_seq_btn = QPushButton(
            "Load Saved Sequence..."
        )

        self.load_seq_btn.setStyleSheet(
            "background-color: #2196F3;"
            "color: white;"
            "font-weight: bold;"
            "padding: 8px;"
        )

        self.load_seq_btn.clicked.connect(
            self.load_sequence_from_file
        )

        postproc_layout.addWidget(
            self.load_seq_btn
        )

        postproc_group.setLayout(
            postproc_layout
        )

        main_panel.addWidget(
            postproc_group
        )

        # ========================================================
        # MEASUREMENT BUTTONS
        # ========================================================

        btn_layout = QHBoxLayout()

        self.start_btn = QPushButton(
            "Start Measurement"
        )

        self.start_btn.setEnabled(False)

        self.start_btn.setStyleSheet(
            "background-color: #4CAF50;"
            "color: white;"
            "font-weight: bold;"
            "padding: 10px;"
        )

        self.start_btn.clicked.connect(
            self.start_measurement
        )

        btn_layout.addWidget(
            self.start_btn
        )

        self.stop_btn = QPushButton("Stop")

        self.stop_btn.setEnabled(False)

        self.stop_btn.setStyleSheet(
            "background-color: #f44336;"
            "color: white;"
            "font-weight: bold;"
            "padding: 10px;"
        )

        self.stop_btn.clicked.connect(
            self.stop_measurement
        )

        btn_layout.addWidget(
            self.stop_btn
        )

        main_panel.addLayout(
            btn_layout
        )

        # ========================================================
        # LIVE DISPLAY
        # ========================================================

        live_layout = QHBoxLayout()

        self.live_btn = QPushButton(
            "Start Live Display"
        )

        self.live_btn.setEnabled(False)

        self.live_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #9C27B0;
                color: white;
                font-weight: bold;
                padding: 10px;
            }

            QPushButton:disabled {
                background-color: #a0a0a0;
                color: #606060;
            }
            """
        )

        self.live_btn.setToolTip(
            "Continuous live spectrum display"
        )

        self.live_btn.clicked.connect(
            self.toggle_live_display
        )

        live_layout.addWidget(
            self.live_btn
        )

        main_panel.addLayout(
            live_layout
        )

        # ========================================================
        # PROGRESS
        # ========================================================

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)

        main_panel.addWidget(
            self.progress_bar
        )

        # ========================================================
        # LOGO
        # ========================================================

        main_panel.addStretch()

        try:
            logo_label = QLabel()

            base_dir = Path("__main__").resolve().parent
            logo_path = (
                base_dir /
                "internal" /
                "GUI_Images" /
                "logo.png")

            logo_pixmap = QPixmap(logo_path)

            if not logo_pixmap.isNull():

                scaled_logo = logo_pixmap.scaledToWidth(
                    280,
                    Qt.SmoothTransformation
                )

                logo_label.setPixmap(
                    scaled_logo
                )

                logo_label.setAlignment(
                    Qt.AlignHCenter |
                    Qt.AlignBottom
                )

                main_panel.addWidget(
                    logo_label
                )

        except Exception:
            pass

    # ============================================================
    # CONNECTION
    # ============================================================

    @Slot()
    def connect_spectrometer(self):
        """
        Connect through the Spectrometer backend.

        No Avantes SDK calls should occur here.
        """

        if self.connected:
            return

        self.newLogMessage.emit(
            "Connecting to spectrometer..."
        )

        success = self.spectrometer.connect()

        # The backend emits connected() on success.
        if not success:
            self.newLogMessage.emit(
                "Failed to connect to spectrometer."
            )

    @Slot()
    def disconnect_spectrometer(self):
        """
        Disconnect through the Spectrometer backend.
        """

        if not self.connected:
            return

        self.stop_live_display()

        self.spectrometer.disconnect()

    # ============================================================
    # CONNECTION CALLBACKS
    # ============================================================

    @Slot()
    def _on_connected(self):

        self.connected = True

        self.connect_btn.setText(
            "Disconnect Spectrometer"
        )

        self.connect_btn.clicked.disconnect(
            self.connect_spectrometer
        )

        self.connect_btn.clicked.connect(
            self.disconnect_spectrometer
        )

        self.start_btn.setEnabled(True)
        self.live_btn.setEnabled(True)

        self.start_pixel.setEnabled(True)
        self.stop_pixel.setEnabled(True)

        # Get information from backend
        serial = getattr(
            self.spectrometer,
            "serial",
            "Unknown"
        )

        num_pixels = getattr(
            self.spectrometer,
            "num_pixels",
            0
        )

        wavelengths = getattr(
            self.spectrometer,
            "wavelengths",
            None
        )

        info = (
            "Connected!\n"
            f"Serial: {serial}\n"
            f"Pixels: {num_pixels}"
        )

        if wavelengths is not None and len(wavelengths):

            info += (
                f"\nRange: "
                f"{wavelengths[0]:.2f} - "
                f"{wavelengths[-1]:.2f} nm"
            )

        self.device_info.setText(info)
        self.device_info.setStyleSheet(
            "color: green;"
        )

        # Configure pixel range
        if num_pixels > 0:

            self.start_pixel.setMaximum(
                num_pixels - 1
            )

            self.stop_pixel.setMaximum(
                num_pixels - 1
            )

            self.stop_pixel.setValue(
                num_pixels - 1
            )

        self.update_wavelength_labels()

        self.newLogMessage.emit(
            "Spectrometer connected."
        )

        self.connected_changed.emit(True)

    @Slot()
    def _on_disconnected(self):

        self.connected = False

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.live_btn.setEnabled(False)

        self.start_pixel.setEnabled(False)
        self.stop_pixel.setEnabled(False)

        self.connect_btn.setText(
            "Connect to Spectrometer"
        )

        try:
            self.connect_btn.clicked.disconnect(
                self.disconnect_spectrometer
            )
        except (TypeError, RuntimeError):
            pass

        try:
            self.connect_btn.clicked.connect(
                self.connect_spectrometer
            )
        except (TypeError, RuntimeError):
            pass

        self.device_info.setText(
            "Not connected"
        )

        self.device_info.setStyleSheet(
            ""
        )

        self.connected_changed.emit(False)

    # ============================================================
    # SPECTRUM SIGNAL
    # ============================================================

    @Slot(object, object)
    def _on_spectrum_ready(
        self,
        wavelengths,
        spectrum
    ):
        """
        Receive spectrum from Spectrometer and
        forward it to MainWindow.
        """

        self.spectrum_ready.emit(
            wavelengths,
            spectrum
        )
        if self.isMeasuring:
            self.progress_bar.setValue(
                self.progress_bar.value() + 1
            )
            return wavelengths, spectrum

    # ============================================================
    # LIVE DISPLAY
    # ============================================================

    @Slot()
    def toggle_live_display(self):

        if self.spectrometer.is_live_running():
            self.stop_live_display()
        else:
            self.start_live_display()

    @Slot()
    def start_live_display(self):

        if not self.connected:
            self.newLogMessage.emit(
                "Spectrometer is not connected."
            )
            return

        start_pixel = self.start_pixel.value()
        stop_pixel = self.stop_pixel.value()

        if start_pixel >= stop_pixel:
            self.newLogMessage.emit(
                "ERROR: Start pixel must be less than stop pixel."
            )
            return

        integration_time = (
            self.integration_time.value()
        )

        self.spectrometer.start_live(
            integration_time=integration_time,
            start_pixel=start_pixel,
            stop_pixel=stop_pixel
        )

        self.live_btn.setText(
            "Stop Live Display"
        )

        self.live_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #E91E63;
                color: white;
                font-weight: bold;
                padding: 10px;
            }
            """
        )

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)

        self.newLogMessage.emit(
            f"Live display started "
            f"(integration: {integration_time:.2f} ms)"
        )

    @Slot()
    def stop_live_display(self):

        self.spectrometer.stop_live()

        self.live_btn.setText(
            "Start Live Display"
        )

        self.live_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #9C27B0;
                color: white;
                font-weight: bold;
                padding: 10px;
            }
            """
        )

        if self.connected:
            self.start_btn.setEnabled(True)

        self.newLogMessage.emit(
            "Live display stopped."
        )

    # ============================================================
    # WAVELENGTH LABELS
    # ============================================================

    @Slot()
    def update_wavelength_labels(self):

        wavelengths = getattr(
            self.spectrometer,
            "wavelengths",
            None
        )

        if (
            not self.connected
            or wavelengths is None
            or len(wavelengths) == 0
        ):
            self.start_wavelength_label.setText(
                "(connect first)"
            )

            self.stop_wavelength_label.setText(
                "(connect first)"
            )

            return

        start_idx = self.start_pixel.value()
        stop_idx = self.stop_pixel.value()

        if 0 <= start_idx < len(wavelengths):

            self.start_wavelength_label.setText(
                f"({wavelengths[start_idx]:.2f} nm)"
            )

        if 0 <= stop_idx < len(wavelengths):

            self.stop_wavelength_label.setText(
                f"({wavelengths[stop_idx]:.2f} nm)"
            )

        if start_idx >= stop_idx:

            style = (
                "color: red;"
                "font-size: 20px;"
                "font-weight: bold;"
            )

        else:

            style = (
                "color: #666;"
                "font-size: 20px;"
                "font-weight: bold;"
            )

        self.start_wavelength_label.setStyleSheet(style)
        self.stop_wavelength_label.setStyleSheet(style)

    # ============================================================
    # SAVING
    # ============================================================

    @Slot()
    def update_save_controls(self):

        enabled = self.save_enable.isChecked()

        self.save_path.setEnabled(enabled)
        self.browse_btn.setEnabled(enabled)

    @Slot()
    def browse_folder(self):

        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Save Folder"
        )

        if folder:
            self.save_path.setText(folder)

    @Slot()
    def browse_calibration(self):

        file, _ = QFileDialog.getOpenFileName(
            self,
            "Select Calibration File",
            "",
            "CSV Files (*.csv);;All Files (*.*)"
        )

        if file:

            self.calib_path.setText(file)

            self.newLogMessage.emit(
                f"Calibration file selected: {Path(file).name}"
            )

    # ============================================================
    # MEASUREMENTS
    # ============================================================

    def start_measurement(self):

        if not self.connected:
            self.newLogMessage.emit(
                "ERROR: Not connected to spectrometer."
            )
            return

        if self.start_pixel.value() >= self.stop_pixel.value():

            self.newLogMessage.emit(
                "ERROR: Start pixel must be less than stop pixel."
            )
            return

        # Keep measurement parameters grouped together.
        params = {
            "integration_time":
                self.integration_time.value(),

            "integration_delay":
                0,

            "num_averages":
                self.num_averages.value(),

            "num_scans":
                self.num_scans.value(),

            "start_pixel":
                self.start_pixel.value(),

            "stop_pixel":
                self.stop_pixel.value(),

            "save_enabled":
                self.save_enable.isChecked(),

            "save_folder":
                self.save_path.text(),
        }

        num_scans = params["num_scans"]
        integration_time = params["integration_time"]
        if num_scans <= 0:
            self.newLogMessage.emit("Number of scans must be greater than 0.")
            return

        if not self.spectrometer.is_connected:
            self.newLogMessage.emit("Spectrometer is not connected.")
            return

        # Stop live display before starting the measurement
        if self.spectrometer.is_live_running():
            self.spectrometer.stop_live()

        # Get wavelengths from the spectrometer
        wavelengths = self.spectrometer.wavelengths[self.start_pixel.value():self.stop_pixel.value() + 1]

        if wavelengths is None:
            self.newLogMessage.emit("Wavelength calibration is not available.")
            return

        # Storage for acquired spectra
        self.measurement_scans = []

        # Measurement state
        self.measurement_running = True
        self.measurement_target = num_scans
        self.measurement_count = 0

        self.newLogMessage.emit(
                f"Starting measurement: {num_scans} scans, "
                f"{integration_time:.2f} ms integration time."
            )

            # Start acquisition
        self.spectrometer.start_live(
            integration_time=integration_time
        )
                
        self.newLogMessage.emit(
                "Starting measurement..."
            )
        self.progress_bar.setMaximum(num_scans)
        self.progress_bar.setValue(0)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
    
    def _on_spectrum_received(self, wavelengths, spectrum):
        """Handle every spectrum received from the spectrometer."""

        # Always forward the spectrum to the live display
        self._on_spectrum_ready(wavelengths, spectrum)
        # Only collect data when a measurement is running
        if not self.measurement_running:
            return

        self.measurement_scans.append(
            np.asarray(spectrum).copy()
        )

        self.measurement_count += 1

        self.newLogMessage.emit(
            f"Acquired scan "
            f"{self.measurement_count}/{self.measurement_target}"
        )
        self.progress_bar.setValue(self.measurement_count)
        if self.measurement_count >= self.measurement_target:
            self.finish_measurement()


    def finish_measurement(self):
        """Stop acquisition and save the acquired scans to CSV."""

        if not self.measurement_running:
            return

        self.measurement_running = False

        self.stop_live_display()

        self.spectrometer.stop_live()

        if not self.measurement_scans:
            self.newLogMessage.emit("No spectra were acquired.")
            return

        wavelengths = np.asarray(self.spectrometer.wavelengths)[self.start_pixel.value():self.stop_pixel.value() + 1]

        spectra = np.column_stack(
                self.measurement_scans
            )

        header = ",".join(
                ["Wavelength"] +
                [
                    f"Scan_{i}"
                    for i in range(self.measurement_count)
                ]
            )
        
        data = np.column_stack(
                (wavelengths, spectra)
            )

        if self.save_enable.isChecked() and self.save_path.text():
            save_folder = Path(self.save_path.text())
            save_folder.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = save_folder / f"spectrum_measurement_{timestamp}.csv"

            np.savetxt(
                filename,
                data,
                delimiter=",",
                header=header,
                comments="",
                fmt="%.8g"
            )

            self.newLogMessage.emit(
                f"Measurement saved: {filename}"
            )
        else:
            filename, _ = QFileDialog.getSaveFileName(
                self,
                "Save Spectrum Measurement",
                "",
                "CSV Files (*.csv)"
            )

            if not filename:
                self.newLogMessage.emit(
                    "Measurement completed but was not saved."
                )
                return

            np.savetxt(
                filename,
                data,
                delimiter=",",
                header=header,
                comments="",
                fmt="%.8g"
            )

            self.newLogMessage.emit(
                f"Measurement saved: {filename}"
            )

    def stop_measurement(self):
        """Stop the ongoing measurement."""
        if self.measurement_running:
            self.measurement_running = False
            self.spectrometer.stop_live()
            self.newLogMessage.emit("Measurement stopped by user.")
            self.finish_measurement()
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
        else:
            self.newLogMessage.emit("No measurement is currently running.")

    # ============================================================
    # ERROR HANDLING
    # ============================================================

    @Slot(str)
    def _on_spectrometer_error(
        self,
        message
    ):

        self.newLogMessage.emit(
            f"ERROR: {message}"
        )

    # ============================================================
    # SETTINGS
    # ============================================================

    def _load_settings(self):

        if self.settings.contains("start_pixel"):
            self.start_pixel.setValue(
                self.settings.value(
                    "start_pixel",
                    0,
                    type=int
                )
            )

        if self.settings.contains("stop_pixel"):

            self.stop_pixel.setValue(
                self.settings.value(
                    "stop_pixel",
                    4095,
                    type=int
                )
            )

        if self.settings.contains("integration_time"):

            self.integration_time.setValue(
                self.settings.value(
                    "integration_time",
                    10.0,
                    type=float
                )
            )

        if self.settings.contains("save_path"):

            self.save_path.setText(
                self.settings.value(
                    "save_path",
                    "./data"
                )
            )

        if self.settings.contains("calibration_file"):

            self.calib_path.setText(
                self.settings.value(
                    "calibration_file",
                    ""
                )
            )

    def _save_settings(self):

        self.settings.setValue(
            "start_pixel",
            self.start_pixel.value()
        )

        self.settings.setValue(
            "stop_pixel",
            self.stop_pixel.value()
        )

        self.settings.setValue(
            "integration_time",
            self.integration_time.value()
        )

        self.settings.setValue(
            "save_path",
            self.save_path.text()
        )

        self.settings.setValue(
            "calibration_file",
            self.calib_path.text()
        )

    def load_sequence_from_file(self, file_path):
        pass

    # ============================================================
    # CLEANUP
    # ============================================================

    def shutdown(self):

        try:
            self._save_settings()
            self.stop_live_display()
            self.stop_measurement()
            self.spectrometer.stop_live()
            self.spectrometer.disconnect()
        except Exception as e:
            self.newLogMessage.emit(
                f"Error disconnecting spectrometer: {e}"
            )