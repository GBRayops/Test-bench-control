"""
Hardware Simulator for AvaSpec-SF6100 Control Application
Provides mock implementations of all hardware interfaces for testing without physical devices.

Usage: python avaspec_gui_3modes.py --simulate
"""

import numpy as np
import time
import random
from threading import Lock, Thread


# =============================================================================
# AVASPEC SPECTROMETER SIMULATOR
# =============================================================================

class AvaSpecSimulator:
    """
    Simulates the AvaSpec spectrometer DLL functions.
    Generates realistic-looking spectra with configurable parameters.
    """

    # Simulated device info
    SIMULATED_SERIAL = b"SIM001234"
    SIMULATED_NUM_PIXELS = 4096
    SIMULATED_WAVELENGTH_MIN = 200.0  # nm
    SIMULATED_WAVELENGTH_MAX = 1100.0  # nm

    # Timing constants (from real hardware documentation)
    READOUT_TIME_FULL_SCALE = 8.7  # ms for 4094 pixels
    OVERHEAD_TIME = 1.0  # ms processing overhead

    def __init__(self):
        self.initialized = False
        self.activated = False
        self.handle = 1  # Simulated handle
        self.measuring = False
        self.measurement_ready = False
        self.scan_count = 0
        self.lock = Lock()

        # Measurement configuration
        self.start_pixel = 0
        self.stop_pixel = self.SIMULATED_NUM_PIXELS - 1
        self.integration_time = 10.0  # ms
        self.trigger_mode = 0  # 0=software, 2=external

        # Generate wavelength calibration
        self.wavelengths = np.linspace(
            self.SIMULATED_WAVELENGTH_MIN,
            self.SIMULATED_WAVELENGTH_MAX,
            self.SIMULATED_NUM_PIXELS
        )

        # Spectrum generation parameters
        self.noise_level = 100  # Base noise level
        self.peak_wavelengths = [450, 550, 650, 750]  # Peak positions (nm)
        self.peak_intensities = [30000, 45000, 35000, 20000]  # Peak heights
        self.peak_widths = [20, 25, 30, 35]  # Peak widths (nm)

        # For external trigger simulation
        self.trigger_thread = None
        self.pending_scans = 0
        self.scans_completed = 0

        # Timing simulation for external trigger mode
        self.last_scan_complete_time = 0  # When last scan finished (time.time())
        self.scan_in_progress = False  # True during integration + readout
        self.external_trigger_thread = None
        self.external_trigger_stop = False

    def get_readout_time(self):
        """Calculate readout time based on pixel range"""
        num_pixels = self.stop_pixel - self.start_pixel + 1
        # Readout time scales linearly with number of pixels
        return self.READOUT_TIME_FULL_SCALE * (num_pixels / self.SIMULATED_NUM_PIXELS)

    def get_min_scan_interval(self):
        """Get minimum time between scan completions"""
        return self.integration_time + self.get_readout_time() + self.OVERHEAD_TIME

    def can_accept_trigger(self):
        """Check if spectrometer can accept a new trigger (not BUSY)"""
        if self.scan_in_progress:
            return False
        return True

    def _generate_spectrum(self):
        """Generate a realistic-looking spectrum with peaks and noise"""
        spectrum = np.zeros(self.SIMULATED_NUM_PIXELS)

        # Add Gaussian peaks
        for wl, intensity, width in zip(self.peak_wavelengths,
                                         self.peak_intensities,
                                         self.peak_widths):
            peak = intensity * np.exp(-((self.wavelengths - wl) ** 2) / (2 * width ** 2))
            spectrum += peak

        # Add baseline
        spectrum += 500

        # Add random noise
        noise = np.random.normal(0, self.noise_level, self.SIMULATED_NUM_PIXELS)
        spectrum += noise

        # Add some random variation to peaks (simulate real measurement variability)
        variation = 1.0 + np.random.uniform(-0.05, 0.05)
        spectrum *= variation

        # Clip to valid range
        spectrum = np.clip(spectrum, 0, 65535)

        return spectrum


# Global simulator instance
_avaspec_sim = None

def get_avaspec_simulator():
    """Get or create the global AvaSpec simulator instance"""
    global _avaspec_sim
    if _avaspec_sim is None:
        _avaspec_sim = AvaSpecSimulator()
    return _avaspec_sim


# Simulated AvaSpec DLL functions
def AVS_Init_Sim(a_Port=0):
    """Initialize simulated spectrometer"""
    sim = get_avaspec_simulator()
    sim.initialized = True
    return 1  # 1 device found

def AVS_Done_Sim():
    """Close simulated spectrometer"""
    sim = get_avaspec_simulator()
    sim.initialized = False
    sim.activated = False
    return 0

def AVS_GetNrOfDevices_Sim():
    """Return number of simulated devices"""
    return 1

def AVS_UpdateUSBDevices_Sim():
    """Return number of simulated USB devices"""
    return 1

def AVS_GetList_Sim(spectrometers=1):
    """Return list of simulated devices"""
    class SimulatedIdentity:
        def __init__(self):
            self.SerialNumber = AvaSpecSimulator.SIMULATED_SERIAL
            self.UserFriendlyName = b"Simulated AvaSpec"
            self.Status = b'\x00'

    return (SimulatedIdentity(),)

def AVS_Activate_Sim(deviceId):
    """Activate simulated device"""
    sim = get_avaspec_simulator()
    sim.activated = True
    return sim.handle

def AVS_Deactivate_Sim(handle):
    """Deactivate simulated device"""
    sim = get_avaspec_simulator()
    sim.activated = False
    return True

def AVS_UseHighResAdc_Sim(handle, enable):
    """Set ADC resolution (simulated)"""
    return 0

def AVS_GetNumPixels_Sim(handle):
    """Get number of pixels"""
    return (0, AvaSpecSimulator.SIMULATED_NUM_PIXELS)

def AVS_GetLambda_Sim(handle):
    """Get wavelength calibration"""
    sim = get_avaspec_simulator()
    return (0, tuple(sim.wavelengths))

def AVS_PrepareMeasure_Sim(handle, measconfig):
    """Prepare measurement configuration"""
    sim = get_avaspec_simulator()
    sim.start_pixel = measconfig.m_StartPixel
    sim.stop_pixel = measconfig.m_StopPixel
    sim.integration_time = measconfig.m_IntegrationTime
    sim.trigger_mode = measconfig.m_Trigger_m_Mode
    return 0

def AVS_Measure_Sim(handle, windowhandle, nummeas):
    """Start simulated measurement"""
    sim = get_avaspec_simulator()
    sim.measuring = True
    sim.measurement_ready = False
    sim.pending_scans = nummeas
    sim.scans_completed = 0
    sim.scan_in_progress = False
    sim.external_trigger_stop = False

    def simulate_single_scan():
        """Simulate a single scan with proper timing (integration + readout)"""
        sim.scan_in_progress = True
        sim.measurement_ready = False

        # Simulate integration time
        time.sleep(sim.integration_time / 1000.0)

        # Simulate readout time
        readout_time = sim.get_readout_time()
        time.sleep(readout_time / 1000.0)

        # Simulate overhead
        time.sleep(sim.OVERHEAD_TIME / 1000.0)

        # Scan complete
        sim.scan_in_progress = False
        sim.measurement_ready = True
        sim.scans_completed += 1
        sim.last_scan_complete_time = time.time()

    if sim.trigger_mode == 0:
        # Software trigger - start immediately
        Thread(target=simulate_single_scan, daemon=True).start()
    else:
        # External trigger mode - wait for triggers from Arduino simulator
        # Each trigger starts one scan if spectrometer is not BUSY
        # Triggers arriving while BUSY are ignored (like real hardware)
        def external_trigger_handler():
            """Handle external triggers with realistic timing"""
            arduino_sim = get_arduino_simulator()
            last_trigger_cycle = 0

            while sim.scans_completed < sim.pending_scans and not sim.external_trigger_stop:
                # Check if Arduino is generating triggers
                if arduino_sim and arduino_sim.running:
                    current_cycle = arduino_sim.cycles_sent

                    # Check if a new trigger has arrived
                    if current_cycle > last_trigger_cycle:
                        # New trigger(s) arrived
                        if sim.can_accept_trigger():
                            # Spectrometer is IDLE - accept trigger and start scan
                            last_trigger_cycle = current_cycle
                            simulate_single_scan()

                            # Wait for scan to complete before checking next trigger
                            while sim.scan_in_progress and not sim.external_trigger_stop:
                                time.sleep(0.0005)
                        else:
                            # Spectrometer is BUSY - trigger ignored (like real hardware)
                            # Update last_trigger_cycle to skip missed triggers
                            last_trigger_cycle = current_cycle
                    else:
                        # No new trigger yet
                        time.sleep(0.0005)
                else:
                    # Arduino not running yet, wait
                    time.sleep(0.005)

            sim.measuring = False

        sim.external_trigger_thread = Thread(target=external_trigger_handler, daemon=True)
        sim.external_trigger_thread.start()

    return 0

def AVS_PollScan_Sim(handle):
    """Poll for measurement completion"""
    sim = get_avaspec_simulator()
    return sim.measurement_ready

def AVS_GetScopeData_Sim(handle):
    """Get measured spectrum data"""
    sim = get_avaspec_simulator()

    # Generate spectrum
    spectrum = sim._generate_spectrum()

    # Reset for next measurement
    sim.measurement_ready = False
    sim.scan_count += 1

    # Simulate timestamp
    timestamp = int(time.time() * 100) % 0xFFFFFFFF

    return (timestamp, tuple(spectrum))

def AVS_StopMeasure_Sim(handle):
    """Stop measurement"""
    sim = get_avaspec_simulator()
    sim.measuring = False
    sim.measurement_ready = False
    sim.external_trigger_stop = True  # Signal external trigger handler to stop
    sim.scan_in_progress = False
    return 0


# =============================================================================
# SF6100 LASER DRIVER SIMULATOR
# =============================================================================

class SF6100Simulator:
    """
    Simulates the SF6100 laser driver serial interface.
    Maintains internal state and provides realistic responses.
    """

    # Copy constants from real driver
    STATE_START = "0008"
    STATE_STOP = "0010"
    STATE_CURRENT_INTERNAL = "0020"
    STATE_CURRENT_EXTERNAL = "0040"
    STATE_ENABLE_EXTERNAL = "0200"
    STATE_ENABLE_INTERNAL = "0400"
    STATE_INTERLOCK_ALLOW = "1000"
    STATE_INTERLOCK_DENY = "2000"
    STATE_NTC_ALLOW = "8000"
    STATE_NTC_DENY = "4000"

    def __init__(self):
        self.is_connected = False
        self.serial_port = None  # For compatibility with real driver
        self.lock = Lock()
        self._simulated_disconnect = False  # For testing disconnect handling

        # Simulated state
        self._powered = True
        self._started = False
        self._current_internal = True
        self._enable_internal = False  # Default external
        self._ntc_denied = False
        self._interlock_denied = False

        # Simulated settings
        self._current_setpoint = 0.0  # Amps
        self._frequency = 0.0  # Hz (0 = CW)
        self._duration = 10.0  # ms
        self._ntc_lower = 15.0  # °C
        self._ntc_upper = 40.0  # °C
        self._ntc_b_coeff = 3977

        # Simulated measurements (with some random variation)
        self._base_pcb_temp = 35.0
        self._base_ntc_temp = 25.0

        # Simulated protection status
        self._interlock_active = False
        self._crowbar_active = False
        self._overcurrent = False
        self._overheat = False
        self._ntc_interlock = False

    def connect(self, port, baudrate=115200, timeout=1.0):
        """Simulate connection"""
        time.sleep(0.2)  # Simulate connection delay
        self.is_connected = True
        self._simulated_disconnect = False
        return True

    def disconnect(self):
        """Simulate disconnection"""
        self._started = False
        self.is_connected = False
        self.serial_port = None

    def simulate_usb_disconnect(self):
        """Simulate USB cable being unplugged (for testing)"""
        self._simulated_disconnect = True
        self.is_connected = False
        self.serial_port = None
        print("[SIMULATOR] USB disconnect simulated!")

    def toggle_interlock(self):
        """Toggle interlock protection status"""
        self._interlock_active = not self._interlock_active
        state = "ACTIVE" if self._interlock_active else "OK"
        print(f"[SIMULATOR] Interlock: {state}")
        return self._interlock_active

    def toggle_crowbar(self):
        """Toggle crowbar protection status"""
        self._crowbar_active = not self._crowbar_active
        state = "ACTIVE" if self._crowbar_active else "OK"
        print(f"[SIMULATOR] Crowbar: {state}")
        return self._crowbar_active

    def toggle_overcurrent(self):
        """Toggle overcurrent protection status"""
        self._overcurrent = not self._overcurrent
        state = "FAULT" if self._overcurrent else "OK"
        print(f"[SIMULATOR] Overcurrent: {state}")
        return self._overcurrent

    def toggle_overheat(self):
        """Toggle overheat protection status"""
        self._overheat = not self._overheat
        state = "WARNING" if self._overheat else "OK"
        print(f"[SIMULATOR] Overheat: {state}")
        return self._overheat

    def toggle_ntc_interlock(self):
        """Toggle NTC interlock protection status"""
        self._ntc_interlock = not self._ntc_interlock
        state = "ACTIVE" if self._ntc_interlock else "OK"
        print(f"[SIMULATOR] NTC Interlock: {state}")
        return self._ntc_interlock

    def _check_connection(self):
        """Check if simulated connection is still valid"""
        if self._simulated_disconnect:
            self.is_connected = False
            return False
        return self.is_connected

    def set_current(self, current_amps, verify=True):
        """Set current setpoint"""
        if not self._check_connection():
            return False
        if 0 <= current_amps <= 20:
            self._current_setpoint = current_amps
            return True
        return False

    def get_current_setpoint(self):
        """Get current setpoint"""
        if not self._check_connection():
            return None
        return self._current_setpoint

    def get_current_measured(self):
        """Get measured current (with noise)"""
        if not self._check_connection():
            return None
        if self._started and not self._interlock_active:
            # Return setpoint with small variation
            return self._current_setpoint * (1 + random.uniform(-0.02, 0.02))
        return 0.0

    def get_current_min(self):
        """Get minimum current limit (simulated as 0)"""
        if not self._check_connection():
            return None
        return 0.0

    def get_current_max(self):
        """Get maximum current limit (simulated hardware pot setting)"""
        if not self._check_connection():
            return None
        return 20.0  # Simulate 20A max (hardware potentiometer setting)

    def get_voltage_measured(self):
        """Get measured voltage"""
        if not self._check_connection():
            return None
        if self._started and not self._interlock_active:
            # Simulate voltage based on current (V = I * R, assuming ~2 ohm load)
            return self._current_setpoint * 2.0 * (1 + random.uniform(-0.05, 0.05))
        return 0.0

    def set_frequency(self, frequency_hz, verify=True):
        """Set QCW frequency"""
        if 0 <= frequency_hz <= 500:
            self._frequency = frequency_hz
            return True
        return False

    def get_frequency(self):
        """Get QCW frequency"""
        return self._frequency

    def set_duration(self, duration_ms, verify=True):
        """Set pulse duration"""
        if 0.1 <= duration_ms <= 5000:
            self._duration = duration_ms
            return True
        return False

    def get_duration(self):
        """Get pulse duration"""
        return self._duration

    def set_state(self, state_value):
        """Set device state"""
        if state_value == self.STATE_START:
            if not self._interlock_active:
                self._started = True
        elif state_value == self.STATE_STOP:
            self._started = False
        elif state_value == self.STATE_ENABLE_INTERNAL:
            self._enable_internal = True
        elif state_value == self.STATE_ENABLE_EXTERNAL:
            self._enable_internal = False
        elif state_value == self.STATE_CURRENT_INTERNAL:
            self._current_internal = True
        elif state_value == self.STATE_CURRENT_EXTERNAL:
            self._current_internal = False
        elif state_value == self.STATE_NTC_ALLOW:
            self._ntc_denied = False
        elif state_value == self.STATE_NTC_DENY:
            self._ntc_denied = True
        return True

    def start(self):
        """Start output"""
        return self.set_state(self.STATE_START)

    def stop(self):
        """Stop output"""
        return self.set_state(self.STATE_STOP)

    def get_state(self):
        """Get device state"""
        if not self._check_connection():
            return None
        return {
            'powered': self._powered,
            'started': self._started,
            'current_internal': self._current_internal,
            'enable_internal': self._enable_internal,
            'ntc_denied': self._ntc_denied,
            'interlock_denied': self._interlock_denied,
            'raw_value': 0
        }

    def get_lock_status(self):
        """Get protection status"""
        if not self._check_connection():
            return None
        return {
            'interlock': self._interlock_active,
            'crowbar': self._crowbar_active,
            'overcurrent': self._overcurrent,
            'overheat': self._overheat,
            'ntc_interlock': self._ntc_interlock,
            'raw_value': 0
        }

    def get_pcb_temperature(self):
        """Get PCB temperature with variation"""
        if not self._check_connection():
            return None
        return self._base_pcb_temp + random.uniform(-1, 1)

    def get_ntc_temperature(self):
        """Get NTC temperature with variation"""
        if not self._check_connection():
            return None
        return self._base_ntc_temp + random.uniform(-0.5, 0.5)

    def set_ntc_lower_limit(self, temperature_celsius):
        """Set NTC lower limit"""
        self._ntc_lower = temperature_celsius
        return True

    def set_ntc_upper_limit(self, temperature_celsius):
        """Set NTC upper limit"""
        self._ntc_upper = temperature_celsius
        return True

    def set_ntc_b_coefficient(self, b_value):
        """Set NTC B coefficient"""
        self._ntc_b_coeff = b_value
        return True

    def get_device_id(self):
        """Get simulated device ID"""
        return 0x6100  # SF6100

    def get_serial_number(self):
        """Get simulated serial number"""
        return 12345

    # === Methods for test control ===

    def simulate_interlock(self, active):
        """Simulate interlock activation (for testing)"""
        self._interlock_active = active
        if active:
            self._started = False


# =============================================================================
# ARDUINO TRIGGER SIMULATOR
# =============================================================================

class ArduinoTriggerSimulator:
    """
    Simulates the Arduino trigger controller.
    Provides realistic trigger generation simulation.
    """

    def __init__(self, port=None, baud=115200, timeout=1):
        self.ser = None  # No actual serial port
        self.is_connected = True  # For compatibility with real controller
        self.running = False
        self.cycles_sent = 0
        self._simulated_disconnect = False  # For testing disconnect handling

        # Parameters
        self._num_cycles = 0  # 0 = continuous
        self._frequency = 100  # Hz
        self._duty_spec = 10  # %
        self._duty_laser = 50  # %
        self._offset = 0  # µs

        # Simulation thread
        self._trigger_thread = None
        self._stop_flag = False

    def is_port_open(self):
        """Check if simulated port is still open"""
        return self.is_connected and not self._simulated_disconnect

    def simulate_usb_disconnect(self):
        """Simulate USB cable being unplugged (for testing)"""
        self._simulated_disconnect = True
        self.is_connected = False
        self._stop_flag = True
        self.running = False
        print("[SIMULATOR] Arduino USB disconnect simulated!")

    def start(self):
        """Start trigger generation"""
        if not self.is_port_open():
            return "ERR:DISCONNECTED"
        self.running = True
        self.cycles_sent = 0
        self._stop_flag = False

        def trigger_loop():
            """Simulate trigger generation"""
            period = 1.0 / self._frequency if self._frequency > 0 else 1.0
            target_cycles = self._num_cycles if self._num_cycles > 0 else float('inf')

            while not self._stop_flag and self.cycles_sent < target_cycles:
                if self._simulated_disconnect:
                    break
                time.sleep(period)
                self.cycles_sent += 1

            self.running = False

        self._trigger_thread = Thread(target=trigger_loop, daemon=True)
        self._trigger_thread.start()

        return "OK:STARTED"

    def stop(self):
        """Stop trigger generation"""
        if not self.is_port_open():
            return "ERR:DISCONNECTED"
        self._stop_flag = True
        self.running = False
        return "OK:STOPPED"

    def get_status(self):
        """Get current status"""
        if not self.is_port_open():
            return None
        return {
            'running': self.running,
            'cycles': self.cycles_sent,
            'frequency': self._frequency,
            'duty_spec': self._duty_spec,
            'duty_laser': self._duty_laser,
            'offset': self._offset
        }

    def set_cycles(self, cycles):
        """Set number of cycles"""
        if not self.is_port_open():
            return "ERR:DISCONNECTED"
        self._num_cycles = cycles
        return "OK:CYCLES"

    def set_frequency(self, freq):
        """Set frequency"""
        if not self.is_port_open():
            return "ERR:DISCONNECTED"
        self._frequency = freq
        return "OK:FREQ"

    def set_duty_spec(self, duty):
        """Set spectrometer duty cycle"""
        if not self.is_port_open():
            return "ERR:DISCONNECTED"
        self._duty_spec = duty
        return "OK:DUTY_SPEC"

    def set_duty_laser(self, duty):
        """Set laser duty cycle"""
        if not self.is_port_open():
            return "ERR:DISCONNECTED"
        self._duty_laser = duty
        return "OK:DUTY_LASER"

    def set_offset(self, offset_us):
        """Set laser offset"""
        if not self.is_port_open():
            return "ERR:DISCONNECTED"
        self._offset = offset_us
        return "OK:OFFSET"

    def set_all_params(self, cycles, freq, duty_spec, duty_laser, offset):
        """Set all parameters at once"""
        if not self.is_port_open():
            return "ERR:DISCONNECTED"
        self._num_cycles = cycles
        self._frequency = freq
        self._duty_spec = duty_spec
        self._duty_laser = duty_laser
        self._offset = offset
        return "OK:PARAMS"

    def close(self):
        """Close connection"""
        self._stop_flag = True
        self.running = False
        self.is_connected = False


# =============================================================================
# SIMULATION MODE HELPERS
# =============================================================================

def is_simulation_mode():
    """Check if simulation mode is enabled via command line"""
    import sys
    return '--simulate' in sys.argv


def print_simulation_banner():
    """Print banner indicating simulation mode is active"""
    banner = """
╔══════════════════════════════════════════════════════════════════╗
║                    🔧 SIMULATION MODE ACTIVE 🔧                   ║
║                                                                  ║
║  All hardware is simulated. No real devices are connected.       ║
║  - Spectrometer: Generates synthetic spectra                     ║
║  - Laser Driver: Simulated state and measurements                ║
║  - Arduino: Simulated trigger generation                         ║
║                                                                  ║
║  USB DISCONNECT TESTING:                                         ║
║  In Python console, import hardware_simulator and call:          ║
║  - simulate_driver_disconnect() - simulate driver USB unplug     ║
║  - simulate_arduino_disconnect() - simulate Arduino USB unplug   ║
║                                                                  ║
║  To use real hardware, restart without --simulate flag           ║
╚══════════════════════════════════════════════════════════════════╝
"""
    print(banner)


# =============================================================================
# USB DISCONNECT SIMULATION HELPERS (for testing)
# =============================================================================

# Global references to active simulators (set by GUI when connecting)
_active_driver_sim = None
_active_arduino_sim = None


def register_driver_simulator(driver_sim):
    """Register the active driver simulator instance for disconnect testing"""
    global _active_driver_sim
    _active_driver_sim = driver_sim


def register_arduino_simulator(arduino_sim):
    """Register the active Arduino simulator instance for disconnect testing"""
    global _active_arduino_sim
    _active_arduino_sim = arduino_sim


def get_arduino_simulator():
    """Get the registered Arduino simulator instance"""
    global _active_arduino_sim
    return _active_arduino_sim


def simulate_driver_disconnect():
    """
    Simulate USB disconnect for the laser driver.
    Call this from Python console to test disconnect handling.
    """
    global _active_driver_sim
    if _active_driver_sim:
        _active_driver_sim.simulate_usb_disconnect()
        return True
    else:
        print("[SIMULATOR] No driver simulator registered. Connect to driver first.")
        return False


def simulate_arduino_disconnect():
    """
    Simulate USB disconnect for the Arduino.
    Call this from Python console to test disconnect handling.
    """
    global _active_arduino_sim
    if _active_arduino_sim:
        _active_arduino_sim.simulate_usb_disconnect()
        return True
    else:
        print("[SIMULATOR] No Arduino simulator registered. Connect to Arduino first.")
        return False


def simulate_toggle_interlock():
    """Toggle interlock protection status on driver simulator."""
    global _active_driver_sim
    if _active_driver_sim:
        return _active_driver_sim.toggle_interlock()
    else:
        print("[SIMULATOR] No driver simulator registered. Connect to driver first.")
        return None


def simulate_toggle_crowbar():
    """Toggle crowbar protection status on driver simulator."""
    global _active_driver_sim
    if _active_driver_sim:
        return _active_driver_sim.toggle_crowbar()
    else:
        print("[SIMULATOR] No driver simulator registered. Connect to driver first.")
        return None


def simulate_toggle_overcurrent():
    """Toggle overcurrent protection status on driver simulator."""
    global _active_driver_sim
    if _active_driver_sim:
        return _active_driver_sim.toggle_overcurrent()
    else:
        print("[SIMULATOR] No driver simulator registered. Connect to driver first.")
        return None


def simulate_toggle_overheat():
    """Toggle overheat protection status on driver simulator."""
    global _active_driver_sim
    if _active_driver_sim:
        return _active_driver_sim.toggle_overheat()
    else:
        print("[SIMULATOR] No driver simulator registered. Connect to driver first.")
        return None


def simulate_toggle_ntc_interlock():
    """Toggle NTC interlock protection status on driver simulator."""
    global _active_driver_sim
    if _active_driver_sim:
        return _active_driver_sim.toggle_ntc_interlock()
    else:
        print("[SIMULATOR] No driver simulator registered. Connect to driver first.")
        return None
