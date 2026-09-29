from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
                               QComboBox, QGroupBox, QGridLayout, QDoubleSpinBox, 
                               QSpinBox, QStatusBar, QSlider, QLineEdit, QStyle, QMessageBox, QFileDialog,
                               QCheckBox, QProgressBar)
from PySide6.QtCore import Signal, Qt, QThread, QSettings, QObject, Slot
from PySide6.QtGui import  QAction , QPixmap
from Spectro_diode.src.avaspec import *
import os
import sys
import numpy as np



# Check for simulation mode
SIMULATION_MODE = '--simulate' in sys.argv
if SIMULATION_MODE:
    from Spectro_diode.src.hardware_simulator import (
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
    def __init__(self):
        super().__init__()

        self._create_tab()

    def _create_tab(self):
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
            logo_path = r"C:\Users\guill\Desktop\Testbench Control\Camera_turret\GUI_Images\logo.png"
            logo_pixmap = QPixmap(logo_path)
            if not logo_pixmap.isNull():
                # Scale logo to fit nicely (max width 300px)
                scaled_logo = logo_pixmap.scaledToWidth(280, Qt.SmoothTransformation)
                logo_label.setPixmap(scaled_logo)
                logo_label.setAlignment(Qt.Alignmain | Qt.AlignBottom)
                logo_label.setStyleSheet("padding: 5px;")
                main_panel.addWidget(logo_label)
            else:
                print(f"⚠️ RAYOPS_logo.jpg not found at: {logo_path}")
        except Exception as e:
            print(f"⚠️ Could not load logo: {e}")     

        main_panel.addWidget(logo_label)

        return main_panel

    def connect_spectrometer(self):
            try:
                if SIMULATION_MODE:
                    self.log_status("🔧 SIMULATION MODE - Initializing simulated spectrometer...")
                    ret = AVS_Init_Sim(0)
                else:
                    self.log_status("Initializing Avantes library...")
                    ret = AVS_Init(0)
    
                if ret < 0:
                    self.log_status(f"ERROR: Failed to initialize! Code: {ret}")
                    return
    
                self.log_status(f"Found {ret} device(s)")
    
                if SIMULATION_MODE:
                    num_devices = AVS_GetNrOfDevices_Sim()
                else:
                    num_devices = AVS_GetNrOfDevices()
    
                if num_devices == 0:
                    self.log_status("ERROR: No spectrometers found!")
                    return
    
                if SIMULATION_MODE:
                    device_list = AVS_GetList_Sim(num_devices)
                else:
                    device_list = AVS_GetList(num_devices)
    
                self.serial = device_list[0].SerialNumber.decode("utf-8")
                name = device_list[0].UserFriendlyName.decode("utf-8") if hasattr(device_list[0], 'UserFriendlyName') else "Simulated"
    
                self.log_status(f"Connecting to: {self.serial}")
    
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
                        self.log_status("ERROR: Failed to activate spectrometer!")
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
    
                self.log_status("Connection successful!")
                self.log_status(f"Wavelength range: {wl_min:.2f} - {wl_max:.2f} nm")
    
                if not SIMULATION_MODE:
                    self._spectro_check_timer.start(200)  # Poll USB presence every 200 ms
    
                if hasattr(self, 'arduino_tab'):
                    self.arduino_tab.update_timing_display()
    
            except Exception as e:
                self.log_status(f"ERROR: {str(e)}")
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
            self.log_status(f"Calibration file selected: {os.path.basename(file)}")
    
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
            self.log_status(f"Loading sequence from: {os.path.basename(file)}")

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
                self.log_status(f"  ADC Max from file: {self.adc_max}")

            # Read CSV file with encoding fallback
            import pandas as pd
            df = None

            # Try different encodings
            for encoding in ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']:
                try:
                    df = pd.read_csv(file, comment='#', encoding=encoding)
                    self.log_status(f"  Loaded with {encoding} encoding")
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
            
            self.log_status(f"✓ Loaded {len(all_spectra)} spectra from file")
            self.log_status(f"  Wavelength range: {wavelengths[0]:.2f} - {wavelengths[-1]:.2f} nm")
            
        except Exception as e:
            self.log_status(f"❌ Error loading sequence: {str(e)}")
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
            from Spectro_diode.src.spectrum_analysis import (read_calibration_file, analyze_spectrum, KFactorCache)

            self.log_status("Starting post-processing analysis...")
            self.analyze_btn.setEnabled(False)

            # Load calibration
            wl_calib, K_calib = read_calibration_file(calib_file)
            self.log_status(f"✓ Loaded calibration: {len(wl_calib)} wavelength points")

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
                self.log_status(f"✅ Analysis complete: {detected_count}/{num_total} spectra analyzed")
                self.log_status(f"   Average temperature: {avg_temp:.0f} K")
                
                # Show all species detected
                all_species = set()
                for r in self.analysis_results:
                    all_species.update(r['species'])
                if all_species:
                    self.log_status(f"   Species detected: {', '.join(sorted(all_species))}")
            else:
                self.log_status("⚠️ No Planck fits successful (low signal or out of range)")
            
            # Enable showing analysis on current scan
            if self.current_scan_index < num_total:
                self.show_scan_with_analysis(self.current_scan_index)
            
        except Exception as e:
            self.log_status(f"❌ Analysis error: {str(e)}")
            QMessageBox.critical(self, "Analysis Error", f"Error during analysis:\n{str(e)}")
        finally:
            self.analyze_btn.setEnabled(True)
            self.export_plot_btn.setEnabled(True)
    def start_measurement(self):
        if not self.connected:
            self.log_status("ERROR: Not connected to spectrometer!")
            return
        
        # Check if measurement is already running
        if hasattr(self, 'measurement_thread') and self.measurement_thread and self.measurement_thread.isRunning():
            self.log_status("WARNING: Measurement already in progress!")
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
            
            self.log_status("Using EXTERNAL trigger mode (Arduino synchronized)")
        
        # Validate start/stop pixel values
        if self.start_pixel.value() >= self.stop_pixel.value():
            self.log_status("ERROR: Start pixel must be less than stop pixel!")
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
        self.log_status(f"Starting measurement: {scan_label}, {params['integration_time']}ms, {trigger_mode}")
        
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
        #self.measurement_thread.status_update.connect(self.log_status)
        #self.measurement_thread.error.connect(self.handle_error)
        #self.measurement_thread.finished.connect(self.measurement_finished)
        #self.measurement_thread.data_acquired.connect(self.store_acquired_data)  # Store for navigation
        #self.measurement_thread.start()