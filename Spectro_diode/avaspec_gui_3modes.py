"""
AvaSpec-Mini4096CL GUI Control Application
Professional interface for spectrometer control with full parameter configuration
"""
import sys
import os
import time
import faulthandler
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(PROJECT_ROOT, relative_path)  
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QCheckBox, QFileDialog, QGroupBox, QGridLayout,
                             QTextEdit, QProgressBar, QDoubleSpinBox, QSpinBox,
                             QMessageBox, QTabWidget)
from PySide6.QtCore import QThread, Signal, Qt, QTimer, QSettings
from PySide6.QtGui import QFont, QPixmap
import PySide6
import pyqtgraph as pg
from pyqtgraph.exporters import ImageExporter
import numpy as np

# Enable OpenGL acceleration for smoother plotting
pg.setConfigOptions(useOpenGL=True, antialias=True)
from internal.Spectro.avaspec import *
from internal.Diode_driver.driver_control_tab import DriverControlTab
from internal.arduino_trigger_tab import ArduinoTriggerTab
from queue import Queue

# Check for simulation mode
SIMULATION_MODE = '--simulate' in sys.argv
if SIMULATION_MODE:
    from internal.Spectro.hardware_simulator import (
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

# === AVASPEC TIMING CONSTANTS ===
FPGA_CLOCK_PERIOD_NS = 20.83  # nanoseconds
FPGA_CLOCK_PERIOD_US = 0.02083  # microseconds

def us_to_clock_cycles(microseconds):
    """Convert microseconds to FPGA clock cycles (rounded to nearest integer)"""
    return round(microseconds / FPGA_CLOCK_PERIOD_US)

def clock_cycles_to_us(clock_cycles):
    """Convert FPGA clock cycles to microseconds"""
    return clock_cycles * FPGA_CLOCK_PERIOD_US

class MeasurementThread(QThread):
    """Thread for running measurements without blocking GUI"""
    progress = Signal(int, int)  # current, total
    spectrum_ready = Signal(np.ndarray, np.ndarray, dict)  # wavelengths, spectrum, stats
    status_update = Signal(str)
    finished = Signal()
    error = Signal(str)
    data_acquired = Signal(list, object)  # (all_spectra, wavelength_array) for navigation
    
    def __init__(self):
        super().__init__()
        self.handle = None
        self.num_pixels = 0
        self.wavelengths = None
        self.running = False
        self.device_lost = False
        self.params = {}
        self.adc_max = 65535

    def set_parameters(self, handle, num_pixels, wavelengths, params, display_queue=None, adc_max=65535):
        self.handle = handle
        self.num_pixels = num_pixels
        self.wavelengths = wavelengths
        self.params = params
        self.display_queue = display_queue  # Queue for async display
        self.adc_max = adc_max

    def stop(self):
        self.running = False
        # Skip DLL call if device is gone — calling avs_stop_measure on a dead handle
        # from the main thread while the worker thread may still be in the DLL causes crashes.
        if self.handle and not self.device_lost:
            try:
                avs_stop_measure(self.handle)
            except:
                pass
        
    def run(self):
        self.running = True
        num_scans = self.params['num_scans']
        
        # Storage for all scans in this sequence
        all_spectra = []
        wavelength_array = None
        sequence_start_time = time.time()  # Unix timestamp for time.strftime compatibility
        
        # Ensure spectrometer is in clean state
        try:
            # Stop any previous measurement
            avs_stop_measure(self.handle)
            time.sleep(0.2)  # Let hardware fully reset
            self.status_update.emit("✓ Spectrometer reset complete")
        except Exception as e:
            self.status_update.emit(f"Reset: {str(e)}")
        
        try:
            # Configure measurement
            self.status_update.emit("Configuring measurement...")
            
            avs_use_high_res_adc(self.handle, True)
            
            # Check trigger mode early - needed for configuration
            is_external_trigger = self.params.get('external_trigger', False)
            
            measconfig = MeasConfigType()
            measconfig.m_StartPixel = self.params['start_pixel']
            measconfig.m_StopPixel = self.params['stop_pixel']
            measconfig.m_IntegrationTime = self.params['integration_time']
            measconfig.m_IntegrationDelay = self.params['integration_delay']
            measconfig.m_NrAverages = self.params['num_averages']
            measconfig.m_CorDynDark_m_Enable = 0
            measconfig.m_CorDynDark_m_ForgetPercentage = 0
            measconfig.m_Smoothing_m_SmoothPix = 0
            measconfig.m_Smoothing_m_SmoothModel = 0
            measconfig.m_SaturationDetection = 1
            
            # TRIGGER CONFIGURATION - Software or Hardware
            if is_external_trigger:
                # External hardware trigger mode (Arduino)
                # Use SINGLE SCAN mode (mode 2) for one scan per trigger
                # Mode 1 (Hardware) would take all scans after first trigger
                measconfig.m_Trigger_m_Mode = 2  # Single Scan trigger (one scan per trigger)
                measconfig.m_Trigger_m_Source = 0  # External trigger input
                measconfig.m_Trigger_m_SourceType = 0  # Edge trigger (0=edge, 1=level)
            else:
                # Software trigger mode (default)
                measconfig.m_Trigger_m_Mode = 0  # Software trigger
                measconfig.m_Trigger_m_Source = 0
                measconfig.m_Trigger_m_SourceType = 0
            
            # LASER CONTROL
            if self.params['laser_enabled']:
                measconfig.m_Control_m_StrobeControl = 1  # Enable strobe (laser output)
                measconfig.m_Control_m_LaserDelay = self.params['laser_delay']
                measconfig.m_Control_m_LaserWidth = self.params['laser_width']
            else:
                measconfig.m_Control_m_StrobeControl = 0
                measconfig.m_Control_m_LaserDelay = 0
                measconfig.m_Control_m_LaserWidth = 0
                
            measconfig.m_Control_m_LaserWaveLength = 0.0
            
            # StoreToRam configuration
            if is_external_trigger:
                # Single Scan trigger mode does NOT support StoreToRam
                # We must retrieve each scan individually
                measconfig.m_Control_m_StoreToRam = 0
            else:
                # Software mode: no StoreToRam (retrieve each scan immediately)
                measconfig.m_Control_m_StoreToRam = 0
            
            ret = avs_prepare_measure(self.handle, measconfig)
            if ret != 0:
                self.error.emit(f"Failed to prepare measurement. Error code: {ret}")
                return
            
            # MEASUREMENT LOOP - Different behavior for software vs hardware trigger
            
            if is_external_trigger:
                # ====================================================================
                # SINGLE SCAN TRIGGER MODE (one scan per trigger)
                # ====================================================================
                # Single Scan mode: Request N scans, each scan waits for a trigger
                # No StoreToRam, but we can call AVS_Measure once and poll for each scan
                # ====================================================================

                # Start measurement for ALL scans - each will wait for its own trigger
                # 65535 = uint16 max; AvaSpec SDK treats it as "arm indefinitely"
                arm_count = num_scans if num_scans > 0 else 65535
                ret = avs_measure(self.handle, 0, arm_count)
                if ret != 0:
                    self.error.emit(f"Failed to start measurement. Error: {ret}")
                    return

                count_str = str(num_scans) if num_scans > 0 else "continuous"
                self.status_update.emit(f"✓ Spectrometer armed - waiting for {count_str} triggers...")

                # Now poll and retrieve each scan as triggers arrive
                # OPTIMIZED for maximum speed (targeting 100 Hz = 10ms per scan)
                scan_num = 0
                while self.running and (num_scans == 0 or scan_num < num_scans):
                    if not self.running:
                        self.status_update.emit("Measurement stopped by user")
                        AVS_StopMeasure(self.handle)
                        break
                    
                    # ================================================================
                    # CRITICAL SECTION: Minimize latency
                    # ================================================================
                    poll_start = time.perf_counter()
                    timeout_seconds = 2.0  # 2 second timeout per scan
                    
                    # Ultra-fast polling: 0.1ms sleep instead of 5ms
                    user_stopped = False
                    while time.perf_counter() - poll_start < timeout_seconds:
                        if not self.running:
                            user_stopped = True
                            break
                        try:
                            if avs_poll_scan(self.handle):
                                break
                        except Exception:
                            # Device lost mid-sequence — exit immediately
                            self.running = False
                            user_stopped = True
                            break
                        time.sleep(0.0001)  # 100 microseconds (0.1ms) - 50x faster!

                    if user_stopped:
                        self.status_update.emit("Measurement stopped by user")
                        if not self.device_lost:
                            try:
                                AVS_StopMeasure(self.handle)
                            except Exception:
                                pass
                        break

                    if time.perf_counter() - poll_start >= timeout_seconds:
                        self.error.emit(f"Timeout waiting for trigger {scan_num + 1}!")

                        # Emit partial data for navigation
                        if len(all_spectra) > 0 and wavelength_array is not None:
                            self.data_acquired.emit(all_spectra, wavelength_array)

                        # Save partial data
                        if len(all_spectra) > 0:
                            if self.params['save_enabled'] and wavelength_array is not None:
                                self.save_sequence_to_csv(wavelength_array, all_spectra, sequence_start_time)
                            partial_str = f"{len(all_spectra)}/{num_scans}" if num_scans > 0 else str(len(all_spectra))
                            self.status_update.emit(f"Partial sequence saved: {partial_str} scans")

                        AVS_StopMeasure(self.handle)
                        return
                    
                    # Get data for this scan - FAST retrieval
                    timestamp, spectrum = avs_get_scope_data(self.handle)
                    
                    # ================================================================
                    # FAST PATH: Minimal processing during acquisition
                    # ================================================================
                    # Calculate wavelength array only once
                    if wavelength_array is None:
                        start_px = self.params['start_pixel']
                        stop_px = self.params['stop_pixel']
                        wavelength_array = np.array([self.wavelengths[i] for i in range(start_px, stop_px + 1)])
                    
                    # AVS_GetScopeData always returns a full 4096-element array.
                    # Must index by physical pixel, not from 0.
                    spectrum_array = np.array(spectrum[start_px:stop_px + 1])
                    
                    # ALWAYS store for navigation (not just when saving)
                    all_spectra.append(spectrum_array.copy())

                    # Calculate statistics for this scan
                    stats = {
                        'max': np.max(spectrum_array),
                        'min': np.min(spectrum_array),
                        'mean': np.mean(spectrum_array),
                        'std': np.std(spectrum_array),
                        'max_wavelength': wavelength_array[np.argmax(spectrum_array)],
                        'saturation': (np.max(spectrum_array) / 65535.0) * 100,
                        'scan_number': scan_num + 1,
                        'timestamp': 0
                    }

                    # Queue data for async display (non-blocking!) - update graph during acquisition
                    if self.display_queue is not None:
                        try:
                            self.display_queue.put_nowait((wavelength_array, spectrum_array, stats))
                        except:
                            pass  # Queue full - skip this update, keep acquiring

                    # Progress update
                    if num_scans > 0:
                        self.progress.emit(scan_num + 1, num_scans)
                    scan_num += 1

                # ================================================================
                # POST-ACQUISITION: Display acquisition rate
                # avg/median/save/completed are handled by the shared section below
                # ================================================================
                total_time = time.time() - sequence_start_time
                actual_rate = len(all_spectra) / total_time if total_time > 0 else 0
                self.status_update.emit(f"✓ Acquisition complete: {actual_rate:.1f} scans/second")
                
            else:
                # SOFTWARE TRIGGER MODE
                scan_num = 0
                while self.running and (num_scans == 0 or scan_num < num_scans):
                    if not self.running:
                        self.status_update.emit("Measurement stopped by user")
                        break
                    
                    # Start THIS scan (triggers laser if enabled)
                    ret = avs_measure(self.handle, 0, 1)
                    if ret != 0:
                        self.error.emit(f"Failed to start scan {scan_num + 1}. Error: {ret}")
                        return
                    
                    # Optimized polling - check every 5ms instead of 100ms
                    timeout = 0
                    max_timeout = 2000  # 10 seconds (5ms * 2000)
                    user_stopped = False

                    while timeout < max_timeout:
                        if not self.running:
                            user_stopped = True
                            break
                        try:
                            if avs_poll_scan(self.handle):
                                break
                        except Exception:
                            self.device_lost = True
                            user_stopped = True
                            break
                        time.sleep(0.005)  # 5ms polling - much faster!
                        timeout += 1

                    if user_stopped:
                        self.status_update.emit("Measurement stopped by user")
                        if not self.device_lost:
                            try:
                                AVS_StopMeasure(self.handle)
                            except Exception:
                                pass
                        break

                    if timeout >= max_timeout:
                        self.error.emit(f"Timeout on scan {scan_num + 1}!")

                        # Emit partial data for navigation
                        if len(all_spectra) > 0 and wavelength_array is not None:
                            self.data_acquired.emit(all_spectra, wavelength_array)

                        # Save partial data
                        if len(all_spectra) > 0:
                            if self.params['save_enabled'] and wavelength_array is not None:
                                self.save_sequence_to_csv(wavelength_array, all_spectra, sequence_start_time)
                            partial_str = f"{len(all_spectra)}/{num_scans}" if num_scans > 0 else str(len(all_spectra))
                            self.status_update.emit(f"Partial sequence saved: {partial_str} scans")

                        return
                    
                    # Get data
                    timestamp, spectrum = avs_get_scope_data(self.handle)
                    
                    start_px = self.params['start_pixel']
                    stop_px = self.params['stop_pixel']

                    # Calculate wavelength array for the configured range
                    wavelength_array = np.array([self.wavelengths[i] for i in range(start_px, stop_px + 1)])

                    # AVS_GetScopeData always returns a full 4096-element array.
                    # Must index by physical pixel, not from 0.
                    spectrum_array = np.array(spectrum[start_px:stop_px + 1])
                    
                    # ALWAYS store spectrum for navigation (not just when saving)
                    all_spectra.append(spectrum_array.copy())
                    
                    # Calculate statistics
                    stats = {
                        'max': np.max(spectrum_array),
                        'min': np.min(spectrum_array),
                        'mean': np.mean(spectrum_array),
                        'std': np.std(spectrum_array),
                        'max_wavelength': wavelength_array[np.argmax(spectrum_array)],
                        'saturation': (np.max(spectrum_array) / 65535.0) * 100,
                        'scan_number': scan_num + 1,
                        'timestamp': timestamp
                    }
                    
                    # Update progress
                    if num_scans > 0:
                        self.progress.emit(scan_num + 1, num_scans)
                    scan_num += 1

                    # Queue data for async display (non-blocking!)
                    if self.display_queue is not None:
                        try:
                            # Put in queue without blocking (drop if queue full)
                            self.display_queue.put_nowait((wavelength_array, spectrum_array, stats))
                        except:
                            pass  # Queue full - skip this update, keep acquiring
                    else:
                        # Fallback to direct emit if no queue
                        self.spectrum_ready.emit(wavelength_array, spectrum_array, stats)

            # ================================================================
            # POST-ACQUISITION: Add average and median scans
            # ================================================================
            if len(all_spectra) > 0:
                # Calculate average spectrum
                avg_spectrum = np.mean(all_spectra, axis=0)
                all_spectra.append(avg_spectrum)
                self.status_update.emit(f"✓ Added average spectrum (scan #{len(all_spectra)})")
                
                # Calculate median spectrum  
                median_spectrum = np.median(all_spectra[:-1], axis=0)  # Exclude the average we just added
                all_spectra.append(median_spectrum)
                self.status_update.emit(f"✓ Added median spectrum (scan #{len(all_spectra)})")
            
            # Emit all acquired data for navigation
            if len(all_spectra) > 0 and wavelength_array is not None:
                self.data_acquired.emit(all_spectra, wavelength_array)
            
            # Save all scans to single CSV file at the end
            if self.params['save_enabled'] and all_spectra and wavelength_array is not None:
                self.save_sequence_to_csv(wavelength_array, all_spectra, sequence_start_time)
                
            self.status_update.emit(f"Completed {scan_num} scan(s)")
            
            # Emit final spectrum directly to ensure last scan is displayed
            if wavelength_array is not None and len(all_spectra) > 0:
                self.spectrum_ready.emit(wavelength_array, all_spectra[-1], stats)
            
        except Exception as e:
            self.error.emit(f"Error during measurement: {str(e)}")
            import traceback
            traceback.print_exc()
        finally:
            self.finished.emit()
            
    def save_sequence_to_csv(self, wavelengths, all_spectra, start_time):
        """Save all scans from sequence to a single CSV file with metadata"""
        try:
            # Store timestamp for plot exports
            self.last_sequence_timestamp = start_time
            
            save_folder = self.params['save_folder']
            if not os.path.exists(save_folder):
                os.makedirs(save_folder)
            
            # Convert Unix timestamp to formatted string
            timestamp_str = time.strftime("%Y%m%d_%H%M%S", time.localtime(start_time))
            num_scans = len(all_spectra)
            csv_filename = f"{save_folder}/sequence_{timestamp_str}_{num_scans}scans.csv"
            
            with open(csv_filename, 'w') as f:
                # Write metadata as comments
                f.write("# ============================================================\n")
                f.write("# AvaSpec Scan Sequence Data\n")
                f.write("# ============================================================\n")
                f.write(f"# Date/Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))}\n")
                f.write(f"# Total Scans: {num_scans} ({num_scans-2} measurements + Average + Median)\n")
                f.write("#\n")
                f.write("# Acquisition Parameters:\n")
                f.write(f"#   Integration Time: {self.params['integration_time']} ms\n")
                f.write(f"#   Integration Delay (T2): {self.params['integration_delay']} µs\n")
                f.write(f"#   Number of Averages: {self.params['num_averages']}\n")
                f.write(f"#   Pixel Range: {self.params['start_pixel']} to {self.params['stop_pixel']}\n")
                f.write(f"#   Wavelength Range: {wavelengths[0]:.2f} to {wavelengths[-1]:.2f} nm\n")
                f.write(f"#   ADC Max: {self.adc_max}\n")
                f.write(f"#   Laser Output: {'Enabled' if self.params['laser_enabled'] else 'Disabled'}\n")
                if self.params['laser_enabled']:
                    f.write(f"#   Laser Delay (T1): {self.params['laser_delay']} µs\n")
                    f.write(f"#   Laser Width (T3): {self.params['laser_width']} µs\n")
                f.write("# ============================================================\n")
                f.write("#\n")
                
                # Write header row: Pixel, Wavelength(nm), Scan_1, Scan_2, ..., Average, Median
                header = "Pixel,Wavelength(nm)"
                num_regular_scans = num_scans - 2  # Last 2 are average and median
                for i in range(num_regular_scans):
                    header += f",Scan_{i+1}"
                header += ",Average,Median"
                f.write(header + "\n")
                
                # Write data rows
                num_pixels = len(wavelengths)
                for px_idx in range(num_pixels):
                    # Pixel number (based on start_pixel)
                    pixel_num = self.params['start_pixel'] + px_idx
                    row = f"{pixel_num},{wavelengths[px_idx]:.4f}"
                    
                    # Add intensity from each scan
                    for spectrum in all_spectra:
                        row += f",{spectrum[px_idx]:.2f}"
                    
                    f.write(row + "\n")
            
            self.status_update.emit(f"Saved sequence: {os.path.basename(csv_filename)}")
            
        except Exception as e:
            self.error.emit(f"Error saving sequence file: {str(e)}")


class LiveDisplayThread(QThread):
    """Thread for continuous live display without saving or processing"""
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
                self.error.emit(f"Failed to prepare live display. Error code: {ret}")
                return

            self.status_update.emit("Live display started")

            # Continuous acquisition loop
            while self.running:
                # Start single measurement (window_handle=0, nummeas=1)
                ret = avs_measure(self.handle, 0, 1)
                if ret != 0:
                    if self.running:
                        self.error.emit(f"Measurement error: {ret}")
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
                        self.status_update.emit(f"Live display error: {str(e)}")

            self.status_update.emit("Live display stopped")

        except Exception as e:
            self.error.emit(f"Live display error: {str(e)}")

        finally:
            try:
                avs_stop_measure(self.handle)
            except:
                pass


class AvaSpecGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.handle = None
        self.serial = None
        self.num_pixels = 0
        self.wavelengths = None
        self.measurement_thread = None
        self.live_display_thread = None
        self.live_display_active = False
        self.connected = False
        self.adc_max = 65535  # Default 16-bit ADC max, updated on spectrometer connection

        # Settings persistence
        self.settings = QSettings('RAYOPS', 'AvaSpecGUI')
        
        # Display queue for asynchronous plotting
        self.display_queue = Queue(maxsize=10)  # Buffer up to 10 spectra
        
        # Display update timer
        self.display_timer = QTimer()
        self.display_timer.timeout.connect(self.process_display_queue)
        self.display_timer.start(50)  # Check queue every 50ms (20 Hz display update)

        # USB disconnect detection timer (started only while connected)
        self._spectro_check_timer = QTimer()
        self._spectro_check_timer.timeout.connect(self._check_spectrometer_connection)
        
        self.initUI()
        self.load_settings()  # Load saved settings after UI is created
        
    def initUI(self):
        self.setWindowTitle('AvaSpec-Mini4096CL & SF6100 Driver Control - RAYOPS')
        self.setGeometry(100, 100, 1400, 900)

        # Add menu bar for simulation mode
        if SIMULATION_MODE:
            self.create_simulation_menu()

        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QHBoxLayout()
        central_widget.setLayout(main_layout)
        
        # Create tab widget for left panel
        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget, 1)
        
        # Right panel - Plot and stats (remains the same)
        right_panel = QVBoxLayout()
        main_layout.addLayout(right_panel, 2)
        
        # === TAB 1: SPECTROMETER CONTROL ===
        spectro_tab = QWidget()
        spectro_layout = QVBoxLayout()
        spectro_tab.setLayout(spectro_layout)
        self.tab_widget.addTab(spectro_tab, "Spectrometer Control")
        
        # Now use spectro_layout instead of left_panel for spectrometer controls
        left_panel = spectro_layout  # Alias for compatibility with existing code
        
        # === LEFT PANEL ===
        
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
        left_panel.addWidget(conn_group)
        
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
        left_panel.addWidget(acq_group)
        
        
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
        left_panel.addWidget(laser_note)
        
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
        left_panel.addWidget(file_group)
        
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
        left_panel.addWidget(postproc_group)
        
        
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

        left_panel.addLayout(btn_layout)

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

        left_panel.addLayout(live_layout)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        left_panel.addWidget(self.progress_bar)
        
        # Add stretch to push everything to top
        left_panel.addStretch()
        
        # === RAYOPS LOGO ===
        try:
            logo_label = QLabel()
            logo_path = resource_path(os.path.join("assets", "RAYOPS_logo.jpg"))
            logo_pixmap = QPixmap(logo_path)
            if not logo_pixmap.isNull():
                # Scale logo to fit nicely (max width 300px)
                scaled_logo = logo_pixmap.scaledToWidth(280, Qt.SmoothTransformation)
                logo_label.setPixmap(scaled_logo)
                logo_label.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
                logo_label.setStyleSheet("padding: 5px;")
                left_panel.addWidget(logo_label)
            else:
                print(f"⚠️ RAYOPS_logo.jpg not found at: {logo_path}")
        except Exception as e:
            print(f"⚠️ Could not load logo: {e}")       
        
        # === TAB 2: DRIVER CONTROL ===
        self.driver_tab = DriverControlTab(self.settings)
        self.driver_tab.status_update.connect(self.log_status)
        self.driver_tab.protection_activated.connect(self.on_protection_activated)
        self.tab_widget.addTab(self.driver_tab, "Driver Control")

        # === TAB 3: ARDUINO TRIGGER CONTROL ===
        self.arduino_tab = ArduinoTriggerTab(self.settings)
        self.arduino_tab.status_update.connect(self.log_status)
        # Auto-start: Arduino tab will arm spectrometer FIRST, then start triggers
        self.arduino_tab.auto_start_spectrometer.connect(self.start_measurement)
        # Disable live display when switching to external trigger mode
        self.arduino_tab.trigger_mode_changed.connect(self.on_trigger_mode_changed)
        # Keep timing info in sync when integration time changes
        self.integration_time.valueChanged.connect(self.arduino_tab.update_timing_display)
        self.tab_widget.addTab(self.arduino_tab, "Trigger Sync")

        # === TAB 4: USER GUIDE ===
        guide_tab = self.create_guide_tab()
        self.tab_widget.addTab(guide_tab, "Guide")

        # === RIGHT PANEL ===
        
        # Plot widget
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w')
        self.plot_widget.setLabel('left', 'Intensity', units='counts')
        self.plot_widget.setLabel('bottom', 'Wavelength', units='nm')
        self.plot_widget.setTitle('Spectrum')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        right_panel.addWidget(self.plot_widget, 3)

        # Create persistent plot curves for performance (reuse with setData)
        self.spectrum_curve = self.plot_widget.plot([], [], pen=pg.mkPen('b', width=2))
        # Additional curves for analysis view
        self.raw_curve = None
        self.calibrated_curve = None
        self.planck_curve = None
        self.plot_legend = None
        
        # === SCAN NAVIGATION CONTROLS ===
        nav_group = QGroupBox("Scan Navigation")
        nav_layout = QHBoxLayout()
        
        # Previous scan button
        self.prev_scan_btn = QPushButton("◄ Previous")
        self.prev_scan_btn.clicked.connect(self.show_previous_scan)
        self.prev_scan_btn.setEnabled(False)
        nav_layout.addWidget(self.prev_scan_btn)
        
        # Scan number input
        nav_layout.addWidget(QLabel("Scan #:"))
        self.scan_number_input = QSpinBox()
        self.scan_number_input.setMinimum(1)
        self.scan_number_input.setMaximum(1)
        self.scan_number_input.setValue(1)
        self.scan_number_input.valueChanged.connect(self.show_scan_by_number)
        self.scan_number_input.setEnabled(False)
        nav_layout.addWidget(self.scan_number_input)
        
        self.scan_count_label = QLabel("/ 0")
        nav_layout.addWidget(self.scan_count_label)
        
        # Next scan button
        self.next_scan_btn = QPushButton("Next ►")
        self.next_scan_btn.clicked.connect(self.show_next_scan)
        self.next_scan_btn.setEnabled(False)
        nav_layout.addWidget(self.next_scan_btn)
        
        nav_layout.addStretch()
        
        # Export plot button
        self.export_plot_btn = QPushButton("📸 Export Plot")
        self.export_plot_btn.clicked.connect(self.export_current_plot)
        self.export_plot_btn.setEnabled(False)
        self.export_plot_btn.setToolTip("Export current plot as PNG to data folder")
        nav_layout.addWidget(self.export_plot_btn)

        # Y-axis controls
        nav_layout.addWidget(QLabel("  |  Y-axis:"))

        self.y_auto_scale_cb = QCheckBox("Auto")
        self.y_auto_scale_cb.setChecked(True)
        self.y_auto_scale_cb.setToolTip("Auto-scale Y-axis to fit data")
        self.y_auto_scale_cb.toggled.connect(self.on_y_auto_scale_changed)
        nav_layout.addWidget(self.y_auto_scale_cb)

        nav_layout.addWidget(QLabel("Min:"))
        self.y_min_spin = QDoubleSpinBox()
        self.y_min_spin.setRange(0, 100)
        self.y_min_spin.setValue(0)
        self.y_min_spin.setDecimals(0)
        self.y_min_spin.setSuffix("%")
        self.y_min_spin.setEnabled(False)
        self.y_min_spin.setFixedWidth(70)
        self.y_min_spin.valueChanged.connect(self.apply_y_axis_limits)
        nav_layout.addWidget(self.y_min_spin)

        nav_layout.addWidget(QLabel("Max:"))
        self.y_max_spin = QDoubleSpinBox()
        self.y_max_spin.setRange(0, 100)
        self.y_max_spin.setValue(100)
        self.y_max_spin.setDecimals(0)
        self.y_max_spin.setSuffix("%")
        self.y_max_spin.setEnabled(False)
        self.y_max_spin.setFixedWidth(70)
        self.y_max_spin.valueChanged.connect(self.apply_y_axis_limits)
        nav_layout.addWidget(self.y_max_spin)

        nav_group.setLayout(nav_layout)
        right_panel.addWidget(nav_group)
        
        # Storage for all acquired spectra
        self.acquired_spectra = []
        self.acquired_wavelengths = None
        self.current_scan_index = 0
        self.last_sequence_timestamp = None  # Track sequence timestamp for exports
        
        # Statistics group
        stats_group = QGroupBox("Measurement Statistics")
        stats_layout = QGridLayout()
        
        self.stat_labels = {}
        stats = ['Max Intensity', 'Max Wavelength', 'Min Intensity', 
                 'Mean Intensity', 'Std Deviation', 'Saturation']
        
        for i, stat in enumerate(stats):
            label = QLabel(f"{stat}:")
            value = QLabel("--")
            value.setFont(QFont("Courier", 10))
            stats_layout.addWidget(label, i // 2, (i % 2) * 2)
            stats_layout.addWidget(value, i // 2, (i % 2) * 2 + 1)
            self.stat_labels[stat] = value
        
        stats_group.setLayout(stats_layout)
        right_panel.addWidget(stats_group)
        
        # Status log
        status_group = QGroupBox("Status Log")
        status_layout = QVBoxLayout()
        
        self.status_log = QTextEdit()
        self.status_log.setReadOnly(True)
        self.status_log.setMaximumHeight(150)
        status_layout.addWidget(self.status_log)
        
        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(self.status_log.clear)
        status_layout.addWidget(clear_btn)
        
        status_group.setLayout(status_layout)
        right_panel.addWidget(status_group)
        
        # Initial status
        self.log_status("Application started. Click 'Connect to Spectrometer' to begin.")
        
    def update_laser_controls(self):
        enabled = self.laser_enable.isChecked()
        self.laser_delay.setEnabled(enabled)
        self.laser_width.setEnabled(enabled)
        self.laser_delay_label.setEnabled(enabled)
        self.laser_width_label.setEnabled(enabled)
        
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
            from Spectro_diode.spectrum_analysis import (read_calibration_file, analyze_spectrum, KFactorCache)

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
            
    def show_previous_scan(self):
        """Show previous scan in the sequence"""
        if self.current_scan_index > 0:
            self.current_scan_index -= 1
            self.show_scan_by_index(self.current_scan_index)
            self.scan_number_input.setValue(self.current_scan_index + 1)
    
    def show_next_scan(self):
        """Show next scan in the sequence"""
        if self.current_scan_index < len(self.acquired_spectra) - 1:
            self.current_scan_index += 1
            self.show_scan_by_index(self.current_scan_index)
            self.scan_number_input.setValue(self.current_scan_index + 1)
    
    def show_scan_by_number(self, scan_number):
        """Show specific scan by number (1-indexed)"""
        if len(self.acquired_spectra) > 0:
            index = scan_number - 1
            if 0 <= index < len(self.acquired_spectra):
                self.current_scan_index = index
                self.show_scan_by_index(index)
    
    def show_scan_by_index(self, index):
        """Display a specific scan by its index"""
        if 0 <= index < len(self.acquired_spectra):
            # Check if we have analysis for this scan
            if hasattr(self, 'analysis_results') and index < len(self.analysis_results):
                self.show_scan_with_analysis(index)
            else:
                # Normal display without analysis
                spectrum = self.acquired_spectra[index]
                
                # Determine scan type
                total_scans = len(self.acquired_spectra)
                if index == total_scans - 2:
                    scan_type = "Average"
                elif index == total_scans - 1:
                    scan_type = "Median"
                else:
                    scan_type = "Normal"
                
                # Calculate stats for this scan
                stats = {
                    'max': np.max(spectrum),
                    'min': np.min(spectrum),
                    'mean': np.mean(spectrum),
                    'std': np.std(spectrum),
                    'max_wavelength': self.acquired_wavelengths[np.argmax(spectrum)],
                    'saturation': (np.max(spectrum) / 65535.0) * 100,
                    'scan_number': index + 1,
                    'scan_type': scan_type,
                    'timestamp': 0
                }
                
                # Update display
                self.display_spectrum(self.acquired_wavelengths, spectrum, stats)
                
            # Update navigation buttons
            self.prev_scan_btn.setEnabled(index > 0)
            self.next_scan_btn.setEnabled(index < len(self.acquired_spectra) - 1)
    
    def show_scan_with_analysis(self, index):
        """Display scan with calibrated spectrum and Planck fit overlay"""
        if index >= len(self.analysis_results):
            return

        result = self.analysis_results[index]
        raw = result['raw']
        calibrated = result['calibrated']
        planck = result['planck_model']

        # Hide main spectrum curve (used for non-analysis display)
        self.spectrum_curve.setData([], [])

        # Remove old legend before creating new one (prevents accumulation)
        if self.plot_legend is not None:
            self.plot_widget.removeItem(self.plot_legend)
            self.plot_legend = None

        # Create/reuse analysis curves
        if self.raw_curve is None:
            self.raw_curve = self.plot_widget.plot([], [], pen=pg.mkPen('k', width=1.5), name='Raw (counts)')
        if self.calibrated_curve is None:
            self.calibrated_curve = self.plot_widget.plot([], [], pen=pg.mkPen('b', width=2), name='Calibrated (counts × K)')
        if self.planck_curve is None:
            self.planck_curve = self.plot_widget.plot([], [], pen=pg.mkPen('r', width=2, style=Qt.DashLine), name='Planck fit')

        # Update curve data using setData (fast)
        self.raw_curve.setData(self.acquired_wavelengths, raw)
        self.calibrated_curve.setData(self.acquired_wavelengths, calibrated)

        # Update Planck fit curve if available
        if result['fit_ok'] and planck is not None:
            fit_min, fit_max = result['fit_range']
            mask = (self.acquired_wavelengths >= fit_min) & (self.acquired_wavelengths <= fit_max)
            self.planck_curve.setData(self.acquired_wavelengths[mask], planck[mask])
        else:
            self.planck_curve.setData([], [])

        # Update title with analysis info
        total_scans = len(self.acquired_spectra)
        title = f"Spectrum - Scan #{index + 1}"

        # Add scan type (Average/Median) if applicable
        if index == total_scans - 2:
            title += " (Average)"
        elif index == total_scans - 1:
            title += " (Median)"

        # Add temperature and species
        if result['fit_ok']:
            title += f" | T ≈ {result['temperature_K']:.0f} K"
            if result['species']:
                title += f" | Species: {', '.join(result['species'])}"
        else:
            title += " | No Planck fit"

        self.plot_widget.setTitle(title)

        # Add legend (store reference to remove later)
        self.plot_legend = self.plot_widget.addLegend(offset=(10, 10))
        
        # Update stats from calibrated spectrum
        stats = {
            'max': np.max(calibrated),
            'min': np.min(calibrated),
            'mean': np.mean(calibrated),
            'std': np.std(calibrated),
            'max_wavelength': self.acquired_wavelengths[np.argmax(calibrated)],
            'saturation': (np.max(raw) / 65535.0) * 100,  # Saturation from raw
            'scan_number': index + 1,
            'scan_type': 'Analyzed',
            'timestamp': 0
        }
        
        # Update statistics display
        self.stat_labels['Max Intensity'].setText(f"{stats['max']:.2f}")
        self.stat_labels['Max Wavelength'].setText(f"{stats['max_wavelength']:.2f} nm")
        self.stat_labels['Min Intensity'].setText(f"{stats['min']:.2f}")
        self.stat_labels['Mean Intensity'].setText(f"{stats['mean']:.2f}")
        self.stat_labels['Std Deviation'].setText(f"{stats['std']:.2f}")
        self.stat_labels['Saturation'].setText(f"{stats['saturation']:.1f}%")

        # Apply Y-axis limits
        self.apply_y_axis_limits()


    def export_current_plot(self):
        """Export current plot to PNG in data folder"""
        if not hasattr(self, 'acquired_spectra') or len(self.acquired_spectra) == 0:
            QMessageBox.warning(self, "No Data", "No data to export!")
            return
        
        try:
            # Determine save folder
            save_folder = self.save_path.text() if self.save_path.text() else "./data"
            if not os.path.exists(save_folder):
                os.makedirs(save_folder)
            
            # Generate filename based on sequence and scan
            if self.last_sequence_timestamp:
                timestamp_str = time.strftime("%Y%m%d_%H%M%S", time.localtime(self.last_sequence_timestamp))
            else:
                timestamp_str = time.strftime("%Y%m%d_%H%M%S")
            
            # Determine scan info
            scan_num = self.current_scan_index + 1
            total_scans = len(self.acquired_spectra)
            
            # Check if it's Average or Median
            if scan_num == total_scans - 1:
                scan_label = "Average"
            elif scan_num == total_scans:
                scan_label = "Median"
            else:
                scan_label = f"scan{scan_num:03d}"
            
            # Build filename
            filename = f"{save_folder}/plot_{timestamp_str}_{scan_label}.png"
            
            # Export using pyqtgraph's export functionality
            exporter = ImageExporter(self.plot_widget.plotItem)
            exporter.parameters()['width'] = 1920  # HD resolution
            exporter.export(filename)
            
            self.log_status(f"✓ Plot exported: {os.path.basename(filename)}")
            
        except Exception as e:
            self.log_status(f"❌ Export error: {str(e)}")
            QMessageBox.critical(self, "Export Error", f"Failed to export plot:\n{str(e)}")
    
    def log_status(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.status_log.append(f"[{timestamp}] {message}")
        # Auto-scroll to bottom
        self.status_log.verticalScrollBar().setValue(
            self.status_log.verticalScrollBar().maximum()
        )
        
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
            
    def update_laser_delay_cycles(self):
        """Update laser delay clock cycles display"""
        us_value = self.laser_delay.value()
        cycles = us_to_clock_cycles(us_value)
        actual_us = clock_cycles_to_us(cycles)
        self.laser_delay_cycles.setText(f"({cycles} cycles = {actual_us:.2f} µs)")
    
    def update_integration_delay_cycles(self):
        """Update integration delay clock cycles display"""
        us_value = self.integration_delay.value()
        cycles = us_to_clock_cycles(us_value)
        actual_us = clock_cycles_to_us(cycles)
        self.integration_delay_cycles.setText(f"({cycles} cycles = {actual_us:.2f} µs)")
    
    def update_laser_width_cycles(self):
        """Update laser width clock cycles display"""
        us_value = self.laser_width.value()
        cycles = us_to_clock_cycles(us_value)
        actual_us = clock_cycles_to_us(cycles)
        self.laser_width_cycles.setText(f"({cycles} cycles = {actual_us:.2f} µs)")
      
    def process_display_queue(self):
        """Process display queue - update plot without blocking acquisition"""
        if self.display_queue.empty():
            return
        
        try:
            # Get latest spectrum (skip old ones if queue is backed up)
            wavelengths, spectrum, stats = None, None, None
            
            # Drain queue but keep only the latest
            while not self.display_queue.empty():
                wavelengths, spectrum, stats = self.display_queue.get_nowait()
            
            # Update display with latest data
            if wavelengths is not None:
                self.display_spectrum(wavelengths, spectrum, stats)
                
        except Exception as e:
            print(f"Display queue error: {e}")
            
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
        self.measurement_thread = MeasurementThread()
        self.measurement_thread.set_parameters(self.handle, self.num_pixels,
                                              self.wavelengths, params,
                                              self.display_queue,
                                              adc_max=self.adc_max)
        self.measurement_thread.progress.connect(self.update_progress)
        self.measurement_thread.spectrum_ready.connect(self.display_spectrum)
        self.measurement_thread.status_update.connect(self.log_status)
        self.measurement_thread.error.connect(self.handle_error)
        self.measurement_thread.finished.connect(self.measurement_finished)
        self.measurement_thread.data_acquired.connect(self.store_acquired_data)  # Store for navigation
        self.measurement_thread.start()
        
    def store_acquired_data(self, all_spectra, wavelength_array):
        """Store acquired data for scan navigation"""
        self.acquired_spectra = all_spectra
        self.acquired_wavelengths = wavelength_array
        self.current_scan_index = len(all_spectra) - 1  # Start at last scan

        # Clear any previous analysis results so post-processing uses fresh data
        if hasattr(self, 'analysis_results'):
            delattr(self, 'analysis_results')
        
    def stop_measurement(self):
        if self.measurement_thread:
            self.log_status("Stopping measurement...")
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
            self.log_status("Error: Spectrometer not connected")
            return

        # Check if measurement is running
        if self.measurement_thread and self.measurement_thread.isRunning():
            self.log_status("Cannot start live display while measurement is running")
            return

        # Check if in external trigger mode (disabled for live display)
        if hasattr(self, 'arduino_tab') and self.arduino_tab.is_external_mode():
            self.log_status("Live display not available in external trigger mode")
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
        self.live_display_thread.status_update.connect(self.log_status)
        self.live_display_thread.error.connect(self.log_status)
        self.live_display_thread.finished.connect(self.on_live_display_finished)

        self.live_display_thread.start()
        self.log_status(f"Live display started (integration: {self.integration_time.value():.2f} ms)")

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
            self.log_status("Live display stopped - external trigger mode selected")

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
                    self.log_status(f"⚠️ Stopping Arduino triggers due to protection: {', '.join(protections)}")
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
        self.log_status(f"ERROR: {error_msg}")
        QMessageBox.critical(self, "Measurement Error",
                             f"An error occurred during measurement:\n\n{error_msg}\n\n"
                             "Data may not have been saved correctly.")
        
    def measurement_finished(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(100)
        self.log_status("Measurement sequence complete")
        
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
                    self.log_status("Auto post-processing enabled - starting analysis...")
                    self.run_postprocessing()
                else:
                    self.log_status("⚠️ Auto post-processing enabled but no calibration file set")
        
    def disconnect_spectrometer(self):
        """Properly disconnect from spectrometer"""
        self._spectro_check_timer.stop()
        if self.connected and self.handle:
            try:
                self.log_status("Disconnecting from spectrometer...")
                AVS_Deactivate(self.handle)
                AVS_Done()
                self.connected = False
                self.handle = None
                self.log_status("Spectrometer disconnected successfully")
            except Exception as e:
                self.log_status(f"Error during disconnect: {str(e)}")

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

        self.log_status("ERROR: Spectrometer USB disconnected! Reconnect the cable and press Connect.")

    def nativeEvent(self, eventType, message):
        """Catch Windows WM_DEVICECHANGE to detect USB removal instantly.

        Qt broadcasts WM_DEVICECHANGE to all top-level windows without any
        registration. DBT_DEVICEREMOVECOMPLETE (0x8004) fires the moment the
        OS processes the physical disconnect — no polling delay.
        """
        if eventType == b'windows_generic_MSG':
            import ctypes, ctypes.wintypes
            WM_DEVICECHANGE = 0x0219
            DBT_DEVICEREMOVECOMPLETE = 0x8004
            try:
                msg = ctypes.cast(int(message),
                                  ctypes.POINTER(ctypes.wintypes.MSG)).contents
                if msg.message == WM_DEVICECHANGE and msg.wParam == DBT_DEVICEREMOVECOMPLETE:
                    sys.stderr.write("[DEVICECHANGE] DBT_DEVICEREMOVECOMPLETE received\n")
                    sys.stderr.flush()
                    if self.connected and not getattr(self, '_disconnect_in_progress', False):
                        self._disconnect_in_progress = True
                        self._spectro_check_timer.stop()
                        self._on_spectrometer_lost()
            except Exception as e:
                sys.stderr.write(f"[DEVICECHANGE] error: {e}\n")
                sys.stderr.flush()
        return False, 0

    def create_guide_tab(self):
        """Create the user guide tab with operation instructions"""
        from PySide6.QtWidgets import QTextBrowser, QPushButton, QHBoxLayout
        from PySide6.QtCore import Qt

        guide_widget = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        guide_widget.setLayout(layout)

        # Font size control bar
        font_control_layout = QHBoxLayout()
        font_label = QLabel("Font Size:")
        self.guide_font_size = 15  # Default font size

        decrease_btn = QPushButton("-")
        decrease_btn.setFixedSize(30, 30)
        decrease_btn.setStyleSheet("font-size: 16px; font-weight: bold;")
        decrease_btn.clicked.connect(lambda: self.change_guide_font_size(-2))

        increase_btn = QPushButton("+")
        increase_btn.setFixedSize(30, 30)
        increase_btn.setStyleSheet("font-size: 16px; font-weight: bold;")
        increase_btn.clicked.connect(lambda: self.change_guide_font_size(2))

        self.font_size_label = QLabel(f"{self.guide_font_size}px")
        self.font_size_label.setMinimumWidth(40)

        font_control_layout.addWidget(font_label)
        font_control_layout.addWidget(decrease_btn)
        font_control_layout.addWidget(self.font_size_label)
        font_control_layout.addWidget(increase_btn)
        font_control_layout.addStretch()

        layout.addLayout(font_control_layout)

        # Use QTextBrowser for rich text with scrolling
        self.guide_browser = QTextBrowser()
        self.guide_browser.setOpenExternalLinks(True)
        self.guide_browser.setMinimumHeight(400)
        self.update_guide_stylesheet()

        self.guide_html_template = """
        <html>
        <head>
        <style>
            body { font-family: Arial, sans-serif; font-size: 15px; line-height: 1.6; color: #333; }
            h1 { color: #1565C0; font-size: 24px; border-bottom: 2px solid #1565C0; padding-bottom: 8px; }
            h2 { color: #2E7D32; font-size: 20px; margin-top: 25px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }
            h3 { color: #E65100; font-size: 17px; margin-top: 20px; }
            p { margin: 10px 0; font-size: 15px; }
            ul, ol { margin: 10px 0 10px 20px; font-size: 15px; }
            li { margin: 8px 0; }
            .info-box { background: #E3F2FD; border-left: 4px solid #1976D2; padding: 12px; margin: 15px 0; font-size: 15px; }
            .warning-box { background: #FFF3E0; border-left: 4px solid #FF9800; padding: 12px; margin: 15px 0; font-size: 15px; }
            .success { color: #2E7D32; }
            .danger { color: #C62828; }
            code { background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 14px; }
            hr { border: none; border-top: 1px solid #ddd; margin: 25px 0; }
        </style>
        </head>
        <body>

        <h1>AvaSpec SF6100 Control - User Guide</h1>

        <div class="info-box">
        <b>Overview:</b> This application has <b>three main tabs</b> for controlling the spectroscopy system:
        Spectrometer Control, Driver Control, and Trigger Sync. Post-processing features include calibration,
        Planck temperature fitting, and atomic line detection.
        </div>

        <hr>

        <h2>1. Spectrometer Control Tab</h2>

        <h3>Connection</h3>
        <ul>
            <li>Click <b>"Connect to Spectrometer"</b> to detect and connect to the AvaSpec device</li>
            <li>Wait 2-3 seconds for connection confirmation</li>
            <li>Status shows: Serial number, Firmware, Pixels, Wavelength range</li>
        </ul>

        <h3>Acquisition Parameters</h3>
        <ul>
            <li><b>Integration Time (ms):</b> Light collection duration per scan (1 - 10,000 ms)
                <ul>
                    <li>Bright samples: 1-10 ms</li>
                    <li>Dim samples: 50-500 ms</li>
                    <li>Weak signals: 500-5000 ms</li>
                </ul>
            </li>
            <li><b>Start/Stop Pixel:</b> Define spectral range (0-4093)</li>
            <li><b>Number of Averages:</b> Spectra averaged per scan (1-100). Higher = better SNR but slower</li>
            <li><b>Number of Scans:</b> Total measurements in sequence (1-10,000)</li>
        </ul>

        <h3>Trigger Mode</h3>
        <ul>
            <li><b>Software:</b> Immediate acquisition, no external trigger needed</li>
            <li><b>Hardware:</b> Waits for external TTL trigger signal</li>
            <li><b>Sync:</b> Combined hardware + software trigger mode</li>
        </ul>

        <div class="warning-box">
        <b>Important:</b> Integration time must be shorter than trigger period in external mode!
        </div>

        <h3>Running Measurements</h3>
        <ul>
            <li>Click <b>"Start Measurement"</b> to begin acquisition</li>
            <li>Progress bar shows completion percentage</li>
            <li>Use <b>Previous/Next</b> buttons to navigate through scans</li>
            <li>Average and Median spectra are automatically calculated</li>
        </ul>

        <h3>Live Display Mode (Alignment)</h3>
        <div class="info-box">
        <b>Purpose:</b> Continuous real-time spectrum display for optical alignment without saving data.
        </div>
        <ul>
            <li>Click <b>"Start Live Display"</b> (purple button) to begin</li>
            <li>Spectrum updates continuously in real-time</li>
            <li>Uses the same integration time as measurement mode</li>
            <li>No data saving, no post-processing</li>
            <li><b>Only available in Software trigger mode</b> (disabled in External mode)</li>
            <li>Click <b>"Stop Live Display"</b> to end</li>
        </ul>

        <h3>Post-Processing Analysis</h3>
        <ul>
            <li>Select a calibration file (CSV with Wavelength and K_lambda columns)</li>
            <li>Click <b>"Analyze Spectra"</b> to run analysis</li>
            <li>Analysis includes:
                <ul>
                    <li>Apply calibration factors (counts × K)</li>
                    <li>Fit Planck blackbody curve (550-750 nm range)</li>
                    <li>Estimate temperature in Kelvin</li>
                    <li>Detect atomic emission lines (Na, Li, K, Rb)</li>
                </ul>
            </li>
            <li>Plot shows: <span style="color:black;">■ Raw</span>, <span style="color:blue;">■ Calibrated</span>, <span style="color:red;">--- Planck fit</span></li>
        </ul>

        <hr>

        <h2>2. Driver Control Tab</h2>

        <h3>Connection</h3>
        <ul>
            <li>Select the SF6100 COM port (Silicon Labs CP210x)</li>
            <li>Click <b>"Connect to Driver"</b></li>
            <li>Parameters are automatically applied on connection</li>
        </ul>

        <h3>Enable Mode</h3>
        <ul>
            <li><b>External Enable:</b> Hardware pin controls output (for Arduino sync)
                <ul>
                    <li>START OUTPUT button disabled (grayed out)</li>
                    <li>Output controlled by Arduino triggers only</li>
                    <li>Driver status shows STARTED/STOPPED synced with triggers</li>
                    <li><b>QCW mode automatically disabled</b> - only CW available</li>
                </ul>
            </li>
            <li><b>Internal Enable:</b> Software controls output via START/STOP buttons
                <ul>
                    <li>Both CW and QCW modes available</li>
                    <li>Cannot start Arduino triggers while driver is running internally</li>
                </ul>
            </li>
        </ul>

        <h3>Output Mode</h3>
        <ul>
            <li><b>CW (Continuous):</b> Laser runs continuously when enabled</li>
            <li><b>QCW (Pulsed):</b> Laser pulses at specified frequency and duration
                <ul>
                    <li>Frequency: 0.1 - 500.0 Hz</li>
                    <li>Pulse Duration: Limited to 1/frequency (period)</li>
                    <li>Example: At 100 Hz, max duration = 10 ms</li>
                </ul>
            </li>
        </ul>

        <div class="warning-box">
        <b>Duty Cycle Warning:</b> Keep duty cycle < 50% to prevent overheating.<br>
        Formula: Duty = (Duration × Frequency) / 1000
        </div>

        <h3>Current Control</h3>
        <ul>
            <li>Range: 0.00 - 20.00 A</li>
            <li><b>Always start with low current (0.1-1.0 A) for testing!</b></li>
            <li>Parameters are <b>automatically sent</b> when clicking START OUTPUT</li>
            <li>In QCW mode, frequency and duration are also sent automatically</li>
            <li><b>Parameters are locked</b> while laser is running - stop output to change</li>
        </ul>

        <h3>Protection Status</h3>
        <ul>
            <li><span class="success">● OK</span> = Normal operation</li>
            <li><span class="danger">● ACTIVE/FAULT</span> = Protection engaged</li>
            <li><b>DISCONNECTED</b> (gray) = Driver not connected</li>
        </ul>
        <p>Monitors: Interlock, Overcurrent, Overheat, Crowbar, NTC Interlock</p>

        <div class="warning-box">
        <b>Protection Safety Features:</b>
        <ul style="margin:5px 0;">
            <li>If ANY protection activates: <b>Laser and Arduino triggers stop automatically</b></li>
            <li>Cannot start laser or triggers while protection is active</li>
            <li>NTC Interlock: Only triggers auto-stop when "Allowed" (not when "Denied")</li>
        </ul>
        </div>

        <h3>NTC Temperature Control</h3>
        <ul>
            <li><b>Allow/Deny NTC:</b> Enable or disable NTC-based protection</li>
            <li><b>Lower Limit:</b> Typically 10-20°C</li>
            <li><b>Upper Limit:</b> Typically 35-50°C</li>
            <li>Settings are remembered between sessions</li>
        </ul>

        <hr>

        <h2>3. Trigger Sync Tab (Arduino)</h2>

        <div class="info-box">
        <b>Purpose:</b> Synchronize spectrometer and laser triggers for time-resolved measurements using Arduino hardware timing.
        </div>

        <h3>Hardware Setup</h3>
        <ul>
            <li>Arduino Uno/Nano with trigger firmware</li>
            <li><b>Pin 9</b> → Spectrometer Trigger Input</li>
            <li><b>Pin 10</b> → Laser Driver External Enable Pin</li>
        </ul>

        <h3>Trigger Parameters</h3>
        <ul>
            <li><b>Frequency:</b> Trigger rate (1-1000 Hz)</li>
            <li><b>Number of Triggers:</b> Total count (0 = continuous)</li>
            <li><b>Laser Trigger Width:</b> Laser enable pulse width as % of period</li>
            <li><b>Laser Delay (Offset):</b>
                <ul>
                    <li><b>Negative:</b> Laser fires BEFORE spectrometer (for plasma formation)</li>
                    <li><b>Zero:</b> Simultaneous triggers</li>
                    <li><b>Positive:</b> Laser fires AFTER spectrometer starts</li>
                </ul>
            </li>
        </ul>

        <h3>Operation Sequence</h3>
        <ol>
            <li>Connect Arduino and set <b>"External Trigger"</b> mode</li>
            <li>In Driver Control: Set <b>External Enable</b> + <b>CW mode</b></li>
            <li>In Spectrometer Control: Set trigger mode to <b>Hardware</b> or <b>Sync</b></li>
            <li>Configure trigger parameters</li>
            <li>Click <b>"START TRIGGERS"</b> (parameters are applied automatically)</li>
            <li>If auto-start enabled, spectrometer measurement begins automatically</li>
        </ol>

        <hr>

        <h2>4. Common Workflows</h2>

        <h3>Quick Single Spectrum (Software Mode)</h3>
        <ol>
            <li>Connect spectrometer</li>
            <li>Set Integration Time: 10 ms, Scans: 1</li>
            <li>Click "Start Measurement"</li>
        </ol>

        <h3>Synchronized LIBS Measurement</h3>
        <ol>
            <li><b>Tab 3:</b> Connect Arduino, External mode, Freq: 10 Hz, Delay: -1000 µs</li>
            <li><b>Tab 2:</b> External Enable, CW mode, Set current</li>
            <li><b>Tab 1:</b> Integration: 5 ms, Hardware trigger, Scans: 100</li>
            <li><b>Tab 3:</b> START TRIGGERS (parameters applied automatically)</li>
        </ol>

        <hr>

        <h2>5. Safety & Best Practices</h2>

        <div class="warning-box">
        <ul style="margin:0;">
            <li>Always verify all protection statuses are <span class="success">OK</span> before operation</li>
            <li>Start with low laser current (0.1-1.0 A) for testing</li>
            <li>Keep spectrum saturation below 90%</li>
            <li>Monitor temperatures continuously during operation</li>
            <li>Stop immediately if any protection triggers</li>
        </ul>
        </div>

        <h3>Safe Shutdown</h3>
        <ul>
            <li>When closing the application, all running operations are <b>automatically stopped</b></li>
            <li>A warning dialog shows if laser, measurement, or triggers are running</li>
            <li>Shutdown sequence: Measurement → Live Display → Triggers → Laser Output</li>
            <li>Settings are saved before closing</li>
        </ul>

        <hr>

        <h2>6. Troubleshooting</h2>

        <h3>Spectrometer won't connect</h3>
        <ul>
            <li>Check USB-over-Ethernet is powered</li>
            <li>Verify Ethernet link LED is lit</li>
            <li>Try disconnecting/reconnecting USB</li>
        </ul>

        <h3>External triggers not working</h3>
        <ul>
            <li>Verify "External Trigger" mode selected in Tab 3</li>
            <li>Check "START TRIGGERS" was clicked</li>
            <li>Ensure Driver is in "External Enable" + "CW" mode</li>
            <li>Verify trigger frequency allows time for integration</li>
        </ul>

        <h3>"No Planck fit" in analysis</h3>
        <ul>
            <li>Signal may be too weak - increase integration time</li>
            <li>Check spectrum covers 550-750 nm fitting range</li>
            <li>Verify calibration file wavelengths match spectrometer</li>
        </ul>

        <hr>

        <p style="color: #666; font-size: 13px; margin-top: 30px;">
        <b>For complete documentation</b>, see the <code>docs/Software/GUI_OPERATION_GUIDE.md</code> file included with this application.
        </p>

        </body>
        </html>
        """

        self.update_guide_html()
        layout.addWidget(self.guide_browser)

        # Add logo at bottom left (consistent with other tabs)
        logo_label = QLabel()
        logo_path = resource_path(os.path.join("assets", "RAYOPS_logo.jpg"))
        logo_pixmap = QPixmap(logo_path)
        if not logo_pixmap.isNull():
            scaled_logo = logo_pixmap.scaledToWidth(280, Qt.SmoothTransformation)
            logo_label.setPixmap(scaled_logo)
            logo_label.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
            logo_label.setStyleSheet("padding: 5px;")
            layout.addWidget(logo_label)

        return guide_widget

    def update_guide_stylesheet(self):
        """Update the guide browser stylesheet with current font size"""
        self.guide_browser.setStyleSheet(f"""
            QTextBrowser {{
                background-color: #ffffff;
                border: 1px solid #ccc;
                padding: 15px;
                font-family: Arial, sans-serif;
                font-size: {self.guide_font_size}px;
                line-height: 1.5;
            }}
        """)

    def update_guide_html(self):
        """Update the guide HTML with current font size"""
        # Scale other sizes proportionally based on base font size
        h1_size = int(self.guide_font_size * 1.6)
        h2_size = int(self.guide_font_size * 1.33)
        h3_size = int(self.guide_font_size * 1.13)
        code_size = int(self.guide_font_size * 0.93)

        html = self.guide_html_template.replace(
            "font-size: 15px", f"font-size: {self.guide_font_size}px"
        ).replace(
            "font-size: 24px", f"font-size: {h1_size}px"
        ).replace(
            "font-size: 20px", f"font-size: {h2_size}px"
        ).replace(
            "font-size: 17px", f"font-size: {h3_size}px"
        ).replace(
            "font-size: 14px", f"font-size: {code_size}px"
        ).replace(
            "font-size: 13px", f"font-size: {code_size}px"
        )
        self.guide_browser.setHtml(html)

    def change_guide_font_size(self, delta):
        """Change the guide font size by delta pixels"""
        new_size = self.guide_font_size + delta
        # Limit font size between 10 and 30
        if 10 <= new_size <= 30:
            self.guide_font_size = new_size
            self.font_size_label.setText(f"{self.guide_font_size}px")
            self.update_guide_stylesheet()
            self.update_guide_html()

    def closeEvent(self, event):
        """Handle window close event - ensure clean shutdown"""
        # Check what is currently running
        running_items = []

        # Check if measurement is running
        measurement_running = self.measurement_thread and self.measurement_thread.isRunning()
        if measurement_running:
            running_items.append("Spectrometer measurement")

        # Check if live display is running
        live_display_running = self.live_display_active
        if live_display_running:
            running_items.append("Live display")

        # Check if laser output is running
        laser_running = False
        if hasattr(self, 'driver_tab') and hasattr(self.driver_tab, 'current_state'):
            if self.driver_tab.current_state and self.driver_tab.current_state.get('started', False):
                laser_running = True
                running_items.append("Laser output (SAFETY)")

        # Check if Arduino triggers are running
        triggers_running = False
        if hasattr(self, 'arduino_tab') and hasattr(self.arduino_tab, 'trigger_status_text'):
            if self.arduino_tab.trigger_status_text.text() == "RUNNING":
                triggers_running = True
                running_items.append("Arduino triggers")

        # Show warning if anything is running
        if running_items:
            running_list = "\n• ".join(running_items)
            reply = QMessageBox.question(
                self,
                'Active Operations',
                f'The following are currently running:\n• {running_list}\n\n'
                'All operations will be stopped before closing.\n\n'
                'Stop everything and exit?',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply == QMessageBox.No:
                event.ignore()
                return

        # Stop all running operations in order: Measurement -> Triggers -> Laser

        # 1. Stop measurement first
        if measurement_running:
            self.log_status("Stopping measurement for shutdown...")
            self.measurement_thread.stop()
            # Wait longer for thread to stop (5 seconds)
            if not self.measurement_thread.wait(5000):
                self.log_status("Warning: Measurement thread did not stop gracefully")
                # Thread didn't stop - force terminate as last resort
                try:
                    self.measurement_thread.terminate()
                    self.measurement_thread.wait(1000)
                except Exception:
                    pass

        # 1b. Stop live display if running
        if live_display_running:
            self.log_status("Stopping live display for shutdown...")
            self.stop_live_display()

        # 2. Stop Arduino triggers
        if triggers_running and hasattr(self, 'arduino_tab'):
            try:
                self.log_status("Stopping Arduino triggers for shutdown...")
                if self.arduino_tab.connected and self.arduino_tab.arduino:
                    self.arduino_tab.arduino.stop()
            except Exception as e:
                self.log_status(f"Warning: Could not stop triggers: {e}")

        # 3. Stop laser output (CRITICAL SAFETY)
        if laser_running and hasattr(self, 'driver_tab'):
            try:
                self.log_status("Stopping laser output for shutdown...")
                self.driver_tab.stop_output()
            except Exception as e:
                self.log_status(f"Warning: Could not stop laser output: {e}")

        # Save settings before closing (with error handling)
        try:
            self.save_settings()
        except Exception as e:
            self.log_status(f"Warning: Could not save settings: {e}")

        # Disconnect Arduino first (stop triggers before laser operations)
        if hasattr(self, 'arduino_tab'):
            try:
                self.arduino_tab.cleanup()
            except Exception as e:
                self.log_status(f"Warning: Arduino cleanup error: {e}")

        # Disconnect driver
        if hasattr(self, 'driver_tab'):
            try:
                self.driver_tab.cleanup()
            except Exception as e:
                self.log_status(f"Warning: Driver cleanup error: {e}")

        # Disconnect spectrometer last
        try:
            self.disconnect_spectrometer()
        except Exception as e:
            self.log_status(f"Warning: Spectrometer disconnect error: {e}")

        # Accept the close event
        self.log_status("Application closing...")
        event.accept()

    def create_simulation_menu(self):
        """Create debug menu for simulation mode testing"""
        from PySide6.QtGui import QAction

        menubar = self.menuBar()

        # Debug menu (only in simulation mode)
        debug_menu = menubar.addMenu('Debug (Simulation)')

        # Simulate driver disconnect
        driver_disconnect_action = QAction('Simulate Driver USB Disconnect', self)
        driver_disconnect_action.setShortcut('Ctrl+Shift+D')
        driver_disconnect_action.triggered.connect(self.simulate_driver_disconnect)
        debug_menu.addAction(driver_disconnect_action)

        # Simulate Arduino disconnect
        arduino_disconnect_action = QAction('Simulate Arduino USB Disconnect', self)
        arduino_disconnect_action.setShortcut('Ctrl+Shift+A')
        arduino_disconnect_action.triggered.connect(self.simulate_arduino_disconnect)
        debug_menu.addAction(arduino_disconnect_action)

        debug_menu.addSeparator()

        # Protection status simulation submenu
        protection_menu = debug_menu.addMenu('Toggle Protection Status')

        interlock_action = QAction('Toggle Interlock', self)
        interlock_action.triggered.connect(self.simulate_toggle_interlock)
        protection_menu.addAction(interlock_action)

        crowbar_action = QAction('Toggle Crowbar', self)
        crowbar_action.triggered.connect(self.simulate_toggle_crowbar)
        protection_menu.addAction(crowbar_action)

        overcurrent_action = QAction('Toggle Overcurrent', self)
        overcurrent_action.triggered.connect(self.simulate_toggle_overcurrent)
        protection_menu.addAction(overcurrent_action)

        overheat_action = QAction('Toggle Overheat', self)
        overheat_action.triggered.connect(self.simulate_toggle_overheat)
        protection_menu.addAction(overheat_action)

        ntc_interlock_action = QAction('Toggle NTC Interlock', self)
        ntc_interlock_action.triggered.connect(self.simulate_toggle_ntc_interlock)
        protection_menu.addAction(ntc_interlock_action)

        debug_menu.addSeparator()

        # Info action
        info_action = QAction('About Simulation Mode...', self)
        info_action.triggered.connect(self.show_simulation_info)
        debug_menu.addAction(info_action)

    def simulate_driver_disconnect(self):
        """Simulate driver USB cable being unplugged"""
        from internal.Spectro.hardware_simulator import simulate_driver_disconnect
        if simulate_driver_disconnect():
            self.log_status("DEBUG: Simulated driver USB disconnect")
        else:
            self.log_status("DEBUG: Driver not connected - connect first to test disconnect")

    def simulate_arduino_disconnect(self):
        """Simulate Arduino USB cable being unplugged"""
        from internal.Spectro.hardware_simulator import simulate_arduino_disconnect
        if simulate_arduino_disconnect():
            self.log_status("DEBUG: Simulated Arduino USB disconnect")
        else:
            self.log_status("DEBUG: Arduino not connected - connect first to test disconnect")

    def simulate_toggle_interlock(self):
        """Toggle interlock protection status"""
        from internal.Spectro.hardware_simulator import simulate_toggle_interlock
        result = simulate_toggle_interlock()
        if result is not None:
            state = "ACTIVE" if result else "OK"
            self.log_status(f"DEBUG: Interlock toggled to {state}")
        else:
            self.log_status("DEBUG: Driver not connected - connect first")

    def simulate_toggle_crowbar(self):
        """Toggle crowbar protection status"""
        from internal.Spectro.hardware_simulator import simulate_toggle_crowbar
        result = simulate_toggle_crowbar()
        if result is not None:
            state = "ACTIVE" if result else "OK"
            self.log_status(f"DEBUG: Crowbar toggled to {state}")
        else:
            self.log_status("DEBUG: Driver not connected - connect first")

    def simulate_toggle_overcurrent(self):
        """Toggle overcurrent protection status"""
        from internal.Spectro.hardware_simulator import simulate_toggle_overcurrent
        result = simulate_toggle_overcurrent()
        if result is not None:
            state = "FAULT" if result else "OK"
            self.log_status(f"DEBUG: Overcurrent toggled to {state}")
        else:
            self.log_status("DEBUG: Driver not connected - connect first")

    def simulate_toggle_overheat(self):
        """Toggle overheat protection status"""
        from internal.Spectro.hardware_simulator import simulate_toggle_overheat
        result = simulate_toggle_overheat()
        if result is not None:
            state = "WARNING" if result else "OK"
            self.log_status(f"DEBUG: Overheat toggled to {state}")
        else:
            self.log_status("DEBUG: Driver not connected - connect first")

    def simulate_toggle_ntc_interlock(self):
        """Toggle NTC interlock protection status"""
        from internal.Spectro.hardware_simulator import simulate_toggle_ntc_interlock
        result = simulate_toggle_ntc_interlock()
        if result is not None:
            state = "ACTIVE" if result else "OK"
            self.log_status(f"DEBUG: NTC Interlock toggled to {state}")
        else:
            self.log_status("DEBUG: Driver not connected - connect first")

    def show_simulation_info(self):
        """Show information about simulation mode"""
        QMessageBox.information(
            self,
            "Simulation Mode",
            "You are running in SIMULATION MODE.\n\n"
            "All hardware is simulated - no real devices are connected.\n\n"
            "To test USB disconnect handling:\n"
            "1. Connect to simulated devices\n"
            "2. Use Debug menu or keyboard shortcuts:\n"
            "   - Ctrl+Shift+D: Simulate Driver disconnect\n"
            "   - Ctrl+Shift+A: Simulate Arduino disconnect\n\n"
            "To use real hardware, restart without --simulate flag."
        )

    def save_settings(self):
        """Save all GUI parameters to persistent storage"""
        try:
            # Spectrometer parameters
            self.settings.setValue('integration_time', self.integration_time.value())
            self.settings.setValue('num_averages', self.num_averages.value())
            self.settings.setValue('num_scans', self.num_scans.value())
            self.settings.setValue('start_pixel', self.start_pixel.value())
            self.settings.setValue('stop_pixel', self.stop_pixel.value())
            
            # Save enabled/disabled states
            self.settings.setValue('save_enabled', self.save_enable.isChecked())
            self.settings.setValue('save_path', self.save_path.text())
            
            # Post-processing settings
            self.settings.setValue('postproc_enabled', self.postproc_enable.isChecked())
            self.settings.setValue('calibration_path', self.calib_path.text())
            
            # Window geometry
            self.settings.setValue('geometry', self.saveGeometry())
            
            self.log_status("✓ Settings saved")
            
        except Exception as e:
            self.log_status(f"Warning: Could not save settings: {str(e)}")
    
    def load_settings(self):
        """Load saved GUI parameters from persistent storage"""
        try:
            # Spectrometer parameters - with defaults
            if self.settings.contains('integration_time'):
                self.integration_time.setValue(self.settings.value('integration_time', 10.0, type=float))
            
            if self.settings.contains('num_averages'):
                self.num_averages.setValue(self.settings.value('num_averages', 1, type=int))
            
            if self.settings.contains('num_scans'):
                self.num_scans.setValue(self.settings.value('num_scans', 10, type=int))
            
            if self.settings.contains('start_pixel'):
                self.start_pixel.setValue(self.settings.value('start_pixel', 0, type=int))
            
            if self.settings.contains('stop_pixel'):
                self.stop_pixel.setValue(self.settings.value('stop_pixel', 4093, type=int))
            
            # Save settings
            if self.settings.contains('save_enabled'):
                self.save_enable.setChecked(self.settings.value('save_enabled', False, type=bool))
                self.update_save_controls()
            
            if self.settings.contains('save_path'):
                self.save_path.setText(self.settings.value('save_path', '', type=str))
            
            # Post-processing settings
            if self.settings.contains('postproc_enabled'):
                self.postproc_enable.setChecked(self.settings.value('postproc_enabled', False, type=bool))
            
            if self.settings.contains('calibration_path'):
                self.calib_path.setText(self.settings.value('calibration_path', '', type=str))
            
            # Window geometry
            if self.settings.contains('geometry'):
                self.restoreGeometry(self.settings.value('geometry'))
            
            self.log_status("✓ Previous settings loaded")
            
        except Exception as e:
            self.log_status(f"Warning: Could not load settings: {str(e)}")


def main():
    # Write native crash traces to a log file (windowed app has no console)
    _crash_log = open(os.path.join(os.path.expanduser('~'), 'avaspec_crash.log'), 'w')
    faulthandler.enable(_crash_log)

    # Print simulation banner if in simulation mode
    if SIMULATION_MODE:
        print_simulation_banner()

    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # Modern look

    gui = AvaSpecGUI()
    gui.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()