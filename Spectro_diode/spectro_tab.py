from pathlib import Path
import time

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
                               QComboBox, QGroupBox, QGridLayout, QDoubleSpinBox, 
                               QSpinBox, QStatusBar, QSlider, QLineEdit, QStyle, QMessageBox, QFileDialog,
                               QCheckBox, QProgressBar)
from PySide6.QtCore import Signal, Qt, QThread, QSettings, QObject, Slot
from PySide6.QtGui import  QAction , QPixmap
from Spectro_diode.avaspec import *
import os
import sys
import numpy as np
import time



# Check for simulation mode
SIMULATION_MODE = '--simulate' in sys.argv
if SIMULATION_MODE:
    from Spectro_diode.hardware_simulator import (
        AVS_Init_Sim, AVS_Done_Sim, AVS_GetNrOfDevices_Sim,
        AVS_UpdateUSBDevices_Sim, AVS_GetList_Sim, AVS_Activate_Sim,
        AVS_Deactivate_Sim, AVS_UseHighResAdc_Sim, AVS_GetNumPixels_Sim,
        AVS_GetLambda_Sim, AVS_PrepareMeasure_Sim, AVS_Measure_Sim,
        AVS_PollScan_Sim, AVS_GetScopeData_Sim, AVS_StopMeasure_Sim,
        print_simulation_banner, get_avaspec_simulator
    )

# Wrapper functions that use simulator when in simulation mode
def avs_stop_measure(handle):
    if SIMULATION_MODE:
        return AVS_StopMeasure_Sim(handle)
    return AVS_StopMeasure(handle)

def avs_use_high_res_adc(handle, enable):
    if SIMULATION_MODE:
        return AVS_UseHighResAdc_Sim(handle, enable)
    return AVS_UseHighResAdc(handle, enable)

def avs_prepare_measure(handle, measconfig):
    if SIMULATION_MODE:
        return AVS_PrepareMeasure_Sim(handle, measconfig)
    return AVS_PrepareMeasure(handle, measconfig)

def avs_measure(handle, windowhandle, nummeas):
    if SIMULATION_MODE:
        return AVS_Measure_Sim(handle, windowhandle, nummeas)
    return AVS_Measure(handle, windowhandle, nummeas)

def avs_poll_scan(handle):
    if SIMULATION_MODE:
        return AVS_PollScan_Sim(handle)
    return AVS_PollScan(handle)

def avs_get_scope_data(handle):
    if SIMULATION_MODE:
        return AVS_GetScopeData_Sim(handle)
    return AVS_GetScopeData(handle)



class SpectroTab(QWidget):
    newLogMessage = Signal(str)  # Signal to send log messages to main window
    def __init__(self):
        super().__init__()
        main_panel = QVBoxLayout()
        
        # Connection group
        conn_group = QGroupBox("Connection")
        conn_layout = QVBoxLayout()
        
        self.connect_btn = QPushButton("Connect to Spectrometer")
        self.connect_btn.clicked.connect(self.connect_spectrometer)
        conn_layout.addWidget(self.connect_btn)
        
        self.device_info = QLabel("Not connected")
        self.device_info.setWordWrap(True)
        conn_layout.addWidget(self.device_info)
        
        conn_group.setLayout(conn_layout)
        main_panel.addWidget(conn_group)
        
        # Acquisition parameters group
        acq_group = QGroupBox("Acquisition Parameters")
        acq_layout = QGridLayout()
        
        row = 0
        
        # Integration time
        acq_layout.addWidget(QLabel("Integration Time (ms):"), row, 0)
        self.integration_time = QDoubleSpinBox()
        self.integration_time.setRange(0.03, 60000)
        self.integration_time.setValue(10.0)
        self.integration_time.setDecimals(2)
        acq_layout.addWidget(self.integration_time, row, 1)
        row += 1
        
        # Start Pixel
        acq_layout.addWidget(QLabel("Start Pixel:"), row, 0)
        start_pixel_layout = QHBoxLayout()
        self.start_pixel = QSpinBox()
        self.start_pixel.setRange(0, 4095)  # Max for AvaSpec-Mini4096CL
        self.start_pixel.setValue(0)
        self.start_pixel.setEnabled(False)  # Disabled until connection
        self.start_pixel.valueChanged.connect(self.update_wavelength_labels)
        start_pixel_layout.addWidget(self.start_pixel)
        self.start_wavelength_label = QLabel("(connect first)")
        self.start_wavelength_label.setStyleSheet("color: #999; font-size: 20px; font-style: italic;")
        start_pixel_layout.addWidget(self.start_wavelength_label)
        start_pixel_layout.addStretch()
        acq_layout.addLayout(start_pixel_layout, row, 1)
        row += 1
        
        # Stop Pixel
        acq_layout.addWidget(QLabel("Stop Pixel:"), row, 0)
        stop_pixel_layout = QHBoxLayout()
        self.stop_pixel = QSpinBox()
        self.stop_pixel.setRange(0, 4095)
        self.stop_pixel.setValue(4095)
        self.stop_pixel.setEnabled(False)  # Disabled until connection
        self.stop_pixel.valueChanged.connect(self.update_wavelength_labels)
        stop_pixel_layout.addWidget(self.stop_pixel)
        self.stop_wavelength_label = QLabel("(connect first)")
        self.stop_wavelength_label.setStyleSheet("color: #999; font-size: 20px; font-style: italic;")
        stop_pixel_layout.addWidget(self.stop_wavelength_label)
        stop_pixel_layout.addStretch()
        acq_layout.addLayout(stop_pixel_layout, row, 1)
        row += 1
        
        # Number of averages
        self.num_averages_label = QLabel("Number of Averages:")
        acq_layout.addWidget(self.num_averages_label, row, 0)
        self.num_averages = QSpinBox()
        self.num_averages.setRange(1, 10000)
        self.num_averages.setValue(1)
        acq_layout.addWidget(self.num_averages, row, 1)
        row += 1
        
        # Number of scans
        acq_layout.addWidget(QLabel("Number of Scans:"), row, 0)
        self.num_scans = QSpinBox()
        self.num_scans.setRange(0, 10000)
        self.num_scans.setSpecialValueText("Continuous")
        self.num_scans.setValue(1)
        acq_layout.addWidget(self.num_scans, row, 1)
        row += 1
        
        acq_group.setLayout(acq_layout)
        main_panel.addWidget(acq_group)
        
        
        # === LASER SYNCHRONIZATION NOTE ===
        laser_note = QGroupBox("Laser Synchronization")
        note_layout = QVBoxLayout()
        
        note_text = QLabel(
            "For synchronized laser control, use the 'Trigger Sync' tab.\n\n"
            "The Arduino will control both spectrometer and laser driver triggers "
            "with precise hardware timing."
        )
        note_text.setWordWrap(True)
        note_text.setStyleSheet("background-color: #e3f2fd; padding: 10px; border-radius: 5px;")
        note_layout.addWidget(note_text)
        
        laser_note.setLayout(note_layout)
        main_panel.addWidget(laser_note)
        
        # File saving group
        file_group = QGroupBox("Data Saving")
        file_layout = QVBoxLayout()
        
        self.save_enable = QCheckBox("Save scans to file")
        self.save_enable.setChecked(True)
        self.save_enable.stateChanged.connect(self.update_save_controls)
        file_layout.addWidget(self.save_enable)
        
        path_layout = QHBoxLayout()
        self.save_path = QLineEdit()
        self.save_path.setText("./data")
        path_layout.addWidget(self.save_path)
        
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self.browse_folder)
        path_layout.addWidget(self.browse_btn)
        
        file_layout.addLayout(path_layout)
        
        file_group.setLayout(file_layout)
        main_panel.addWidget(file_group)
        
        # === POST-PROCESSING GROUP ===
        postproc_group = QGroupBox("Post-Processing (Calibration & Analysis)")
        postproc_layout = QVBoxLayout()
        
        # Enable post-processing checkbox
        self.postproc_enable = QCheckBox("Enable automatic post-processing")
        self.postproc_enable.setChecked(False)
        self.postproc_enable.setToolTip("Automatically analyze spectra after acquisition")
        postproc_layout.addWidget(self.postproc_enable)
        
        # Calibration file path
        calib_layout = QHBoxLayout()
        calib_layout.addWidget(QLabel("Calibration file:"))
        self.calib_path = QLineEdit()
        self.calib_path.setPlaceholderText("Select calibration_factors.csv")
        calib_layout.addWidget(self.calib_path)
        
        self.browse_calib_btn = QPushButton("Browse...")
        self.browse_calib_btn.clicked.connect(self.browse_calibration)
        calib_layout.addWidget(self.browse_calib_btn)
        postproc_layout.addLayout(calib_layout)
        
        # Analyze button (manual trigger)
        self.analyze_btn = QPushButton("Analyze Last Sequence")
        self.analyze_btn.setEnabled(False)
        self.analyze_btn.clicked.connect(self.run_postprocessing)
        
        # Button color options - choose one:
        # Green (current):
        self.analyze_btn.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold; padding: 8px;")
        
        # Blue:
        # self.analyze_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px;")
        
        # Orange:
        # self.analyze_btn.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold; padding: 8px;")
        
        # Purple:
        # self.analyze_btn.setStyleSheet("background-color: #9C27B0; color: white; font-weight: bold; padding: 8px;")
        
        # Red:
        # self.analyze_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 8px;")
        
        # Dark Gray:
        # self.analyze_btn.setStyleSheet("background-color: #424242; color: white; font-weight: bold; padding: 8px;")
        
        self.analyze_btn.setToolTip("Apply calibration and fit Planck temperature to acquired spectra")
        postproc_layout.addWidget(self.analyze_btn)
        
        # Load saved sequence button
        self.load_seq_btn = QPushButton("Load Saved Sequence...")
        self.load_seq_btn.clicked.connect(self.load_sequence_from_file)
        self.load_seq_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px;")
        self.load_seq_btn.setToolTip("Load a previously saved CSV sequence for viewing and analysis")
        postproc_layout.addWidget(self.load_seq_btn)
        
        postproc_group.setLayout(postproc_layout)
        main_panel.addWidget(postproc_group)
        
        
        # Control buttons
        btn_layout = QHBoxLayout()
        
        self.start_btn = QPushButton("Start Measurement")
        self.start_btn.clicked.connect(self.start_measurement)
        self.start_btn.setEnabled(False)
        self.start_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 10px;")
        btn_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_measurement)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 10px;")
        btn_layout.addWidget(self.stop_btn)

        main_panel.addLayout(btn_layout)

        # Live Display button (for alignment - software mode only)
        live_layout = QHBoxLayout()

        self.live_btn = QPushButton("Start Live Display")
        self.live_btn.clicked.connect(self.toggle_live_display)
        self.live_btn.setEnabled(False)
        self.live_btn.setStyleSheet("""
            QPushButton { background-color: #9C27B0; color: white; font-weight: bold; padding: 10px; }
            QPushButton:disabled { background-color: #a0a0a0; color: #606060; }
        """)
        self.live_btn.setToolTip("Continuous live spectrum display for alignment (software mode only, no saving)")
        live_layout.addWidget(self.live_btn)

        main_panel.addLayout(live_layout)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        main_panel.addWidget(self.progress_bar)
        
        # Add stretch to push everything to top
        main_panel.addStretch()
        
        # === RAYOPS LOGO ===
        try:
            logo_label = QLabel()
            base_dir = Path("__main__").resolve().parent
            logo_path = base_dir / "GUI_Images" / "logo.png"
            logo_pixmap = QPixmap(logo_path)
            if not logo_pixmap.isNull():
                # Scale logo to fit nicely (max width 300px)
                scaled_logo = logo_pixmap.scaledToWidth(280, Qt.SmoothTransformation)
                logo_label.setPixmap(scaled_logo)
                logo_label.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
                logo_label.setStyleSheet("padding: 5px;")
                main_panel.addWidget(logo_label)

            else:
                print(f"⚠️ RAYOPS_logo.jpg not found at: {logo_path}")
        except :
            print(f"⚠️ Could not load logo")
            pass  # Ignore if logo not found or fails to load     

        self.setLayout(main_panel)
        

    def connect_spectrometer(self):
            try:
                if SIMULATION_MODE:
                    self.newLogMessage.emit("🔧 SIMULATION MODE - Initializing simulated spectrometer...")
                    ret = AVS_Init_Sim(0)
                else:
                    self.newLogMessage.emit("Initializing Avantes library...")
                    ret = AVS_Init(0)
    
                if ret < 0:
                    self.newLogMessage.emit(f"ERROR: Failed to initialize! Code: {ret}")
                    return
    
                self.newLogMessage.emit(f"Found {ret} device(s)")
    
                if SIMULATION_MODE:
                    num_devices = AVS_GetNrOfDevices_Sim()
                else:
                    num_devices = AVS_GetNrOfDevices()
    
                if num_devices == 0:
                    self.newLogMessage.emit("ERROR: No spectrometers found!")
                    return
    
                if SIMULATION_MODE:
                    device_list = AVS_GetList_Sim(num_devices)
                else:
                    device_list = AVS_GetList(num_devices)
    
                self.serial = device_list[0].SerialNumber.decode("utf-8")
                name = device_list[0].UserFriendlyName.decode("utf-8") if hasattr(device_list[0], 'UserFriendlyName') else "Simulated"
    
                self.newLogMessage.emit(f"Connecting to: {self.serial}")
    
                if SIMULATION_MODE:
                    self.handle = AVS_Activate_Sim(device_list[0])
                    # Simulated device - use simulated values
                    sim = get_avaspec_simulator()
                    self.num_pixels = sim.SIMULATED_NUM_PIXELS
                    self.wavelengths = sim.wavelengths
                    fw_ver = "SIM-1.0.0"
                else:
                    self.handle = AVS_Activate(device_list[0])
                    if self.handle == INVALID_AVS_HANDLE_VALUE:
                        self.newLogMessage.emit("ERROR: Failed to activate spectrometer!")
                        return
                    # Enable high-resolution 16-bit ADC mode (65535 max vs 14-bit 16383)
                    avs_use_high_res_adc(self.handle, True)
                    # Get device info
                    device_config = AVS_GetParameter(self.handle, 63484)
                    self.num_pixels = device_config.m_Detector_m_NrPixels
                    self.wavelengths = AVS_GetLambda(self.handle)
                    # Get version info
                    versions = AVS_GetVersionInfo(self.handle)
                    fw_ver = bytes(versions[1]).decode("utf-8").rstrip('\x00')
    
                # Store ADC max value (16-bit = 65535, used for Y-axis percentage scaling)
                self.adc_max = 65535
    
                # Configure pixel range controls based on actual detector
                self.start_pixel.setMaximum(self.num_pixels - 1)
                self.stop_pixel.setMaximum(self.num_pixels - 1)
    
                # Restore saved pixel values if available, otherwise use defaults
                if self.settings.contains('start_pixel'):
                    saved_start = self.settings.value('start_pixel', 0, type=int)
                    self.start_pixel.setValue(min(saved_start, self.num_pixels - 1))
                if self.settings.contains('stop_pixel'):
                    saved_stop = self.settings.value('stop_pixel', self.num_pixels - 1, type=int)
                    self.stop_pixel.setValue(min(saved_stop, self.num_pixels - 1))
                else:
                    self.stop_pixel.setValue(self.num_pixels - 1)
    
                # Enable pixel controls now that we're connected
                self.start_pixel.setEnabled(True)
                self.stop_pixel.setEnabled(True)
    
                # Set connected flag BEFORE updating labels so wavelengths display
                self.connected = True
    
                # Update wavelength labels with initial values
                self.update_wavelength_labels()
    
                wl_min = self.wavelengths[0]
                wl_max = self.wavelengths[self.num_pixels - 1]
    
                info_text = f"Connected!\n"
                if SIMULATION_MODE:
                    info_text += f"⚠️ SIMULATED\n"
                info_text += f"Serial: {self.serial}\n"
                info_text += f"Firmware: {fw_ver}\n"
                info_text += f"Pixels: {self.num_pixels}\n"
                info_text += f"Range: {wl_min:.2f} - {wl_max:.2f} nm"
    
                self.device_info.setText(info_text)
                self.device_info.setStyleSheet("color: green;" if not SIMULATION_MODE else "color: orange;")
    
                self.connect_btn.setEnabled(False)
                self.start_btn.setEnabled(True)
                # Enable live display only in software mode
                is_external = hasattr(self, 'arduino_tab') and self.arduino_tab.is_external_mode()
                self.live_btn.setEnabled(not is_external)
    
                self.newLogMessage.emit("Connection successful!")
                self.newLogMessage.emit(f"Wavelength range: {wl_min:.2f} - {wl_max:.2f} nm")
    
                if not SIMULATION_MODE:
                    self._spectro_check_timer.start(200)  # Poll USB presence every 200 ms
    
                if hasattr(self, 'arduino_tab'):
                    self.arduino_tab.update_timing_display()
    
            except Exception as e:
                self.newLogMessage.emit(f"ERROR: {str(e)}")
    def update_wavelength_labels(self):
        """Update wavelength labels when start/stop pixel values change"""
        if self.wavelengths is None or not self.connected:
            # Before connection - show placeholder
            self.start_wavelength_label.setText("(connect first)")
            self.start_wavelength_label.setStyleSheet("color: #999; font-size: 20px; font-style: italic;")
            self.stop_wavelength_label.setText("(connect first)")
            self.stop_wavelength_label.setStyleSheet("color: #999; font-size: 20px; font-style: italic;")
            return
            
        # After connection - show actual wavelengths
        start_idx = self.start_pixel.value()
        stop_idx = self.stop_pixel.value()
        
        # Ensure indices are within bounds
        if start_idx < len(self.wavelengths):
            start_wl = self.wavelengths[start_idx]
            self.start_wavelength_label.setText(f"({start_wl:.2f} nm)")
        
        if stop_idx < len(self.wavelengths):
            stop_wl = self.wavelengths[stop_idx]
            self.stop_wavelength_label.setText(f"({stop_wl:.2f} nm)")
        
        # Validate: start must be less than stop
        if start_idx >= stop_idx:
            self.start_wavelength_label.setStyleSheet("color: red; font-size: 20px; font-weight: bold;")
            self.stop_wavelength_label.setStyleSheet("color: red; font-size: 20px; font-weight: bold;")
        else:
            self.start_wavelength_label.setStyleSheet("color: #666; font-size: 20px; font-weight: bold;")
            self.stop_wavelength_label.setStyleSheet("color: #666; font-size: 20px; font-weight: bold;")
    def update_save_controls(self):
        enabled = self.save_enable.isChecked()
        self.save_path.setEnabled(enabled)
        self.browse_btn.setEnabled(enabled)
        
    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Save Folder")
        if folder:
            self.save_path.setText(folder)
    
    def browse_calibration(self):
        """Browse for calibration file"""
        file, _ = QFileDialog.getOpenFileName(
            self, 
            "Select Calibration File",
            "",
            "CSV Files (*.csv);;All Files (*.*)"
        )
        if file:
            self.calib_path.setText(file)
            self.newLogMessage.emit(f"Calibration file selected: {os.path.basename(file)}")
    
    def load_sequence_from_file(self):
        """Load a previously saved sequence CSV for viewing and analysis"""
        file, _ = QFileDialog.getOpenFileName(
            self,
            "Load Saved Sequence",
            self.save_path.text() if hasattr(self, 'save_path') else "./data",
            "CSV Files (*.csv);;All Files (*.*)"
        )
        
        if not file:
            return
        
        try:
            self.newLogMessage.emit(f"Loading sequence from: {os.path.basename(file)}")

            # First, read metadata from comment lines to get ADC max
            adc_max_from_file = None
            with open(file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if not line.startswith('#'):
                        break  # End of metadata
                    if 'ADC Max:' in line:
                        try:
                            adc_max_from_file = int(line.split(':')[1].strip())
                        except (ValueError, IndexError):
                            pass

            # Update adc_max if found in file metadata
            if adc_max_from_file is not None:
                self.adc_max = adc_max_from_file
                self.newLogMessage.emit(f"  ADC Max from file: {self.adc_max}")

            # Read CSV file with encoding fallback
            import pandas as pd
            df = None

            # Try different encodings
            for encoding in ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']:
                try:
                    df = pd.read_csv(file, comment='#', encoding=encoding)
                    self.newLogMessage.emit(f"  Loaded with {encoding} encoding")
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            
            if df is None:
                raise ValueError("Could not decode CSV file with any standard encoding")
            
            # Extract wavelengths (first column after Pixel)
            if 'Wavelength(nm)' in df.columns:
                wavelengths = df['Wavelength(nm)'].to_numpy()
            elif 'wavelength_nm' in df.columns:
                wavelengths = df['wavelength_nm'].to_numpy()
            else:
                wavelengths = df.iloc[:, 1].to_numpy()  # Second column
            
            # Extract all scan columns
            scan_cols = [col for col in df.columns if col.startswith('Scan_') or col in ['Average', 'Median']]
            
            if len(scan_cols) == 0:
                QMessageBox.warning(self, "Invalid File", "No scan data found in CSV file!")
                return
            
            # Load all spectra
            all_spectra = []
            for col in scan_cols:
                spectrum = df[col].to_numpy()
                all_spectra.append(spectrum)
            
            # Store loaded data
            self.acquired_wavelengths = wavelengths
            self.acquired_spectra = all_spectra
            self.current_scan_index = 0
            
            # Clear any previous analysis
            if hasattr(self, 'analysis_results'):
                delattr(self, 'analysis_results')
            
            # Enable navigation
            self.scan_number_input.setEnabled(True)
            self.scan_number_input.setMaximum(len(all_spectra))
            self.scan_number_input.setValue(1)
            self.scan_count_label.setText(f"/ {len(all_spectra)}")
            self.prev_scan_btn.setEnabled(False)
            self.next_scan_btn.setEnabled(True)
            
            self.analyze_btn.setEnabled(True)
            self.export_plot_btn.setEnabled(True)
            
            # Show first scan
            self.show_scan_by_index(0)
            
            self.newLogMessage.emit(f"✓ Loaded {len(all_spectra)} spectra from file")
            self.newLogMessage.emit(f"  Wavelength range: {wavelengths[0]:.2f} - {wavelengths[-1]:.2f} nm")
            
        except Exception as e:
            self.newLogMessage.emit(f"❌ Error loading sequence: {str(e)}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Load Error", f"Failed to load sequence:\n{str(e)}")
    
    def run_postprocessing(self):
        """Run post-processing analysis on acquired spectra"""
        if not hasattr(self, 'acquired_spectra') or len(self.acquired_spectra) == 0:
            QMessageBox.warning(self, "No Data", "No acquired spectra to analyze!")
            return
        
        calib_file = self.calib_path.text()
        if not calib_file or not os.path.exists(calib_file):
            QMessageBox.warning(self, "Calibration File Missing", 
                              "Please select a valid calibration file first!")
            return
        
        try:
            from Spectro_diode.spectrum_analysis import (read_calibration_file, analyze_spectrum, KFactorCache)

            self.newLogMessage.emit("Starting post-processing analysis...")
            self.analyze_btn.setEnabled(False)

            # Load calibration
            wl_calib, K_calib = read_calibration_file(calib_file)
            self.newLogMessage.emit(f"✓ Loaded calibration: {len(wl_calib)} wavelength points")

            # Analyze ALL spectra (including average and median)
            num_total = len(self.acquired_spectra)
            self.analysis_results = []

            # Create K factor cache (all scans in sequence share same wavelength grid)
            k_cache = KFactorCache()

            for i in range(num_total):
                result = analyze_spectrum(
                    self.acquired_wavelengths,
                    self.acquired_spectra[i],
                    wl_calib,
                    K_calib,
                    k_cache=k_cache
                )
                self.analysis_results.append(result)
            
            # Count detected spectra
            detected_count = sum(1 for r in self.analysis_results if r['fit_ok'])
            temps = [r['temperature_K'] for r in self.analysis_results if r['fit_ok']]
            
            if detected_count > 0:
                avg_temp = np.mean(temps)
                self.newLogMessage.emit(f"✅ Analysis complete: {detected_count}/{num_total} spectra analyzed")
                self.newLogMessage.emit(f"   Average temperature: {avg_temp:.0f} K")
                
                # Show all species detected
                all_species = set()
                for r in self.analysis_results:
                    all_species.update(r['species'])
                if all_species:
                    self.newLogMessage.emit(f"   Species detected: {', '.join(sorted(all_species))}")
            else:
                self.newLogMessage.emit("⚠️ No Planck fits successful (low signal or out of range)")
            
            # Enable showing analysis on current scan
            if self.current_scan_index < num_total:
                self.show_scan_with_analysis(self.current_scan_index)
            
        except Exception as e:
            self.newLogMessage.emit(f"❌ Analysis error: {str(e)}")
            QMessageBox.critical(self, "Analysis Error", f"Error during analysis:\n{str(e)}")
        finally:
            self.analyze_btn.setEnabled(True)
            self.export_plot_btn.setEnabled(True)
    def start_measurement(self):
        if not self.connected:
            self.newLogMessage.emit("ERROR: Not connected to spectrometer!")
            return
        
        # Check if measurement is already running
        if hasattr(self, 'measurement_thread') and self.measurement_thread and self.measurement_thread.isRunning():
            self.newLogMessage.emit("WARNING: Measurement already in progress!")
            return
        
        # Check if external trigger mode is active
        if hasattr(self, 'arduino_tab') and self.arduino_tab.is_external_mode():
            from PySide6.QtWidgets import QMessageBox
            
            # Verify Arduino is connected
            if not self.arduino_tab.connected:
                QMessageBox.warning(
                    self,
                    "Arduino Not Connected",
                    "External trigger mode is enabled but Arduino is not connected!\n\n"
                    "Please connect Arduino or switch to software trigger mode."
                )
                return
            
            # Note: We don't check if Arduino triggers are currently running because:
            # 1. Auto-start might be triggered after Arduino already finished sending triggers
            # 2. Spectrometer has a 10-second timeout - it will fail gracefully if no triggers arrive
            # 3. User might want to manually start measurement while Arduino is waiting/stopped
            
            self.newLogMessage.emit("Using EXTERNAL trigger mode (Arduino synchronized)")
        
        # Validate start/stop pixel values
        if self.start_pixel.value() >= self.stop_pixel.value():
            self.newLogMessage.emit("ERROR: Start pixel must be less than stop pixel!")
            return
        
        # Gather parameters
        params = {
            'integration_time': self.integration_time.value(),
            'integration_delay': 0,  # No longer user-configurable (Arduino controls if needed)
            'num_averages': self.num_averages.value(),
            'num_scans': self.num_scans.value(),
            'start_pixel': self.start_pixel.value(),
            'stop_pixel': self.stop_pixel.value(),
            'laser_enabled': False,  # Laser now controlled by Arduino or driver directly
            'laser_delay': 0,
            'laser_width': 0,
            'save_enabled': self.save_enable.isChecked(),
            'save_folder': self.save_path.text(),
            'external_trigger': hasattr(self, 'arduino_tab') and self.arduino_tab.is_external_mode()
        }
        
        trigger_mode = "External (Arduino)" if params['external_trigger'] else "Software"
        scan_label = "continuous" if params['num_scans'] == 0 else f"{params['num_scans']} scans"
        self.newLogMessage.emit(f"Starting measurement: {scan_label}, {params['integration_time']}ms, {trigger_mode}")
        
        # Disable controls
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        
        # Create and start measurement thread
        #self.measurement_thread = MeasurementThread()
        #self.measurement_thread.set_parameters(self.handle, self.num_pixels,
        #                                      self.wavelengths, params,
        #                                      self.display_queue,
        #                                      adc_max=self.adc_max)
        #self.measurement_thread.progress.connect(self.update_progress)
        #self.measurement_thread.spectrum_ready.connect(self.display_spectrum)
        #self.measurement_thread.status_update.connect(self.newLogMessage.emit)
        #self.measurement_thread.error.connect(self.handle_error)
        #self.measurement_thread.finished.connect(self.measurement_finished)
        #self.measurement_thread.data_acquired.connect(self.store_acquired_data)  # Store for navigation
        #self.measurement_thread.start()
    def stop_measurement(self):
        if self.measurement_thread:
            self.newLogMessage.emit("Stopping measurement...")
            self.measurement_thread.stop()

        if not hasattr(self, 'arduino_tab'):
            return
        at = self.arduino_tab
        if not (at.connected and at.arduino):
            return

        laser_inactive = getattr(at.arduino, 'laser_silenced', False)
        if not laser_inactive:
            status = at.arduino.get_status()
            if status:
                laser_inactive = (
                    not status['running'] or
                    (status['laser_cycles'] > 0 and status['cycles'] >= status['laser_cycles'])
                )
        if laser_inactive:
            at.stop_triggers()

    def toggle_live_display(self):
        """Start or stop live display mode"""
        if self.live_display_active:
            self.stop_live_display()
        else:
            self.start_live_display()
    def start_live_display(self):
        """Start continuous live spectrum display for alignment"""
        if not self.connected or self.handle is None:
            self.newLogMessage.emit("Error: Spectrometer not connected")
            return

        # Check if measurement is running
        if self.measurement_thread and self.measurement_thread.isRunning():
            self.newLogMessage.emit("Cannot start live display while measurement is running")
            return

        # Check if in external trigger mode (disabled for live display)
        if hasattr(self, 'arduino_tab') and self.arduino_tab.is_external_mode():
            self.newLogMessage.emit("Live display not available in external trigger mode")
            return

        self.live_display_active = True
        self.live_btn.setText("Stop Live Display")
        self.live_btn.setStyleSheet("""
            QPushButton { background-color: #E91E63; color: white; font-weight: bold; padding: 10px; }
            QPushButton:disabled { background-color: #a0a0a0; color: #606060; }
        """)

        # Disable measurement controls during live display
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)

        # Clear any analysis overlays
        if self.raw_curve is not None:
            self.raw_curve.setData([], [])
        if self.calibrated_curve is not None:
            self.calibrated_curve.setData([], [])
        if self.planck_curve is not None:
            self.planck_curve.setData([], [])
        if self.plot_legend is not None:
            self.plot_widget.removeItem(self.plot_legend)
            self.plot_legend = None

        # Create and configure live display thread
        self.live_display_thread = LiveDisplayThread()
        self.live_display_thread.set_parameters(
            self.handle,
            self.num_pixels,
            self.wavelengths,
            self.integration_time.value(),
            self.start_pixel.value(),
            self.stop_pixel.value()
        )

        # Connect signals
        self.live_display_thread.spectrum_ready.connect(self.display_live_spectrum)
        self.live_display_thread.status_update.connect(self.newLogMessage.emit)
        self.live_display_thread.error.connect(self.newLogMessage.emit)
        self.live_display_thread.finished.connect(self.on_live_display_finished)

        self.live_display_thread.start()
        self.newLogMessage.emit(f"Live display started (integration: {self.integration_time.value():.2f} ms)")

    def stop_live_display(self):
        """Stop live display mode"""
        if self.live_display_thread:
            self.live_display_thread.stop()
            self.live_display_thread.wait(2000)  # Wait up to 2 seconds

        self.live_display_active = False
        self.live_btn.setText("Start Live Display")
        self.live_btn.setStyleSheet("""
            QPushButton { background-color: #9C27B0; color: white; font-weight: bold; padding: 10px; }
            QPushButton:disabled { background-color: #a0a0a0; color: #606060; }
        """)

        # Re-enable measurement controls
        if self.connected:
            self.start_btn.setEnabled(True)

        # Note: "Live display stopped" message is emitted by the thread when it finishes

    def on_live_display_finished(self):
        """Handle live display thread finished"""
        self.live_display_active = False
        self.live_btn.setText("Start Live Display")
        self.live_btn.setStyleSheet("""
            QPushButton { background-color: #9C27B0; color: white; font-weight: bold; padding: 10px; }
            QPushButton:disabled { background-color: #a0a0a0; color: #606060; }
        """)

        # Re-enable measurement controls
        if self.connected:
            self.start_btn.setEnabled(True)

    def display_live_spectrum(self, wavelengths, spectrum):
        """Display spectrum from live display mode (no processing)"""
        # Clear analysis curves
        if self.raw_curve is not None:
            self.raw_curve.setData([], [])
        if self.calibrated_curve is not None:
            self.calibrated_curve.setData([], [])
        if self.planck_curve is not None:
            self.planck_curve.setData([], [])

        # Update plot
        self.spectrum_curve.setData(wavelengths, spectrum)

        # Update title
        self.plot_widget.setTitle('Spectrum - Live Display')

        # Update basic statistics
        max_val = np.max(spectrum)
        min_val = np.min(spectrum)
        mean_val = np.mean(spectrum)
        std_val = np.std(spectrum)
        max_wl = wavelengths[np.argmax(spectrum)]

        self.stat_labels['Max Intensity'].setText(f"{max_val:.2f} counts")
        self.stat_labels['Max Wavelength'].setText(f"{max_wl:.2f} nm")
        self.stat_labels['Min Intensity'].setText(f"{min_val:.2f} counts")
        self.stat_labels['Mean Intensity'].setText(f"{mean_val:.2f} counts")
        self.stat_labels['Std Deviation'].setText(f"{std_val:.2f} counts")

    def on_trigger_mode_changed(self, is_external):
        """Handle trigger mode change from Arduino tab"""
        # Stop live display if switching to external mode
        if is_external and self.live_display_active:
            self.stop_live_display()
            self.newLogMessage.emit("Live display stopped - external trigger mode selected")

        # Disable averages in external trigger mode (each scan is a distinct laser shot)
        self.num_averages.setEnabled(not is_external)
        self.num_averages_label.setEnabled(not is_external)

        # Enable/disable live button based on mode and connection
        if self.connected:
            self.live_btn.setEnabled(not is_external)

    def on_protection_activated(self, protections):
        """Handle protection activation from driver tab - stop Arduino triggers"""
        if hasattr(self, 'arduino_tab') and self.arduino_tab.connected:
            # Check if triggers are running
            if hasattr(self.arduino_tab, 'trigger_status_text'):
                if self.arduino_tab.trigger_status_text.text() == "RUNNING":
                    self.newLogMessage.emit(f"⚠️ Stopping Arduino triggers due to protection: {', '.join(protections)}")
                    if self.arduino_tab.arduino:
                        self.arduino_tab.arduino.stop()
                        self.arduino_tab.trigger_status.setStyleSheet("color: red;")
                        self.arduino_tab.trigger_status_text.setText("STOPPED")
                        self.arduino_tab.trigger_status_text.setStyleSheet("font-weight: bold; color: red;")
                        self.arduino_tab.start_btn.setEnabled(True)
                        self.arduino_tab.stop_btn.setEnabled(False)

    def update_progress(self, current, total):
        progress = int((current / total) * 100)
        self.progress_bar.setValue(progress)

    def on_y_auto_scale_changed(self, checked):
        """Handle Y-axis auto-scale checkbox change"""
        if not checked:
            # Snapshot the current auto-scaled range into the spinboxes so the
            # view does not jump when the user disables auto-scale.
            y_min_counts, y_max_counts = self.plot_widget.viewRange()[1]
            y_min_pct = max(0.0, min(100.0, y_min_counts * 100.0 / self.adc_max))
            y_max_pct = max(0.0, min(100.0, y_max_counts * 100.0 / self.adc_max))
            self.y_min_spin.blockSignals(True)
            self.y_max_spin.blockSignals(True)
            self.y_min_spin.setValue(y_min_pct)
            self.y_max_spin.setValue(y_max_pct)
            self.y_min_spin.blockSignals(False)
            self.y_max_spin.blockSignals(False)

        self.y_min_spin.setEnabled(not checked)
        self.y_max_spin.setEnabled(not checked)
        self.apply_y_axis_limits()

    def apply_y_axis_limits(self):
        """Apply Y-axis limits based on auto-scale setting"""
        if self.y_auto_scale_cb.isChecked():
            # Enable auto-range on Y axis
            self.plot_widget.enableAutoRange(axis='y')
        else:
            # Set manual Y range - convert percentage to counts using ADC max
            y_min_pct = self.y_min_spin.value()
            y_max_pct = self.y_max_spin.value()
            if y_min_pct < y_max_pct:
                y_min_counts = y_min_pct * self.adc_max / 100
                y_max_counts = y_max_pct * self.adc_max / 100
                self.plot_widget.setYRange(y_min_counts, y_max_counts, padding=0)

    def display_spectrum(self, wavelengths, spectrum, stats):
        # Hide analysis curves if they exist (switching from analysis view to normal)
        if self.raw_curve is not None:
            self.raw_curve.setData([], [])
        if self.calibrated_curve is not None:
            self.calibrated_curve.setData([], [])
        if self.planck_curve is not None:
            self.planck_curve.setData([], [])

        # Remove legend if present
        if self.plot_legend is not None:
            self.plot_widget.removeItem(self.plot_legend)
            self.plot_legend = None

        # Update plot using setData for performance (no clear/recreate)
        self.spectrum_curve.setData(wavelengths, spectrum)
        
        # Update title with scan type
        scan_num = stats.get('scan_number', 0)
        scan_type = stats.get('scan_type', 'Normal')
        
        if scan_type == "Average":
            self.plot_widget.setTitle(f'Spectrum - Scan #{scan_num} (Average)')
        elif scan_type == "Median":
            self.plot_widget.setTitle(f'Spectrum - Scan #{scan_num} (Median)')
        elif scan_num == 0:
            self.plot_widget.setTitle('Spectrum - Average of All Scans')
        elif scan_num > 0:
            self.plot_widget.setTitle(f'Spectrum - Scan #{scan_num}')
        else:
            self.plot_widget.setTitle('Spectrum')
        
        # Update statistics
        self.stat_labels['Max Intensity'].setText(f"{stats['max']:.2f} counts")
        self.stat_labels['Max Wavelength'].setText(f"{stats['max_wavelength']:.2f} nm")
        self.stat_labels['Min Intensity'].setText(f"{stats['min']:.2f} counts")
        self.stat_labels['Mean Intensity'].setText(f"{stats['mean']:.2f} counts")
        self.stat_labels['Std Deviation'].setText(f"{stats['std']:.2f} counts")
        
        sat_text = f"{stats['saturation']:.1f}%"
        if stats['saturation'] > 90:
            sat_text += " ⚠️"
            self.stat_labels['Saturation'].setStyleSheet("color: red; font-weight: bold;")
        else:
            self.stat_labels['Saturation'].setStyleSheet("")
        self.stat_labels['Saturation'].setText(sat_text)

        # Apply Y-axis limits
        self.apply_y_axis_limits()

    def handle_error(self, error_msg):
        self.newLogMessage.emit(f"ERROR: {error_msg}")
        QMessageBox.critical(self, "Measurement Error",
                             f"An error occurred during measurement:\n\n{error_msg}\n\n"
                             "Data may not have been saved correctly.")
        
    def measurement_finished(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(100)
        self.newLogMessage.emit("Measurement sequence complete")
        
        # Enable navigation controls if we have acquired data
        if len(self.acquired_spectra) > 0:
            self.scan_number_input.setEnabled(True)
            self.scan_number_input.setMaximum(len(self.acquired_spectra))
            self.scan_number_input.setValue(len(self.acquired_spectra))  # Show last scan
            self.scan_count_label.setText(f"/ {len(self.acquired_spectra)}")
            self.prev_scan_btn.setEnabled(True)
            self.next_scan_btn.setEnabled(False)

            # Enable post-processing button
            self.analyze_btn.setEnabled(True)
            self.export_plot_btn.setEnabled(True)
            
            # Auto post-processing if enabled
            if self.postproc_enable.isChecked():
                calib_file = self.calib_path.text()
                if calib_file and os.path.exists(calib_file):
                    self.newLogMessage.emit("Auto post-processing enabled - starting analysis...")
                    self.run_postprocessing()
                else:
                    self.newLogMessage.emit("⚠️ Auto post-processing enabled but no calibration file set")
        
    def disconnect_spectrometer(self):
        """Properly disconnect from spectrometer"""
        self._spectro_check_timer.stop()
        if self.connected and self.handle:
            try:
                self.newLogMessage.emit("Disconnecting from spectrometer...")
                AVS_Deactivate(self.handle)
                AVS_Done()
                self.connected = False
                self.handle = None
                self.newLogMessage.emit("Spectrometer disconnected successfully")
            except Exception as e:
                self.newLogMessage.emit(f"Error during disconnect: {str(e)}")

    def _check_spectrometer_connection(self):
        """Periodic USB presence check — fallback in case nativeEvent doesn't fire."""
        if not self.connected:
            return
        try:
            n = AVS_UpdateUSBDevices()
            if n == 0:
                self._on_spectrometer_lost()
        except Exception:
            self._on_spectrometer_lost()

    def _on_spectrometer_lost(self):
        """Handle unexpected USB disconnection of the spectrometer."""
        # Mark as disconnected immediately so timers and nativeEvent don't re-enter
        self.connected = False
        self._spectro_check_timer.stop()

        # Stop any running threads before invalidating the handle.
        # device_lost prevents the thread from calling avs_stop_measure on a dead handle,
        # which would race with AVS_Done and segfault.
        if self.measurement_thread and self.measurement_thread.isRunning():
            self.measurement_thread.device_lost = True
            self.measurement_thread.running = False
            self.measurement_thread.wait(2000)
        if self.live_display_active:
            self.stop_live_display()

        # Stop Arduino triggers — on USB-over-Ethernet both devices drop together
        if hasattr(self, 'arduino_tab') and self.arduino_tab.connected:
            try:
                self.arduino_tab.on_connection_lost()
            except Exception:
                pass

        # Clean up SDK state (best-effort — handle is likely invalid)
        try:
            AVS_Done()
        except Exception:
            pass

        self.handle = None
        self._disconnect_in_progress = False

        # Reset UI
        self.device_info.setText("DISCONNECTED\nReconnect USB cable\nthen press Connect")
        self.device_info.setStyleSheet("color: red; font-weight: bold;")
        self.connect_btn.setEnabled(True)
        self.start_btn.setEnabled(False)
        self.live_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(0)

        self.newLogMessage.emit("ERROR: Spectrometer USB disconnected! Reconnect the cable and press Connect.")



class LiveDisplayThread(QThread):
    """Thread for continuous live display without saving or processing (Equivalent to Camera Workers)"""
    spectrum_ready = Signal(np.ndarray, np.ndarray)  # wavelengths, spectrum
    status_update = Signal(str)
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.handle = None
        self.num_pixels = 0
        self.wavelengths = None
        self.running = False
        self.integration_time = 10.0
        self.start_pixel = 0
        self.stop_pixel = 0

    def set_parameters(self, handle, num_pixels, wavelengths, integration_time, start_pixel, stop_pixel):
        self.handle = handle
        self.num_pixels = num_pixels
        self.wavelengths = wavelengths
        self.integration_time = integration_time
        self.start_pixel = start_pixel
        self.stop_pixel = stop_pixel

    def stop(self):
        self.running = False
        if self.handle:
            try:
                avs_stop_measure(self.handle)
            except:
                pass

    def run(self):
        self.running = True

        # Ensure spectrometer is in clean state
        try:
            avs_stop_measure(self.handle)
            time.sleep(0.1)
        except:
            pass

        try:
            # Configure for software trigger, single scan at a time
            avs_use_high_res_adc(self.handle, True)

            measconfig = MeasConfigType()
            measconfig.m_StartPixel = self.start_pixel
            measconfig.m_StopPixel = self.stop_pixel
            measconfig.m_IntegrationTime = self.integration_time
            measconfig.m_IntegrationDelay = 0
            measconfig.m_NrAverages = 1
            measconfig.m_CorDynDark_m_Enable = 0
            measconfig.m_CorDynDark_m_ForgetPercentage = 0
            measconfig.m_Smoothing_m_SmoothPix = 0
            measconfig.m_Smoothing_m_SmoothModel = 0
            measconfig.m_SaturationDetection = 1
            measconfig.m_Trigger_m_Mode = 0  # Software trigger
            measconfig.m_Trigger_m_Source = 0
            measconfig.m_Trigger_m_SourceType = 0
            measconfig.m_Control_m_StrobeControl = 0  # No laser
            measconfig.m_Control_m_LaserDelay = 0
            measconfig.m_Control_m_LaserWidth = 0
            measconfig.m_Control_m_LaserWaveLength = 0.0
            measconfig.m_Control_m_StoreToRam = 0

            ret = avs_prepare_measure(self.handle, measconfig)
            if ret != 0:
                self.newLogMessage.emit(f"Failed to prepare live display. Error code: {ret}")
                return

            self.newLogMessage.emit("Live display started")

            # Continuous acquisition loop
            while self.running:
                # Start single measurement (window_handle=0, nummeas=1)
                ret = avs_measure(self.handle, 0, 1)
                if ret != 0:
                    if self.running:
                        self.newLogMessage.emit(f"Measurement error: {ret}")
                    break

                # Poll for data ready
                timeout_count = 0
                max_timeout = int((self.integration_time + 1000) / 10)  # Timeout based on integration time

                while self.running:
                    try:
                        ready = avs_poll_scan(self.handle)
                    except Exception:
                        self.running = False
                        break
                    if ready:
                        break
                    time.sleep(0.01)
                    timeout_count += 1
                    if timeout_count > max_timeout:
                        break

                if not self.running:
                    break

                if not ready:
                    continue  # Timeout, try again

                # Get data
                try:
                    timestamp, spectrum = avs_get_scope_data(self.handle)

                    # Calculate wavelength array for the configured range
                    wavelength_array = np.array([self.wavelengths[i] for i in range(self.start_pixel, self.stop_pixel + 1)])

                    # AVS_GetScopeData always returns a full 4096-element array.
                    spectrum_array = np.array(spectrum[self.start_pixel:self.stop_pixel + 1])

                    # Emit for display (no processing, no saving)
                    self.spectrum_ready.emit(wavelength_array, spectrum_array)

                except Exception as e:
                    if self.running:
                        self.newLogMessage.emit(f"Live display error: {str(e)}")

            self.newLogMessage.emit("Live display stopped")

        except Exception as e:
            self.newLogMessage.emit(f"Live display error: {str(e)}")

        finally:
            try:
                avs_stop_measure(self.handle)
            except:
                pass