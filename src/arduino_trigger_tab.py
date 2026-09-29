"""
Arduino Trigger Control Tab
Provides synchronized external trigger control for spectrometer and laser driver via Arduino Uno
Works alongside existing software trigger modes
"""

import serial
import serial.tools.list_ports
import struct
import time
import os
import sys
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QSpinBox, QDoubleSpinBox, QComboBox,
                             QGroupBox, QGridLayout, QCheckBox, QMessageBox,
                             QTextEdit, QRadioButton, QButtonGroup, QScrollArea)
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFont, QPixmap

# Check for simulation mode
SIMULATION_MODE = '--simulate' in sys.argv
if SIMULATION_MODE:
    from hardware_simulator import ArduinoTriggerSimulator


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    # Get the project root (parent of src folder)
    src_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(src_dir)
    return os.path.join(project_root, relative_path)


class ArduinoTriggerController:
    """Arduino trigger controller with binary protocol"""

    def __init__(self, port, baud=115200, timeout=1):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.is_connected = True
        self.laser_silenced = False
        self.spec_silenced = False
        time.sleep(2)  # Wait for Arduino reset
        self.ser.flushInput()

    def is_port_open(self):
        """Check if serial port is still open and valid"""
        try:
            return self.ser and self.ser.is_open
        except (serial.SerialException, OSError, AttributeError):
            return False

    def start(self):
        """Start trigger generation"""
        if not self.is_port_open():
            self.is_connected = False
            return "ERR:DISCONNECTED"
        try:
            self.laser_silenced = False
            self.spec_silenced = False
            self.ser.write(b'S')
            return self._read_response()
        except (serial.SerialException, OSError):
            self.is_connected = False
            return "ERR:DISCONNECTED"

    def stop(self):
        """Stop trigger generation"""
        if not self.is_port_open():
            self.is_connected = False
            return "ERR:DISCONNECTED"
        try:
            self.laser_silenced = False
            self.spec_silenced = False
            self.ser.write(b'X')
            return self._read_response()
        except (serial.SerialException, OSError):
            self.is_connected = False
            return "ERR:DISCONNECTED"

    def get_status(self):
        """Query current status"""
        if not self.is_port_open():
            self.is_connected = False
            return None
        try:
            self.ser.write(b'Q')
            response = self.ser.readline().decode().strip()

            if response.startswith('STATUS,'):
                parts = response.split(',')
                return {
                    'running': bool(int(parts[1])),
                    'cycles': int(parts[2]),
                    'frequency': int(parts[3]),
                    'duty_spec': int(parts[4]),
                    'duty_laser': int(parts[5]),
                    'offset': int(parts[6]),
                    'laser_cycles': int(parts[7]) if len(parts) > 7 else 0,
                    'spec_cycles':  int(parts[8]) if len(parts) > 8 else 0,
                }
            return None
        except (serial.SerialException, OSError):
            self.is_connected = False
            return None
        except Exception:
            return None
    
    def set_cycles(self, cycles):
        """Set number of cycles (0 = continuous)"""
        if not self.is_port_open():
            self.is_connected = False
            return "ERR:DISCONNECTED"
        try:
            cmd = b'C' + cycles.to_bytes(4, 'little')
            self.ser.write(cmd)
            return self._read_response()
        except (serial.SerialException, OSError):
            self.is_connected = False
            return "ERR:DISCONNECTED"

    def set_frequency(self, freq):
        """Set frequency in Hz (1-1000)"""
        if not self.is_port_open():
            self.is_connected = False
            return "ERR:DISCONNECTED"
        try:
            cmd = b'F' + freq.to_bytes(2, 'little')
            self.ser.write(cmd)
            return self._read_response()
        except (serial.SerialException, OSError):
            self.is_connected = False
            return "ERR:DISCONNECTED"

    def set_duty_spec(self, duty):
        """Set spectrometer duty cycle (0-100%)"""
        if not self.is_port_open():
            self.is_connected = False
            return "ERR:DISCONNECTED"
        try:
            cmd = b'A' + bytes([duty])
            self.ser.write(cmd)
            return self._read_response()
        except (serial.SerialException, OSError):
            self.is_connected = False
            return "ERR:DISCONNECTED"

    def set_duty_laser(self, duty):
        """Set laser duty cycle (0-100%)"""
        if not self.is_port_open():
            self.is_connected = False
            return "ERR:DISCONNECTED"
        try:
            cmd = b'B' + bytes([duty])
            self.ser.write(cmd)
            return self._read_response()
        except (serial.SerialException, OSError):
            self.is_connected = False
            return "ERR:DISCONNECTED"

    def set_offset(self, offset_us):
        """Set laser offset in microseconds (negative = before spec, positive = after spec)"""
        if not self.is_port_open():
            self.is_connected = False
            return "ERR:DISCONNECTED"
        try:
            # Send as signed int16
            if offset_us < 0:
                # Convert negative to two's complement for int16
                offset_bytes = (offset_us & 0xFFFF).to_bytes(2, 'little', signed=False)
            else:
                offset_bytes = offset_us.to_bytes(2, 'little', signed=False)

            cmd = b'O' + offset_bytes
            self.ser.write(cmd)
            return self._read_response()
        except (serial.SerialException, OSError):
            self.is_connected = False
            return "ERR:DISCONNECTED"

    def stop_spec(self):
        """Silence SPEC pin only — laser continues unaffected"""
        if not self.is_port_open():
            self.is_connected = False
            return "ERR:DISCONNECTED"
        try:
            self.spec_silenced = True
            self.ser.write(b'E')
            return self._read_response()
        except (serial.SerialException, OSError):
            self.is_connected = False
            return "ERR:DISCONNECTED"

    def stop_laser(self):
        """Silence LASER pin only — spec continues unaffected"""
        if not self.is_port_open():
            self.is_connected = False
            return "ERR:DISCONNECTED"
        try:
            self.laser_silenced = True
            self.ser.write(b'L')
            return self._read_response()
        except (serial.SerialException, OSError):
            self.is_connected = False
            return "ERR:DISCONNECTED"

    def set_all_params(self, cycles, laser_cycles, freq, duty_spec, duty_laser, offset):
        """Set all parameters atomically.

        cycles       -- total spec triggers (0 = continuous)
        laser_cycles -- laser fires for first N cycles only (0 = same as cycles)
        """
        if not self.is_port_open():
            self.is_connected = False
            return "ERR:DISCONNECTED"
        try:
            time.sleep(0.05)
            self.ser.reset_input_buffer()
            time.sleep(0.05)

            offset_unsigned = offset & 0xFFFF if offset < 0 else offset

            # I=uint32 cycles, I=uint32 laser_cycles, H=uint16 freq,
            # B=uint8 duty_spec, B=uint8 duty_laser, H=uint16 offset → 14 bytes
            params = struct.pack('<IIHBBH',
                                 cycles, laser_cycles, freq,
                                 duty_spec, duty_laser, offset_unsigned)

            self.ser.write(b'P' + params)
            self.ser.flush()

            time.sleep(0.5)

            return self._read_response()
        except (serial.SerialException, OSError):
            self.is_connected = False
            return "ERR:DISCONNECTED"
    
    def _read_response(self):
        """Read and parse response"""
        try:
            if not self.is_port_open():
                self.is_connected = False
                return "ERR:DISCONNECTED"

            # Wait for response to arrive
            for i in range(30):  # Wait up to 300ms
                if self.ser.in_waiting > 0:
                    break
                time.sleep(0.01)

            # Read response
            if self.ser.in_waiting > 0:
                response = self.ser.readline().decode('utf-8', errors='ignore').strip()
                # If we got an empty line, try reading one more
                if not response and self.ser.in_waiting > 0:
                    response = self.ser.readline().decode('utf-8', errors='ignore').strip()
                return response if response else "ERR:NO_RESPONSE"

            return "ERR:NO_RESPONSE"
        except (serial.SerialException, OSError):
            self.is_connected = False
            return "ERR:DISCONNECTED"

    def close(self):
        """Close serial connection safely"""
        self.is_connected = False
        if self.ser:
            try:
                if self.ser.is_open:
                    self.ser.close()
            except (serial.SerialException, OSError):
                pass
            except Exception:
                pass
        self.ser = None


class ArduinoTriggerTab(QWidget):
    """GUI tab for Arduino trigger control"""

    # Constants
    SPEC_TRIGGER_WIDTH_MS = 1.0  # Fixed spec trigger width in milliseconds

    # Signals
    status_update = Signal(str)
    trigger_mode_changed = Signal(bool)  # True = external, False = software
    auto_start_spectrometer = Signal()  # Signal to auto-start spectrometer measurement
    
    def __init__(self, settings=None):
        super().__init__()
        self.arduino = None
        self.connected = False
        self.settings = settings
        self.init_ui()

        # Status update timer (when running triggers)
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status_display)

        # Connection check timer (always running when connected)
        self.connection_check_timer = QTimer()
        self.connection_check_timer.timeout.connect(self.check_connection)
        self.connection_check_timer.setInterval(500)  # Check every 500ms

        # Load saved settings
        if self.settings:
            self.load_settings()
        
    def init_ui(self):
        """Initialize the user interface"""
        # Create scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # Container widget
        container = QWidget()
        main_layout = QVBoxLayout()
        container.setLayout(main_layout)
        scroll.setWidget(container)
        
        # Set scroll as main layout
        tab_layout = QVBoxLayout()
        tab_layout.addWidget(scroll)
        self.setLayout(tab_layout)
        
        # === INFO GROUP ===
        info_group = QGroupBox("External Trigger Mode")
        info_layout = QVBoxLayout()
        
        info_text = QLabel(
            "When enabled, the Arduino generates synchronized hardware triggers for both:\n"
            "• Spectrometer (Pin 3 → Trigger Input)\n"
            "• Laser Driver (Pin 4 → External Enable Pin)\n\n"
            "The laser can fire BEFORE, WITH, or AFTER the spectrometer:\n"
            "• Negative offset: Laser fires before spectrometer integration\n"
            "• Zero offset: Both triggers start simultaneously\n"
            "• Positive offset: Laser fires after spectrometer integration starts\n\n"
            "Software trigger modes will be disabled when external trigger is active."
        )
        info_text.setWordWrap(True)
        info_text.setStyleSheet("background-color: #e3f2fd; padding: 10px; border-radius: 5px;")
        info_layout.addWidget(info_text)
        
        info_group.setLayout(info_layout)
        main_layout.addWidget(info_group)
        
        # === CONNECTION GROUP ===
        conn_group = QGroupBox("Arduino Connection")
        conn_layout = QGridLayout()
        
        # Serial port selection
        conn_layout.addWidget(QLabel("Serial Port:"), 0, 0)
        self.port_combo = QComboBox()
        self.refresh_ports()
        conn_layout.addWidget(self.port_combo, 0, 1)
        
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_ports)
        conn_layout.addWidget(self.refresh_btn, 0, 2)
        
        # Connect button
        self.connect_btn = QPushButton("Connect to Arduino")
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.connect_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px;")
        conn_layout.addWidget(self.connect_btn, 1, 0, 1, 3)
        
        # Connection status
        self.conn_status = QLabel("Not connected")
        self.conn_status.setStyleSheet("color: gray; font-weight: bold;")
        conn_layout.addWidget(self.conn_status, 2, 0, 1, 3)
        
        conn_group.setLayout(conn_layout)
        main_layout.addWidget(conn_group)
        
        # === TRIGGER MODE GROUP ===
        mode_group = QGroupBox("Trigger Mode Selection")
        mode_layout = QVBoxLayout()
        
        self.trigger_mode_group = QButtonGroup()
        
        self.software_mode_radio = QRadioButton("Software Trigger (Spectrometer internal)")
        self.software_mode_radio.setChecked(True)
        self.software_mode_radio.toggled.connect(self.on_trigger_mode_changed)
        self.trigger_mode_group.addButton(self.software_mode_radio, 0)
        mode_layout.addWidget(self.software_mode_radio)
        
        self.external_mode_radio = QRadioButton("External Trigger (Arduino synchronized)")
        self.external_mode_radio.setEnabled(False)  # Disabled until connected
        self.external_mode_radio.toggled.connect(self.on_trigger_mode_changed)
        self.trigger_mode_group.addButton(self.external_mode_radio, 1)
        mode_layout.addWidget(self.external_mode_radio)
        
        # Auto-start checkbox
        self.auto_start_checkbox = QCheckBox("Auto-start spectrometer when Arduino triggers start")
        self.auto_start_checkbox.setChecked(True)  # Enabled by default
        self.auto_start_checkbox.setToolTip(
            "When enabled, starting Arduino triggers will automatically start\n"
            "a spectrometer measurement sequence. You can still manually start\n"
            "additional measurements while Arduino continues running."
        )
        mode_layout.addWidget(self.auto_start_checkbox)
        
        # Status indicator
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("Current Mode:"))
        self.mode_status = QLabel("SOFTWARE")
        self.mode_status.setStyleSheet("font-weight: bold; color: blue;")
        status_layout.addWidget(self.mode_status)
        status_layout.addStretch()
        mode_layout.addLayout(status_layout)
        
        mode_group.setLayout(mode_layout)
        main_layout.addWidget(mode_group)
        
        # === TRIGGER PARAMETERS GROUP ===
        self.params_group = QGroupBox("Trigger Parameters (External Mode)")
        self.params_group.setEnabled(False)
        params_layout = QGridLayout()
        
        # Frequency
        params_layout.addWidget(QLabel("Trigger Frequency:"), 0, 0)
        self.frequency = QSpinBox()
        self.frequency.setRange(1, 1000)
        self.frequency.setValue(100)
        self.frequency.setSuffix(" Hz")
        self.frequency.valueChanged.connect(self.on_param_changed)
        params_layout.addWidget(self.frequency, 0, 1)
        params_layout.addWidget(QLabel("(1-1000 Hz)"), 0, 2)
        
        # Number of laser pulses
        params_layout.addWidget(QLabel("Number of Laser Pulses:"), 1, 0)
        self.num_triggers = QSpinBox()
        self.num_triggers.setRange(0, 1000000)
        self.num_triggers.setValue(0)
        self.num_triggers.setSpecialValueText("Continuous")
        self.num_triggers.valueChanged.connect(self.on_param_changed)
        params_layout.addWidget(self.num_triggers, 1, 1)
        params_layout.addWidget(QLabel("(0 = no limit, full sequence)"), 1, 2)

        # Laser duty cycle
        params_layout.addWidget(QLabel("Laser Trigger Width:"), 2, 0)
        self.duty_laser = QSpinBox()
        self.duty_laser.setRange(1, 100)
        self.duty_laser.setValue(50)
        self.duty_laser.setSuffix(" %")
        self.duty_laser.valueChanged.connect(self.on_param_changed)
        params_layout.addWidget(self.duty_laser, 2, 1)
        params_layout.addWidget(QLabel("(pulse width)"), 2, 2)

        # Laser offset with unit selector
        params_layout.addWidget(QLabel("Laser Delay (Offset):"), 3, 0)

        offset_layout = QHBoxLayout()
        self.offset = QSpinBox()
        self.offset.setRange(-32767, 32767)  # Signed int16 range in µs
        self.offset.setValue(0)
        self.offset.valueChanged.connect(self.on_param_changed)
        offset_layout.addWidget(self.offset)

        self.offset_unit = QComboBox()
        self.offset_unit.addItems(["µs", "ms"])
        self.offset_unit.currentIndexChanged.connect(self.on_offset_unit_changed)
        offset_layout.addWidget(self.offset_unit)

        params_layout.addLayout(offset_layout, 3, 1)

        offset_help = QLabel("(- = laser before spec, + = laser after spec)")
        offset_help.setStyleSheet("color: gray; font-size: 10px;")
        params_layout.addWidget(offset_help, 3, 2)

        self.params_group.setLayout(params_layout)
        main_layout.addWidget(self.params_group)
        
        # === CONTROL GROUP ===
        self.control_group = QGroupBox("Trigger Control")
        self.control_group.setEnabled(False)
        control_layout = QVBoxLayout()
        
        # Start/Stop buttons
        btn_layout = QHBoxLayout()
        
        self.start_btn = QPushButton("START TRIGGERS")
        self.start_btn.clicked.connect(self.start_triggers)
        self.start_btn.setEnabled(False)  # Disabled by default (Software mode)
        self.start_btn.setStyleSheet("""
            QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 12px; font-size: 14px; }
            QPushButton:disabled { background-color: #a0a0a0; color: #606060; }
        """)
        btn_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("STOP TRIGGERS")
        self.stop_btn.clicked.connect(self.stop_triggers)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 12px; font-size: 14px;")
        btn_layout.addWidget(self.stop_btn)
        
        control_layout.addLayout(btn_layout)
        
        # Status display
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("Trigger Status:"))
        self.trigger_status = QLabel("●")
        self.trigger_status.setFont(QFont("Arial", 16))
        self.trigger_status.setStyleSheet("color: gray;")
        status_layout.addWidget(self.trigger_status)
        self.trigger_status_text = QLabel("DISCONNECTED")
        self.trigger_status_text.setStyleSheet("font-weight: bold; color: gray;")
        status_layout.addWidget(self.trigger_status_text)
        status_layout.addStretch()
        control_layout.addLayout(status_layout)
        
        # Cycle counter
        counter_layout = QHBoxLayout()
        counter_layout.addWidget(QLabel("Triggers Sent:"))
        self.cycle_counter = QLabel("0")
        self.cycle_counter.setStyleSheet("font-size: 18px; font-weight: bold; color: blue;")
        counter_layout.addWidget(self.cycle_counter)
        counter_layout.addStretch()
        control_layout.addLayout(counter_layout)
        
        self.control_group.setLayout(control_layout)
        main_layout.addWidget(self.control_group)
        
        # === TIMING INFO GROUP ===
        timing_group = QGroupBox("Timing Information")
        timing_layout = QGridLayout()

        timing_layout.addWidget(QLabel("Period:"), 0, 0)
        self.period_label = QLabel("--")
        self.period_label.setStyleSheet("font-weight: bold;")
        timing_layout.addWidget(self.period_label, 0, 1)

        timing_layout.addWidget(QLabel("Laser Pulse Width:"), 1, 0)
        self.laser_pulse_label = QLabel("--")
        self.laser_pulse_label.setStyleSheet("font-weight: bold;")
        timing_layout.addWidget(self.laser_pulse_label, 1, 1)

        timing_layout.addWidget(QLabel("Integration Time:"), 2, 0)
        self.integration_time_label = QLabel("--")
        self.integration_time_label.setStyleSheet("font-weight: bold;")
        timing_layout.addWidget(self.integration_time_label, 2, 1)

        timing_layout.addWidget(QLabel("Data Transfer:"), 3, 0)
        self.data_transfer_label = QLabel("12.00 ms")
        self.data_transfer_label.setStyleSheet("font-weight: bold;")
        timing_layout.addWidget(self.data_transfer_label, 3, 1)

        self.timing_status_label = QLabel("")
        self.timing_status_label.setWordWrap(True)
        timing_layout.addWidget(self.timing_status_label, 4, 0, 1, 2)

        timing_group.setLayout(timing_layout)
        main_layout.addWidget(timing_group)

        # Add logo at bottom left (consistent with other tabs)
        logo_label = QLabel()
        logo_path = resource_path(os.path.join("assets", "RAYOPS_logo.jpg"))
        logo_pixmap = QPixmap(logo_path)
        if not logo_pixmap.isNull():
            scaled_logo = logo_pixmap.scaledToWidth(280, Qt.SmoothTransformation)
            logo_label.setPixmap(scaled_logo)
            logo_label.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
            logo_label.setStyleSheet("padding: 5px;")
            main_layout.addWidget(logo_label)

        # Add stretch at bottom
        main_layout.addStretch()

        # Initialize timing display
        self.update_timing_display()
        
    def refresh_ports(self):
        """Refresh available serial ports"""
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()
        for port in ports:
            self.port_combo.addItem(f"{port.device} - {port.description}")
        
        if self.port_combo.count() == 0:
            self.port_combo.addItem("No ports found")
            
    def toggle_connection(self):
        """Connect/disconnect to Arduino"""
        if not self.connected:
            # Get selected port
            port_text = self.port_combo.currentText()
            if "No ports found" in port_text and not SIMULATION_MODE:
                QMessageBox.warning(self, "No Ports", "No serial ports available!")
                return

            port = port_text.split(" - ")[0] if " - " in port_text else "SIMULATED"

            try:
                if SIMULATION_MODE:
                    # Use simulated Arduino
                    self.arduino = ArduinoTriggerSimulator(port)
                    self.connected = True
                    self.conn_status.setText(f"SIMULATED - Arduino")
                    self.conn_status.setStyleSheet("color: orange; font-weight: bold;")
                    self.connect_btn.setText("Disconnect")
                    self.connect_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 8px;")
                    self.external_mode_radio.setEnabled(True)
                    self.status_update.emit(f"🔧 SIMULATION: Arduino connected (simulated)")

                    # Reset trigger status UI to initial state
                    self.reset_trigger_status_ui()

                    # Register simulator for disconnect testing
                    from hardware_simulator import register_arduino_simulator
                    register_arduino_simulator(self.arduino)

                    # Start connection check timer
                    self.connection_check_timer.start()

                    # Restore external mode if it was saved (now that we're connected)
                    if self.settings and self.settings.contains('arduino_external_mode'):
                        if self.settings.value('arduino_external_mode', False, type=bool):
                            self.external_mode_radio.setChecked(True)

                    # Refresh trigger mode UI (enables params if external mode was saved)
                    self.on_trigger_mode_changed()

                    # Send stop command to ensure safe state on connection
                    self.arduino.stop()
                    self.status_update.emit("✓ Stop command sent (safe state)")

                    # Apply initial parameters
                    self.apply_parameters()
                else:
                    # Real Arduino connection
                    self.arduino = ArduinoTriggerController(port)
                    self.connected = True
                    self.conn_status.setText(f"Connected to {port}")
                    self.conn_status.setStyleSheet("color: green; font-weight: bold;")
                    self.connect_btn.setText("Disconnect")
                    self.connect_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 8px;")
                    self.external_mode_radio.setEnabled(True)
                    self.status_update.emit(f"Arduino connected on {port}")

                    for i in range(3):
                        self.arduino.ser.write(b'Q')
                        time.sleep(0.1)
                        self.arduino.ser.reset_input_buffer()

                    # Final cleanup
                    time.sleep(0.5)
                    self.arduino.ser.reset_input_buffer()

                    # Reset trigger status UI to initial state
                    self.reset_trigger_status_ui()

                    # Start connection check timer
                    self.connection_check_timer.start()

                    # Restore external mode if it was saved (now that we're connected)
                    if self.settings and self.settings.contains('arduino_external_mode'):
                        if self.settings.value('arduino_external_mode', False, type=bool):
                            self.external_mode_radio.setChecked(True)

                    # Refresh trigger mode UI (enables params if external mode was saved)
                    self.on_trigger_mode_changed()

                    # Send stop command to ensure safe state on connection
                    self.arduino.stop()
                    self.status_update.emit("✓ Stop command sent (safe state)")

                    # Apply initial parameters
                    self.apply_parameters()

            except Exception as e:
                QMessageBox.critical(self, "Connection Error", f"Failed to connect to Arduino:\n{str(e)}")
                self.conn_status.setText("Connection failed")
                self.conn_status.setStyleSheet("color: red; font-weight: bold;")
                
        else:
            # Disconnect
            # Stop timers first
            self.connection_check_timer.stop()
            self.status_timer.stop()

            if self.arduino:
                try:
                    self.arduino.stop()  # Stop triggers first
                except:
                    pass
                self.arduino.close()

            self.arduino = None
            self.connected = False
            self.conn_status.setText("Not connected")
            self.conn_status.setStyleSheet("color: gray; font-weight: bold;")
            self.connect_btn.setText("Connect to Arduino")
            self.connect_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px;")
            self.external_mode_radio.setEnabled(False)
            self.software_mode_radio.setChecked(True)  # Revert to software mode

            # Update trigger status to DISCONNECTED
            self.trigger_status.setStyleSheet("color: gray;")
            self.trigger_status_text.setText("DISCONNECTED")
            self.trigger_status_text.setStyleSheet("font-weight: bold; color: gray;")

            self.status_update.emit("Arduino disconnected")
            
    def on_trigger_mode_changed(self, checked=True):
        """Handle trigger mode change"""
        # Only process when a button is checked (not unchecked)
        if not checked:
            return

        is_external = self.external_mode_radio.isChecked()

        # Update UI
        self.params_group.setEnabled(is_external and self.connected)
        self.control_group.setEnabled(is_external and self.connected)

        # Enable/disable start button based on mode (only in External mode)
        self.start_btn.setEnabled(is_external and self.connected)

        if is_external:
            self.mode_status.setText("EXTERNAL (Arduino)")
            self.mode_status.setStyleSheet("font-weight: bold; color: green;")
        else:
            self.mode_status.setText("SOFTWARE")
            self.mode_status.setStyleSheet("font-weight: bold; color: blue;")

        # Emit signal for main GUI to handle
        self.trigger_mode_changed.emit(is_external)

        if is_external:
            self.status_update.emit("External trigger mode enabled - Arduino will control both devices")
        else:
            self.status_update.emit("Software trigger mode enabled - Individual device control")

    def reset_trigger_status_ui(self):
        """Reset trigger status UI to initial stopped state (called on connect/reconnect)"""
        # Reset trigger status indicator
        self.trigger_status.setStyleSheet("color: red;")
        self.trigger_status_text.setText("STOPPED")
        self.trigger_status_text.setStyleSheet("font-weight: bold; color: red;")

        # Reset cycle counter
        self.cycle_counter.setText("0")

        # Reset start/stop buttons - only enable start if in External mode
        is_external = self.external_mode_radio.isChecked()
        self.start_btn.setEnabled(is_external)
        self.stop_btn.setEnabled(False)

    def on_param_changed(self):
        """Update timing display when parameters change"""
        self.update_timing_display()

    def on_offset_unit_changed(self):
        """Handle offset unit change between µs and ms"""
        current_value = self.offset.value()
        if self.offset_unit.currentText() == "ms":
            # Switching to ms: divide by 1000 and adjust range
            self.offset.setRange(-32, 32)  # ±32ms range
            self.offset.setValue(current_value // 1000)
        else:
            # Switching to µs: multiply by 1000 and adjust range
            self.offset.setRange(-32767, 32767)
            self.offset.setValue(current_value * 1000)

    def get_offset_in_us(self):
        """Get offset value converted to microseconds"""
        value = self.offset.value()
        if self.offset_unit.currentText() == "ms":
            return value * 1000
        return value
        
    def update_timing_display(self):
        """Update the timing information labels"""
        DATA_TRANSFER_MS = 12.0

        freq = self.frequency.value()
        duty_laser = self.duty_laser.value()
        period_ms = 1000.0 / freq if freq > 0 else 0
        laser_pulse_ms = period_ms * duty_laser / 100.0

        # Retrieve integration time from the spectrometer tab on the main window
        parent = self.parent()
        while parent and not hasattr(parent, 'integration_time'):
            parent = parent.parent()
        integration_ms = parent.integration_time.value() if parent else None

        self.period_label.setText(f"{period_ms:.2f} ms")
        self.laser_pulse_label.setText(f"{laser_pulse_ms:.2f} ms")

        if integration_ms is not None:
            self.integration_time_label.setText(f"{integration_ms:.2f} ms")
            cycle_needed = integration_ms + DATA_TRANSFER_MS
            if period_ms > 0 and cycle_needed > period_ms:
                self.timing_status_label.setText(
                    f"⚠️ Some scans will be missed\n"
                    f"({integration_ms:.1f} + {DATA_TRANSFER_MS:.0f} ms > {period_ms:.1f} ms period)"
                )
                self.timing_status_label.setStyleSheet("color: orange; font-weight: bold;")
            elif period_ms > 0:
                self.timing_status_label.setText(
                    f"✓ All pulses should correspond to a scan\n"
                    f"({integration_ms:.1f} + {DATA_TRANSFER_MS:.0f} ms < {period_ms:.1f} ms period)"
                )
                self.timing_status_label.setStyleSheet("color: green; font-weight: bold;")
            else:
                self.timing_status_label.setText("")
        else:
            self.integration_time_label.setText("-- (connect spectrometer)")
            self.timing_status_label.setText("")
        
    def apply_parameters(self):
        """Apply parameters to Arduino"""
        if not self.connected or not self.arduino:
            return

        try:
            # Calculate duty cycle for fixed 1ms spec trigger width
            freq = self.frequency.value()
            period_ms = 1000.0 / freq if freq > 0 else 1000.0
            duty_spec = min(100, max(1, int((self.SPEC_TRIGGER_WIDTH_MS / period_ms) * 100)))

            # cycles = total spec pulses (from spectrometer tab num_scans)
            # laser_cycles = laser-only pulses (from this tab's spinbox)
            parent = self.parent()
            while parent and not hasattr(parent, 'num_scans'):
                parent = parent.parent()
            spec_scans = parent.num_scans.value() if parent else 0

            # AvaSpec single-scan trigger mode needs ~2 flush pulses before
            # it starts capturing valid data. Send 2 extra pulses so the
            # spectrometer collects exactly the requested number of scans.
            SDK_FLUSH_PULSES = 2
            cycles = (spec_scans + SDK_FLUSH_PULSES) if spec_scans > 0 else 0
            laser_cycles = self.num_triggers.value()  # 0 = fire for all spec scans

            response = self.arduino.set_all_params(
                cycles,
                laser_cycles,
                freq,
                duty_spec,
                self.duty_laser.value(),
                self.get_offset_in_us()
            )
            if response and response.startswith('OK'):
                self.status_update.emit("Parameters applied successfully")
            else:
                self.status_update.emit(f"Parameter update: {response}")
                
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to apply parameters:\n{str(e)}")
            
    def start_triggers(self):
        """Start Arduino trigger generation"""
        if not self.connected or not self.arduino:
            return

        # Check if we have access to driver tab for validation
        parent_widget = self.parent()
        while parent_widget and not hasattr(parent_widget, 'driver_tab'):
            parent_widget = parent_widget.parent()

        if parent_widget and hasattr(parent_widget, 'driver_tab'):
            driver_tab = parent_widget.driver_tab

            # Check if driver is connected and in correct mode
            if driver_tab.driver.is_connected:
                # Check for active protections before starting triggers
                if driver_tab.current_locks:
                    active_protections = []
                    if driver_tab.current_locks.get('interlock', False):
                        active_protections.append('Interlock')
                    if driver_tab.current_locks.get('crowbar', False):
                        active_protections.append('Crowbar')
                    if driver_tab.current_locks.get('overcurrent', False):
                        active_protections.append('Overcurrent')
                    if driver_tab.current_locks.get('overheat', False):
                        active_protections.append('Overheat')
                    if driver_tab.current_locks.get('ntc_interlock', False):
                        active_protections.append('NTC Interlock')

                    if active_protections:
                        QMessageBox.critical(
                            self,
                            "Protection Active",
                            f"Cannot start triggers - driver protection active:\n\n"
                            f"{', '.join(active_protections)}\n\n"
                            "Clear the protection before starting triggers."
                        )
                        return

                # Check if driver is already started using Internal Enable mode
                if driver_tab.internal_enable_radio.isChecked():
                    if driver_tab.current_state and driver_tab.current_state.get('started', False):
                        QMessageBox.critical(
                            self,
                            "Driver Already Running",
                            "Cannot start triggers - driver is already running in Internal Enable mode.\n\n"
                            "Stop the driver output first, or switch to External Enable mode."
                        )
                        return

                # Check External Enable mode
                if not driver_tab.external_enable_radio.isChecked():
                    reply = QMessageBox.warning(
                        self,
                        "Driver Configuration Warning",
                        "The laser driver should be set to 'External Enable' mode for Arduino synchronization.\n\n"
                        "Current mode: Internal Enable\n\n"
                        "Continue anyway?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No
                    )
                    if reply == QMessageBox.No:
                        return
                
                # Check CW mode (QCW not supported with external trigger)
                if not driver_tab.cw_radio.isChecked():
                    QMessageBox.critical(
                        self,
                        "Invalid Driver Configuration",
                        "The laser driver MUST be in CW (Continuous) mode for external trigger operation.\n\n"
                        "QCW mode is not compatible with Arduino external triggering.\n\n"
                        "Please switch to CW mode in the Driver Control tab."
                    )
                    return
                    
                # Send laser parameters before starting triggers
                current = driver_tab.current_setpoint.value()
                if driver_tab.driver.set_current(current):
                    self.status_update.emit(f"Laser current set to {current:.2f} A")
                else:
                    self.status_update.emit("Warning: Failed to set laser current")

                self.status_update.emit("Driver configuration verified: External Enable + CW mode")
            else:
                # Driver not connected - warn but allow (user might not be using laser)
                reply = QMessageBox.question(
                    self,
                    "Driver Not Connected",
                    "The laser driver is not connected.\n\n"
                    "Arduino will generate triggers for both spectrometer and laser,\n"
                    "but the laser will not respond.\n\n"
                    "Continue?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if reply == QMessageBox.No:
                    return

        try:
            # Apply all parameters to Arduino before starting
            self.apply_parameters()

            # CRITICAL: Arm spectrometer BEFORE starting Arduino triggers
            # This ensures spectrometer is ready to capture the first trigger
            if self.auto_start_checkbox.isChecked() and self.external_mode_radio.isChecked():
                # Get the number of scans requested in spectrometer tab
                parent_widget = self.parent()
                while parent_widget and not hasattr(parent_widget, 'num_scans'):
                    parent_widget = parent_widget.parent()
                
                if parent_widget and hasattr(parent_widget, 'num_scans'):
                    spec_scans = parent_widget.num_scans.value()
                    laser_pulses = self.num_triggers.value()

                    if laser_pulses > 0 and laser_pulses > spec_scans:
                        post = laser_pulses - spec_scans
                        self.status_update.emit(
                            f"ℹ️ {spec_scans} spec scans + {post} extra laser pulses after spec stops"
                        )
                    elif laser_pulses > 0 and laser_pulses < spec_scans:
                        post = spec_scans - laser_pulses
                        self.status_update.emit(f"ℹ️ {laser_pulses} laser scans + {post} post-laser spec scans")
                
                self.status_update.emit("Auto-start enabled: Arming spectrometer FIRST...")
                # Emit signal to start spectrometer measurement (it will wait for triggers)
                self.auto_start_spectrometer.emit()
                # Give spectrometer time to arm and enter waiting state
                time.sleep(1.0)
            
            # NOW start Arduino triggers - spectrometer is already armed and waiting
            response = self.arduino.start()
            
            if response and response.startswith('OK'):
                self.start_btn.setEnabled(False)
                self.stop_btn.setEnabled(True)
                self.trigger_status.setStyleSheet("color: green;")
                self.trigger_status_text.setText("RUNNING")
                self.trigger_status_text.setStyleSheet("font-weight: bold; color: green;")
                self.status_timer.start(100)  # Update every 100ms
                self.status_update.emit("External triggers STARTED")

                # Update driver status to STARTED (External) when in external enable mode
                # Re-traverse parent hierarchy to find driver_tab
                main_window = self.parent()
                while main_window and not hasattr(main_window, 'driver_tab'):
                    main_window = main_window.parent()

                if main_window and hasattr(main_window, 'driver_tab'):
                    driver_tab = main_window.driver_tab
                    if driver_tab.driver.is_connected:
                        if driver_tab.external_enable_radio.isChecked():
                            driver_tab.enable_status.setStyleSheet("color: green;")
                            driver_tab.enable_status_text.setText("STARTED (External)")
                            driver_tab.enable_status_text.setStyleSheet("font-weight: bold; color: green;")
                            # Disable enable mode switches while triggers are running
                            driver_tab.external_enable_radio.setEnabled(False)
                            driver_tab.internal_enable_radio.setEnabled(False)
                            self.status_update.emit("Driver mode locked (External Enable)")
                        else:
                            self.status_update.emit("⚠️ Driver is in Internal Enable mode")
                    else:
                        self.status_update.emit("⚠️ Driver not connected")
                else:
                    self.status_update.emit("⚠️ Could not find driver tab")
            else:
                QMessageBox.warning(self, "Error", f"Failed to start triggers:\n{response}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to start triggers:\n{str(e)}")
            
    def stop_triggers(self):
        """Stop Arduino trigger generation"""
        if not self.connected or not self.arduino:
            return

        try:
            response = self.arduino.stop()

            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.trigger_status.setStyleSheet("color: red;")
            self.trigger_status_text.setText("STOPPED")
            self.trigger_status_text.setStyleSheet("font-weight: bold; color: red;")
            self.status_timer.stop()
            self.status_update.emit(f"External triggers STOPPED - {response}")

            # Update driver status to STOPPED when in external enable mode
            parent_widget = self.parent()
            while parent_widget and not hasattr(parent_widget, 'driver_tab'):
                parent_widget = parent_widget.parent()

            if parent_widget and hasattr(parent_widget, 'driver_tab'):
                driver_tab = parent_widget.driver_tab
                if driver_tab.driver.is_connected and driver_tab.external_enable_radio.isChecked():
                    driver_tab.enable_status.setStyleSheet("color: red;")
                    driver_tab.enable_status_text.setText("STOPPED")
                    driver_tab.enable_status_text.setStyleSheet("font-weight: bold; color: red;")
                    # Re-enable enable mode switches when triggers stop
                    driver_tab.external_enable_radio.setEnabled(True)
                    driver_tab.internal_enable_radio.setEnabled(True)

            # Stop the spectrometer measurement thread — triggers are gone, no more
            # scans will arrive, so the acquisition loop must not wait for a timeout.
            # Call mt.stop() directly to avoid mutual recursion with stop_measurement().
            if parent_widget and hasattr(parent_widget, 'measurement_thread'):
                mt = parent_widget.measurement_thread
                if mt and mt.isRunning():
                    mt.stop()

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to stop triggers:\n{str(e)}")

    def check_connection(self):
        """Periodic connection check (runs even when triggers aren't active)"""
        if not self.connected or not self.arduino:
            self.connection_check_timer.stop()
            return

        # Check if Arduino is still connected
        if hasattr(self.arduino, 'is_connected') and not self.arduino.is_connected:
            self.on_connection_lost()
            return

        # For real hardware, try a quick status check
        if hasattr(self.arduino, 'is_port_open') and not self.arduino.is_port_open():
            self.on_connection_lost()
            return

        # Active probe when triggers are idle: pyserial's is_open stays True on Windows
        # after a USB pull until the next actual I/O.  A Q query here costs ~1 byte and
        # makes the disconnect visible within one timer interval (500 ms).
        if not SIMULATION_MODE and not self.status_timer.isActive():
            self.arduino.get_status()
            if hasattr(self.arduino, 'is_connected') and not self.arduino.is_connected:
                self.on_connection_lost()

    def update_status_display(self):
        """Update status display (called by timer)"""
        if not self.connected or not self.arduino:
            return

        try:
            # Check if Arduino is still connected
            if hasattr(self.arduino, 'is_connected') and not self.arduino.is_connected:
                self.on_connection_lost()
                return

            status = self.arduino.get_status()

            # Check for disconnection during get_status
            if hasattr(self.arduino, 'is_connected') and not self.arduino.is_connected:
                self.on_connection_lost()
                return

            if status:
                self.cycle_counter.setText(str(status['cycles']))

                # Auto-stop if not running
                if not status['running']:
                    self.start_btn.setEnabled(True)
                    self.stop_btn.setEnabled(False)
                    self.trigger_status.setStyleSheet("color: red;")
                    self.trigger_status_text.setText("STOPPED")
                    self.trigger_status_text.setStyleSheet("font-weight: bold; color: red;")
                    self.status_timer.stop()

        except (serial.SerialException, OSError):
            self.on_connection_lost()
        except Exception as e:
            self.status_update.emit(f"Status update error: {str(e)}")

    def on_connection_lost(self):
        """Handle unexpected USB disconnection"""
        # Stop timers
        self.status_timer.stop()
        self.connection_check_timer.stop()

        # Stop the measurement thread immediately — no more triggers will arrive
        parent = self.parent()
        while parent and not hasattr(parent, 'measurement_thread'):
            parent = parent.parent()
        if parent and hasattr(parent, 'measurement_thread'):
            mt = parent.measurement_thread
            if mt and mt.isRunning():
                mt.stop()

        # Update connection state
        self.connected = False

        # Update UI to show disconnected state
        self.conn_status.setText("CONNECTION LOST")
        self.conn_status.setStyleSheet("color: red; font-weight: bold;")
        self.connect_btn.setText("Connect to Arduino")
        self.connect_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px;")

        # Update trigger status
        self.trigger_status.setStyleSheet("color: gray;")
        self.trigger_status_text.setText("DISCONNECTED")
        self.trigger_status_text.setStyleSheet("font-weight: bold; color: gray;")

        # Disable controls
        self.external_mode_radio.setEnabled(False)
        self.software_mode_radio.setChecked(True)
        self.params_group.setEnabled(False)
        self.control_group.setEnabled(False)

        # Clean up Arduino object
        if self.arduino:
            try:
                self.arduino.close()
            except Exception:
                pass
        self.arduino = None

        self.status_update.emit("Arduino USB connection lost - please reconnect")
            
    def is_external_mode(self):
        """Check if external trigger mode is active"""
        return self.external_mode_radio.isChecked()
    
    def get_trigger_frequency(self):
        """Get current trigger frequency (for measurement timing)"""
        return self.frequency.value()
    
    def cleanup(self):
        """Cleanup on close"""
        self.save_settings()
        self.status_timer.stop()
        self.connection_check_timer.stop()
        if self.connected and self.arduino:
            try:
                self.arduino.stop()
                self.arduino.close()
            except:
                pass
    
    def load_settings(self):
        """Load Arduino parameters from settings"""
        try:
            if self.settings.contains('arduino_port'):
                port = self.settings.value('arduino_port', '', type=str)
                index = self.port_combo.findText(port)
                if index >= 0:
                    self.port_combo.setCurrentIndex(index)

            if self.settings.contains('arduino_frequency'):
                freq = self.settings.value('arduino_frequency', 100, type=int)
                self.frequency.setValue(freq)

            if self.settings.contains('arduino_num_triggers'):
                num = self.settings.value('arduino_num_triggers', 0, type=int)
                self.num_triggers.setValue(num)

            # duty_spec is now fixed at 1ms, no longer loaded from settings

            if self.settings.contains('arduino_duty_laser'):
                duty = self.settings.value('arduino_duty_laser', 50, type=int)
                self.duty_laser.setValue(duty)

            # Load offset unit first, then offset value
            if self.settings.contains('arduino_offset_unit'):
                unit = self.settings.value('arduino_offset_unit', 'µs', type=str)
                index = self.offset_unit.findText(unit)
                if index >= 0:
                    self.offset_unit.setCurrentIndex(index)

            if self.settings.contains('arduino_offset'):
                offset = self.settings.value('arduino_offset', 0, type=int)
                self.offset.setValue(offset)

            if self.settings.contains('arduino_external_mode'):
                is_external = self.settings.value('arduino_external_mode', True, type=bool)
                # Only restore external mode if Arduino is connected
                # Otherwise always default to software mode
                if is_external and self.connected:
                    self.external_mode_radio.setChecked(True)
                else:
                    self.software_mode_radio.setChecked(True)

            if self.settings.contains('arduino_auto_start'):
                auto_start = self.settings.value('arduino_auto_start', False, type=bool)
                self.auto_start_checkbox.setChecked(auto_start)

        except Exception as e:
            pass  # Silently ignore settings load errors
    
    def save_settings(self):
        """Save Arduino parameters to settings"""
        if not self.settings:
            return

        try:
            self.settings.setValue('arduino_port', self.port_combo.currentText())
            self.settings.setValue('arduino_frequency', self.frequency.value())
            self.settings.setValue('arduino_num_triggers', self.num_triggers.value())
            # duty_spec is now fixed at 1ms, no longer saved
            self.settings.setValue('arduino_duty_laser', self.duty_laser.value())
            self.settings.setValue('arduino_offset', self.offset.value())
            self.settings.setValue('arduino_offset_unit', self.offset_unit.currentText())
            self.settings.setValue('arduino_external_mode', self.external_mode_radio.isChecked())
            self.settings.setValue('arduino_auto_start', self.auto_start_checkbox.isChecked())
        except Exception as e:
            pass  # Silently ignore settings save errors
