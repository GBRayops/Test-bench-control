"""
SF6100 Driver Control Tab
GUI interface for controlling Maiman Electronics SF6100 laser driver
WITH THREADED MONITORING for non-blocking operation
"""
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QComboBox, QGroupBox, QGridLayout,
                             QDoubleSpinBox, QRadioButton, QButtonGroup, QSpinBox)
from PySide6.QtCore import QTimer, Signal, Qt, QThread
from PySide6.QtGui import QFont, QPixmap
import os
import sys
try:
    from Spectro_diode.sf6100_serial import SF6100Serial
except:
    from sf6100_serial import SF6100Serial

# Check for simulation mode
SIMULATION_MODE = '--simulate' in sys.argv
if SIMULATION_MODE:
    from Spectro_diode.hardware_simulator import SF6100Simulator


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    # Get the project root (parent of src folder)
    src_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(src_dir)
    return os.path.join(project_root, relative_path)


class DriverMonitorThread(QThread):
    """Separate thread for monitoring driver status without blocking GUI"""

    # Signals to update GUI
    measurements_ready = Signal(dict)  # current, voltage, temps
    state_ready = Signal(dict)  # started, etc.
    locks_ready = Signal(dict)  # protection status
    error = Signal(str)
    connection_lost = Signal()  # Emitted when USB disconnected

    def __init__(self, driver):
        super().__init__()
        self.driver = driver
        self.running = False
        self.update_interval_ms = 100  # 100ms between cycles
        self._was_connected = False
        self._cycle = 0

    def run(self):
        """Main monitoring loop - runs in separate thread.

        Fast path (every cycle): state + lock queries for interlock detection.
        Slow path (every 3rd cycle): measurement queries (current, voltage, temps).
        This keeps protection polling responsive while reducing serial bus load.
        """
        import time
        import serial
        self.running = True

        while self.running:
            currently_connected = self.driver.is_connected

            if self._was_connected and not currently_connected:
                self._was_connected = False
                self.connection_lost.emit()
                continue

            self._was_connected = currently_connected

            if not currently_connected:
                time.sleep(0.1)
                continue

            try:
                if not self.driver.is_connected or not self.running:
                    break

                self._cycle += 1

                # --- Fast path: state + locks every cycle ---
                if self.driver.is_connected and self.running:
                    state = self.driver.get_state()
                    if state:
                        self.state_ready.emit(state)

                if self.driver.is_connected and self.running:
                    locks = self.driver.get_lock_status()
                    if locks:
                        self.locks_ready.emit(locks)

                # --- Slow path: measurements every 3rd cycle ---
                if self._cycle % 3 == 0 and self.driver.is_connected and self.running:
                    measurements = {
                        'current': self.driver.get_current_measured(),
                        'voltage': self.driver.get_voltage_measured(),
                        'pcb_temp': self.driver.get_pcb_temperature(),
                        'ntc_temp': self.driver.get_ntc_temperature()
                    }

                    if not self.driver.is_connected:
                        self._was_connected = False
                        self.connection_lost.emit()
                        continue

                    if not self.running:
                        break

                    self.measurements_ready.emit(measurements)

            except (AttributeError, serial.SerialException, OSError):
                if self.running and self._was_connected:
                    self._was_connected = False
                    self.connection_lost.emit()
                break
            except Exception as e:
                if self.running:
                    self.error.emit(f"Monitor error: {e}")

            time.sleep(self.update_interval_ms / 1000.0)
    
    def stop(self):
        """Stop the monitoring thread"""
        self.running = False


class DriverControlTab(QWidget):
    """SF6100 Driver Control Tab Widget"""

    # Signals for status updates
    status_update = Signal(str)
    protection_activated = Signal(list)  # Emitted when protection(s) become active
    
    def __init__(self, settings=None):
        super().__init__()
        # Use simulator or real driver based on mode
        if SIMULATION_MODE:
            self.driver = SF6100Simulator()
        else:
            self.driver = SF6100Serial()
        self.settings = settings

        # State tracking
        self.current_state = None
        self.current_locks = None  # Track protection status for start prevention
        self.ntc_interlock_allowed = True  # Track NTC interlock allow/deny state
        # Track previous protection states for rising edge detection (auto-stop)
        self.previous_protection_states = {
            'interlock': False,
            'crowbar': False,
            'overcurrent': False,
            'overheat': False,
            'ntc_interlock': False
        }
        # NTC hysteresis: require this many consecutive active readings before acting
        self._ntc_consecutive = 0
        self._NTC_HYSTERESIS = 3

        # Replace QTimer with monitoring thread
        self.monitor_thread = None


        self.initUI()

        # Load saved settings
        if self.settings:
            self.load_settings()
        
    def initUI(self):
        """Initialize the user interface"""
        from PySide6.QtWidgets import QScrollArea
        
        # Create scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # Create container widget for scroll area
        container = QWidget()
        main_layout = QVBoxLayout()
        container.setLayout(main_layout)
        
        # Set container as scroll area widget
        scroll.setWidget(container)
        
        # Set scroll area as main layout
        tab_layout = QVBoxLayout()
        tab_layout.addWidget(scroll)
        self.setLayout(tab_layout)
        
        # === CONNECTION GROUP ===
        conn_group = QGroupBox("Driver Connection")
        conn_layout = QGridLayout()
        
        # Serial port selection
        conn_layout.addWidget(QLabel("Serial Port:"), 0, 0)
        self.port_combo = QComboBox()
        self.refresh_ports()
        conn_layout.addWidget(self.port_combo, 0, 1)
        
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_ports)
        conn_layout.addWidget(self.refresh_btn, 0, 2)
        
        # Baud rate
        conn_layout.addWidget(QLabel("Baud Rate:"), 1, 0)
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["115200", "57600", "19200", "9600"])
        conn_layout.addWidget(self.baud_combo, 1, 1)
        
        # Connect button
        self.connect_btn = QPushButton("Connect to Driver")
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.connect_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px;")
        conn_layout.addWidget(self.connect_btn, 2, 0, 1, 3)
        
        # Connection status
        self.conn_status = QLabel("Not connected")
        self.conn_status.setStyleSheet("color: gray;")
        conn_layout.addWidget(self.conn_status, 3, 0, 1, 3)
        
        conn_group.setLayout(conn_layout)
        main_layout.addWidget(conn_group)
        
        # === ENABLE CONTROL GROUP ===
        enable_group = QGroupBox("Enable Control")
        enable_layout = QVBoxLayout()
        
        # Enable mode selection
        mode_label = QLabel("Enable Mode:")
        mode_label.setStyleSheet("font-weight: bold;")
        enable_layout.addWidget(mode_label)
        
        self.enable_mode_group = QButtonGroup()
        
        self.external_enable_radio = QRadioButton("External Enable (Hardware Pin)")
        self.external_enable_radio.setChecked(True)
        self.external_enable_radio.toggled.connect(self.on_enable_mode_changed)
        self.enable_mode_group.addButton(self.external_enable_radio, 0)
        enable_layout.addWidget(self.external_enable_radio)
        
        self.internal_enable_radio = QRadioButton("Internal Enable (Software Control)")
        self.internal_enable_radio.toggled.connect(self.on_enable_mode_changed)
        self.enable_mode_group.addButton(self.internal_enable_radio, 1)
        enable_layout.addWidget(self.internal_enable_radio)
        
        # Status display
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("Status:"))
        self.enable_status = QLabel("●")
        self.enable_status.setFont(QFont("Arial", 16))
        self.enable_status.setStyleSheet("color: gray;")
        status_layout.addWidget(self.enable_status)
        self.enable_status_text = QLabel("DISCONNECTED")
        self.enable_status_text.setStyleSheet("font-weight: bold; color: gray;")
        status_layout.addWidget(self.enable_status_text)
        status_layout.addStretch()
        enable_layout.addLayout(status_layout)
        
        # Start/Stop buttons
        btn_layout = QHBoxLayout()

        self.start_btn = QPushButton("START OUTPUT")
        self.start_btn.clicked.connect(self.start_output)
        self.start_btn.setEnabled(False)
        self.start_btn.setStyleSheet("""
            QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 12px; font-size: 14px; }
            QPushButton:disabled { background-color: #a0a0a0; color: #606060; }
        """)
        btn_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("STOP OUTPUT")
        self.stop_btn.clicked.connect(self.stop_output)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("""
            QPushButton { background-color: #f44336; color: white; font-weight: bold; padding: 12px; font-size: 14px; }
            QPushButton:disabled { background-color: #a0a0a0; color: #606060; }
        """)
        btn_layout.addWidget(self.stop_btn)
        
        enable_layout.addLayout(btn_layout)
        
        enable_group.setLayout(enable_layout)
        main_layout.addWidget(enable_group)
        
        # === CURRENT CONTROL GROUP ===
        current_group = QGroupBox("Current Control (Internal)")
        current_layout = QVBoxLayout()
        
        # Current setpoint
        setpoint_layout = QHBoxLayout()
        self.current_label = QLabel("Current Setpoint:")
        setpoint_layout.addWidget(self.current_label)
        
        self.current_setpoint = QDoubleSpinBox()
        self.current_setpoint.setRange(0.0, 20.0)
        self.current_setpoint.setValue(0.0)
        self.current_setpoint.setDecimals(2)
        self.current_setpoint.setSuffix(" A")
        self.current_setpoint.setEnabled(False)
        setpoint_layout.addWidget(self.current_setpoint)

        current_layout.addLayout(setpoint_layout)

        # Current limits display (populated on connection)
        self.current_limits_label = QLabel("Limits: -- A")
        self.current_limits_label.setStyleSheet("color: gray; font-style: italic;")
        current_layout.addWidget(self.current_limits_label)

        current_group.setLayout(current_layout)
        main_layout.addWidget(current_group)
        
        # === OUTPUT MODE GROUP ===
        mode_group = QGroupBox("Output Mode")
        mode_layout = QVBoxLayout()
        
        # CW/QCW selection
        output_mode_layout = QHBoxLayout()
        
        self.output_mode_group = QButtonGroup()
        
        self.cw_radio = QRadioButton("CW (Continuous)")
        self.cw_radio.setChecked(True)
        self.cw_radio.toggled.connect(self.on_output_mode_changed)
        self.output_mode_group.addButton(self.cw_radio, 0)
        output_mode_layout.addWidget(self.cw_radio)
        
        self.qcw_radio = QRadioButton("QCW (Pulsed)")
        self.qcw_radio.toggled.connect(self.on_output_mode_changed)
        self.output_mode_group.addButton(self.qcw_radio, 1)
        output_mode_layout.addWidget(self.qcw_radio)
        
        mode_layout.addLayout(output_mode_layout)
        
        # QCW Parameters
        qcw_params = QGridLayout()
        
        # Frequency
        self.freq_label = QLabel("Frequency:")
        self.freq_label.setEnabled(False)
        qcw_params.addWidget(self.freq_label, 0, 0)
        
        self.frequency = QDoubleSpinBox()
        self.frequency.setRange(0.1, 500.0)
        self.frequency.setValue(10.0)
        self.frequency.setDecimals(1)
        self.frequency.setSuffix(" Hz")
        self.frequency.setEnabled(False)
        qcw_params.addWidget(self.frequency, 0, 1)
        
        # Duration
        self.dur_label = QLabel("Pulse Duration:")
        self.dur_label.setEnabled(False)
        qcw_params.addWidget(self.dur_label, 1, 0)
        
        self.duration = QDoubleSpinBox()
        self.duration.setRange(2.0, 5000.0)  # 2 ms min per SF6100 digital protocol spec
        self.duration.setValue(10.0)
        self.duration.setDecimals(1)
        self.duration.setSuffix(" ms")
        self.duration.setEnabled(False)
        qcw_params.addWidget(self.duration, 1, 1)

        # Connect frequency change to update duration limit
        self.frequency.valueChanged.connect(self.on_frequency_changed)

        mode_layout.addLayout(qcw_params)
        mode_group.setLayout(mode_layout)
        main_layout.addWidget(mode_group)
        
        # === MEASUREMENTS GROUP ===
        meas_group = QGroupBox("Real-Time Measurements")
        meas_layout = QGridLayout()
        
        self.meas_labels = {}
        measurements = [
            ('Output Current', 'A'),
            ('Output Voltage', 'V'),
            ('PCB Temperature', '°C'),
            ('NTC Temperature', '°C')
        ]
        
        for i, (name, unit) in enumerate(measurements):
            label = QLabel(f"{name}:")
            meas_layout.addWidget(label, i, 0)
            
            value_label = QLabel("--")
            value_label.setStyleSheet("background-color: #f0f0f0; padding: 5px; border: 1px solid #ccc;")
            value_label.setMinimumWidth(100)
            meas_layout.addWidget(value_label, i, 1)
            
            unit_label = QLabel(unit)
            meas_layout.addWidget(unit_label, i, 2)
            
            self.meas_labels[name] = value_label
        
        meas_group.setLayout(meas_layout)
        main_layout.addWidget(meas_group)
        
        # === PROTECTION STATUS GROUP ===
        protect_group = QGroupBox("Protection Status")
        protect_layout = QGridLayout()
        
        self.protect_labels = {}
        protections = ['Interlock', 'Overcurrent', 'Overheat', 'Crowbar', 'NTC Interlock']
        
        for i, name in enumerate(protections):
            label = QLabel(f"{name}:")
            protect_layout.addWidget(label, i, 0)

            status_label = QLabel("DISCONNECTED")
            status_label.setStyleSheet("color: gray; font-weight: bold;")
            protect_layout.addWidget(status_label, i, 1)

            self.protect_labels[name] = status_label
        
        protect_group.setLayout(protect_layout)
        main_layout.addWidget(protect_group)
        
        # === NTC CONTROL GROUP ===
        ntc_group = QGroupBox("NTC Temperature Control")
        ntc_layout = QVBoxLayout()
        
        # NTC Enable/Disable
        ntc_enable_layout = QHBoxLayout()
        self.allow_ntc_btn = QPushButton("Allow NTC")
        self.allow_ntc_btn.clicked.connect(self.allow_ntc)
        self.allow_ntc_btn.setEnabled(False)
        ntc_enable_layout.addWidget(self.allow_ntc_btn)
        
        self.deny_ntc_btn = QPushButton("Deny NTC")
        self.deny_ntc_btn.clicked.connect(self.deny_ntc)
        self.deny_ntc_btn.setEnabled(False)
        ntc_enable_layout.addWidget(self.deny_ntc_btn)
        ntc_layout.addLayout(ntc_enable_layout)
        
        # NTC Limits
        limits_layout = QGridLayout()
        
        limits_layout.addWidget(QLabel("Lower Limit:"), 0, 0)
        self.ntc_lower = QDoubleSpinBox()
        self.ntc_lower.setRange(-50.0, 150.0)
        self.ntc_lower.setValue(15.0)
        self.ntc_lower.setDecimals(1)
        self.ntc_lower.setSuffix(" °C")
        self.ntc_lower.setEnabled(False)
        limits_layout.addWidget(self.ntc_lower, 0, 1)
        
        self.set_ntc_lower_btn = QPushButton("Set")
        self.set_ntc_lower_btn.clicked.connect(self.set_ntc_lower_limit)
        self.set_ntc_lower_btn.setEnabled(False)
        limits_layout.addWidget(self.set_ntc_lower_btn, 0, 2)
        
        limits_layout.addWidget(QLabel("Upper Limit:"), 1, 0)
        self.ntc_upper = QDoubleSpinBox()
        self.ntc_upper.setRange(-50.0, 150.0)
        self.ntc_upper.setValue(40.0)
        self.ntc_upper.setDecimals(1)
        self.ntc_upper.setSuffix(" °C")
        self.ntc_upper.setEnabled(False)
        limits_layout.addWidget(self.ntc_upper, 1, 1)
        
        self.set_ntc_upper_btn = QPushButton("Set")
        self.set_ntc_upper_btn.clicked.connect(self.set_ntc_upper_limit)
        self.set_ntc_upper_btn.setEnabled(False)
        limits_layout.addWidget(self.set_ntc_upper_btn, 1, 2)
        
        limits_layout.addWidget(QLabel("B25/100 Coefficient:"), 2, 0)
        self.ntc_b_coeff = QSpinBox()
        self.ntc_b_coeff.setRange(2000, 6000)
        self.ntc_b_coeff.setValue(3977)
        self.ntc_b_coeff.setEnabled(False)
        limits_layout.addWidget(self.ntc_b_coeff, 2, 1)
        
        self.set_ntc_b_btn = QPushButton("Set")
        self.set_ntc_b_btn.clicked.connect(self.set_ntc_b_coefficient)
        self.set_ntc_b_btn.setEnabled(False)
        limits_layout.addWidget(self.set_ntc_b_btn, 2, 2)
        
        ntc_layout.addLayout(limits_layout)
        ntc_group.setLayout(ntc_layout)
        main_layout.addWidget(ntc_group)

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

        main_layout.addStretch()
    
    def refresh_ports(self):
        """Refresh available serial ports"""
        import serial.tools.list_ports
        ports = serial.tools.list_ports.comports()
        
        self.port_combo.clear()
        for port in ports:
            self.port_combo.addItem(f"{port.device} - {port.description}")
    
    def toggle_connection(self):
        """Connect or disconnect from driver"""
        if not self.driver.is_connected:
            # Connect
            port = self.port_combo.currentText().split(' - ')[0]
            baud = int(self.baud_combo.currentText())

            if SIMULATION_MODE:
                # Simulated connection - always succeeds
                self.driver.connect(port, baud)
                self.conn_status.setText(f"SIMULATED - {port}")
                self.conn_status.setStyleSheet("color: orange; font-weight: bold;")
                self.connect_btn.setText("Disconnect")
                self.connect_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 8px;")

                # Register simulator for disconnect testing
                from Spectro_diode.hardware_simulator import register_driver_simulator
                register_driver_simulator(self.driver)

                # Enable controls
                self.enable_controls(True)

                # Query current limits from driver
                self.query_current_limits()

                # Apply all saved parameters BEFORE starting monitoring to avoid
                # serial contention between init commands and the polling loop
                self.apply_all_parameters()

                # Start monitoring thread only after init is complete
                self.start_monitoring()

                self.status_update.emit(f"🔧 SIMULATION: Connected to simulated driver")

            elif self.driver.connect(port, baud):
                self.conn_status.setText(f"Connected to {port}")
                self.conn_status.setStyleSheet("color: green; font-weight: bold;")
                self.connect_btn.setText("Disconnect")
                self.connect_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 8px;")

                # Enable controls (sends P commands: set_frequency + set_state)
                self.enable_controls(True)

                # Let the P-command echoes from enable_controls drain before querying
                import time as _t
                _t.sleep(0.15)
                if self.driver.serial_port and self.driver.serial_port.is_open:
                    self.driver.serial_port.reset_input_buffer()

                # Query current limits from driver
                self.query_current_limits()

                # Apply all saved parameters BEFORE starting monitoring to avoid
                # serial contention between init commands and the polling loop
                self.apply_all_parameters()

                # Start monitoring thread only after init is complete
                self.start_monitoring()

                self.status_update.emit(f"Connected to driver at {port}")
            else:
                self.conn_status.setText("Connection failed")
                self.conn_status.setStyleSheet("color: red;")
                self.status_update.emit("Failed to connect to driver")
        else:
            # Disconnect
            self.stop_monitoring()  # Stop thread before disconnect

            self.driver.disconnect()
            self.conn_status.setText("Not connected")
            self.conn_status.setStyleSheet("color: gray;")
            self.connect_btn.setText("Connect to Driver")
            self.connect_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px;")

            # Disable controls
            self.enable_controls(False)

            # Reset current limits display and spinbox max
            self.current_limits_label.setText("Limits: -- A")
            self.current_limits_label.setStyleSheet("color: gray; font-style: italic;")
            self.current_setpoint.setMaximum(20.0)  # Restore default max

            # Clear current state and locks
            self.current_state = None
            self.current_locks = None
            self._ntc_consecutive = 0

            # Update enable status to show DISCONNECTED
            self.enable_status.setStyleSheet("color: gray;")
            self.enable_status_text.setText("DISCONNECTED")
            self.enable_status_text.setStyleSheet("font-weight: bold; color: gray;")

            # Update all protection labels to show DISCONNECTED
            for label_name in self.protect_labels:
                self.protect_labels[label_name].setText("DISCONNECTED")
                self.protect_labels[label_name].setStyleSheet("color: gray; font-weight: bold;")

            self.status_update.emit("Disconnected from driver")

    def on_connection_lost(self):
        """Handle unexpected USB disconnection"""
        # Update UI to show disconnected state
        self.conn_status.setText("CONNECTION LOST")
        self.conn_status.setStyleSheet("color: red; font-weight: bold;")
        self.connect_btn.setText("Connect to Driver")
        self.connect_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px;")

        # Disable controls
        self.enable_controls(False)

        # Stop monitoring thread
        self.stop_monitoring()

        # Ensure driver state is cleaned up
        self.driver.is_connected = False
        self.driver.serial_port = None

        # Clear current state and locks
        self.current_state = None
        self.current_locks = None
        self._ntc_consecutive = 0

        # Update enable status to show DISCONNECTED
        self.enable_status.setStyleSheet("color: gray;")
        self.enable_status_text.setText("DISCONNECTED")
        self.enable_status_text.setStyleSheet("font-weight: bold; color: gray;")

        # Update all protection labels to show DISCONNECTED
        for label_name in self.protect_labels:
            self.protect_labels[label_name].setText("DISCONNECTED")
            self.protect_labels[label_name].setStyleSheet("color: gray; font-weight: bold;")

        # Stop Arduino triggers if running (safety measure)
        main_window = self.parent()
        while main_window and not hasattr(main_window, 'arduino_tab'):
            main_window = main_window.parent()
        if main_window and hasattr(main_window, 'arduino_tab'):
            arduino_tab = main_window.arduino_tab
            if hasattr(arduino_tab, 'trigger_status_text') and arduino_tab.trigger_status_text.text() == "RUNNING":
                self.status_update.emit("⚠️ Stopping Arduino triggers (driver disconnected)")
                arduino_tab.stop_triggers()

        self.status_update.emit("Driver USB connection lost - please reconnect")
    
    def apply_all_parameters(self):
        """Apply all saved parameters to driver on connection (except started state)"""
        if not self.driver.is_connected:
            return

        try:
            # Send stop command first to ensure safe state on reconnection
            if self.driver.stop():
                self.status_update.emit("✓ Stop command sent (safe state)")

            # 1. Set current
            current = self.current_setpoint.value()
            if self.driver.set_current(current):
                self.status_update.emit(f"✓ Current: {current:.3f} A")
            
            # 2. Set mode (CW vs QCW) and frequency
            # External enable requires CW mode
            if self.external_enable_radio.isChecked() or self.cw_radio.isChecked():
                # CW mode = frequency 0
                if self.driver.set_frequency(0):
                    self.status_update.emit("✓ Mode: CW (Continuous)")
            else:
                # QCW (Pulsed) mode - only available with internal enable
                freq = self.frequency.value()
                if self.driver.set_frequency(freq):
                    self.status_update.emit(f"✓ Mode: QCW @ {freq:.1f} Hz")

                # Set pulse duration
                duration = self.duration.value()
                if self.driver.set_duration(duration):
                    self.status_update.emit(f"✓ Pulse duration: {duration:.1f} ms")
            
            # 3. Set enable mode (External vs Internal)
            if self.external_enable_radio.isChecked():
                if self.driver.set_state(self.driver.STATE_ENABLE_EXTERNAL):
                    self.status_update.emit("✓ Enable: External")
            else:
                if self.driver.set_state(self.driver.STATE_ENABLE_INTERNAL):
                    self.status_update.emit("✓ Enable: Internal")
            
            # 4. Set NTC parameters
            ntc_lower = self.ntc_lower.value()
            if self.driver.set_ntc_lower_limit(ntc_lower):
                self.status_update.emit(f"✓ NTC lower: {ntc_lower:.1f}°C")

            ntc_upper = self.ntc_upper.value()
            if self.driver.set_ntc_upper_limit(ntc_upper):
                self.status_update.emit(f"✓ NTC upper: {ntc_upper:.1f}°C")

            ntc_b = self.ntc_b_coeff.value()
            if self.driver.set_ntc_b_coefficient(ntc_b):
                self.status_update.emit(f"✓ NTC B coefficient: {ntc_b}")

            # 5. Set NTC interlock allow/deny state
            if self.ntc_interlock_allowed:
                if self.driver.set_state(self.driver.STATE_NTC_ALLOW):
                    self.status_update.emit("✓ NTC Interlock: Allowed")
            else:
                if self.driver.set_state(self.driver.STATE_NTC_DENY):
                    self.status_update.emit("✓ NTC Interlock: Denied")

            # Let all P-command echoes drain before monitoring starts
            import time as _time
            _time.sleep(0.3)
            if self.driver.serial_port and self.driver.serial_port.is_open:
                self.driver.serial_port.reset_input_buffer()

            self.status_update.emit("✅ All parameters applied")

        except Exception as e:
            self.status_update.emit(f"❌ Error applying parameters: {str(e)}")
    
    def start_monitoring(self):
        """Start the monitoring thread"""
        if self.monitor_thread is None or not self.monitor_thread.isRunning():
            self.monitor_thread = DriverMonitorThread(self.driver)

            # Connect signals
            self.monitor_thread.measurements_ready.connect(self.update_measurements_display)
            self.monitor_thread.state_ready.connect(self.update_state_display)
            self.monitor_thread.locks_ready.connect(self.update_protection_status)
            self.monitor_thread.error.connect(lambda msg: self.status_update.emit(msg))
            self.monitor_thread.connection_lost.connect(self.on_connection_lost)

            self.monitor_thread.start()
            self.status_update.emit("Real-time monitoring started")
    
    def stop_monitoring(self):
        """Stop the monitoring thread"""
        if self.monitor_thread and self.monitor_thread.isRunning():
            self.monitor_thread.stop()
            # Wait up to 3s: worst case is one in-flight readline (0.5s timeout)
            # plus one 50ms sleep iteration before the thread sees running=False
            if not self.monitor_thread.wait(3000):
                # Thread did not exit gracefully - force terminate to avoid
                # blocking disconnect or application close
                self.monitor_thread.terminate()
                self.monitor_thread.wait(1000)
                self.status_update.emit("Warning: monitoring thread force-terminated")
            else:
                self.status_update.emit("Real-time monitoring stopped")
    
    def update_measurements_display(self, measurements):
        """Update measurement displays from thread data"""
        current = measurements['current']
        voltage = measurements['voltage']
        pcb_temp = measurements['pcb_temp']
        ntc_temp = measurements['ntc_temp']
        
        # Update displays
        self.meas_labels['Output Current'].setText(f"{current:.2f}" if current is not None else "--")
        self.meas_labels['Output Voltage'].setText(f"{voltage:.1f}" if voltage is not None else "--")
        self.meas_labels['PCB Temperature'].setText(f"{pcb_temp:.1f}" if pcb_temp is not None else "--")
        self.meas_labels['NTC Temperature'].setText(f"{ntc_temp:.1f}" if ntc_temp is not None else "--")
        
        # Warning for high PCB temperature
        if pcb_temp is not None and pcb_temp > 60:
            self.meas_labels['PCB Temperature'].setStyleSheet(
                "background-color: #ffcccc; padding: 5px; border: 1px solid red; font-weight: bold;"
            )
        else:
            self.meas_labels['PCB Temperature'].setStyleSheet(
                "background-color: #f0f0f0; padding: 5px; border: 1px solid #ccc;"
            )
    
    def update_state_display(self, state):
        """Update enable status from thread data"""
        # Store state for use in protection status
        self.current_state = state
        self.update_enable_status(state['started'])

    def query_current_limits(self):
        """Query current limits from driver and update GUI"""
        current_min = self.driver.get_current_min()
        current_max = self.driver.get_current_max()

        if current_min is not None and current_max is not None:
            # Update limits label
            self.current_limits_label.setText(f"Limits: {current_min:.2f} - {current_max:.2f} A")
            self.current_limits_label.setStyleSheet("color: #666; font-style: italic;")

            # Update spinbox maximum to match hardware limit
            old_max = self.current_setpoint.maximum()
            self.current_setpoint.setMaximum(current_max)

            # Check if current value exceeds new max and clamp if needed
            current_value = self.current_setpoint.value()
            if current_value > current_max:
                self.current_setpoint.setValue(current_max)
                self.status_update.emit(f"Warning: Current setpoint clamped from {current_value:.2f}A to hardware max {current_max:.2f}A")
        else:
            # Could not query limits
            self.current_limits_label.setText("Limits: (query failed)")
            self.current_limits_label.setStyleSheet("color: orange; font-style: italic;")

    def enable_controls(self, enable):
        """Enable or disable controls based on connection state"""
        # Enable mode buttons
        self.external_enable_radio.setEnabled(enable)
        self.internal_enable_radio.setEnabled(enable)

        # Current control
        self.current_setpoint.setEnabled(enable)

        # Output mode buttons
        self.cw_radio.setEnabled(enable)
        # QCW only available with internal enable mode
        if enable and self.external_enable_radio.isChecked():
            self.qcw_radio.setEnabled(False)
        else:
            self.qcw_radio.setEnabled(enable)

        # Start/Stop buttons
        # Start button disabled in external enable mode (controlled by Arduino)
        if enable and self.external_enable_radio.isChecked():
            self.start_btn.setEnabled(False)
        else:
            self.start_btn.setEnabled(enable)
        self.stop_btn.setEnabled(enable)

        # NTC controls
        self.allow_ntc_btn.setEnabled(enable)
        self.deny_ntc_btn.setEnabled(enable)
        self.ntc_lower.setEnabled(enable)
        self.set_ntc_lower_btn.setEnabled(enable)
        self.ntc_upper.setEnabled(enable)
        self.set_ntc_upper_btn.setEnabled(enable)
        self.ntc_b_coeff.setEnabled(enable)
        self.set_ntc_b_btn.setEnabled(enable)

        # Update output mode settings
        if enable:
            self.on_output_mode_changed()
            # Set to internal current mode on connect
            self.driver.set_state(self.driver.STATE_CURRENT_INTERNAL)
    
    def on_enable_mode_changed(self, checked=True):
        """Handle enable mode change"""
        # Only process when a button is checked (not unchecked)
        if not checked:
            return

        is_internal = self.internal_enable_radio.isChecked()
        is_external = self.external_enable_radio.isChecked()

        # When external enable is selected, force CW mode and disable QCW option
        if is_external:
            # Force CW mode
            self.cw_radio.setChecked(True)
            # Disable QCW option (not compatible with external enable)
            self.qcw_radio.setEnabled(False)
            # Disable Start button - output controlled by Arduino triggers
            self.start_btn.setEnabled(False)
        else:
            # Re-enable QCW option when using internal enable
            self.qcw_radio.setEnabled(self.driver.is_connected)
            # Re-enable Start button for internal enable mode
            self.start_btn.setEnabled(self.driver.is_connected)

        if not self.driver.is_connected:
            return

        if is_internal:
            self.driver.set_state(self.driver.STATE_ENABLE_INTERNAL)
            self.status_update.emit("Enable mode: Internal (Software)")
        else:
            self.driver.set_state(self.driver.STATE_ENABLE_EXTERNAL)
            # Ensure CW mode is set on driver
            self.driver.set_frequency(0)
            self.status_update.emit("Enable mode: External (Hardware) - CW mode enforced")

    def on_output_mode_changed(self, checked=True):
        """Handle output mode change (CW/QCW)"""
        # Only process when a button is checked (not unchecked)
        if not checked:
            return

        is_qcw = self.qcw_radio.isChecked()

        # External enable requires CW mode - force CW if external enable selected
        if self.external_enable_radio.isChecked() and is_qcw:
            self.cw_radio.setChecked(True)
            is_qcw = False

        # Enable/disable QCW parameters (only when laser is not running)
        is_running = hasattr(self, 'current_state') and self.current_state and self.current_state.get('started', False)
        can_edit = is_qcw and not is_running

        self.frequency.setEnabled(can_edit)
        self.freq_label.setEnabled(is_qcw)

        self.duration.setEnabled(can_edit)
        self.dur_label.setEnabled(is_qcw)

        if self.driver.is_connected:
            if is_qcw:
                self.status_update.emit("Mode: QCW (Pulsed)")
            else:
                # Set frequency to 0 for CW mode
                self.driver.set_frequency(0)
                self.status_update.emit("Mode: CW (Continuous)")

    def on_frequency_changed(self, freq):
        """Limit pulse duration when frequency changes.

        Per SF6100 spec: duration must be in range [2 ms, period - 2 ms],
        capped at 5000 ms for very low frequencies.
        """
        if freq > 0:
            max_duration_ms = min(1000.0 / freq - 2.0, 5000.0)
            max_duration_ms = max(max_duration_ms, 2.0)  # never below hw minimum
            self.duration.setMaximum(max_duration_ms)
            if self.duration.value() > max_duration_ms:
                self.duration.setValue(max_duration_ms)

    def start_output(self):
        """Start driver output"""
        if not self.driver.is_connected:
            return

        # In External Enable mode, output is controlled by Arduino triggers, not this button
        if self.external_enable_radio.isChecked():
            self.status_update.emit("⚠️ External Enable mode - use Arduino triggers to start output")
            return

        # Check for active protections before starting
        if self.current_locks:
            active_protections = []
            if self.current_locks.get('interlock', False):
                active_protections.append('Interlock')
            if self.current_locks.get('crowbar', False):
                active_protections.append('Crowbar')
            if self.current_locks.get('overcurrent', False):
                active_protections.append('Overcurrent')
            if self.current_locks.get('overheat', False):
                active_protections.append('Overheat')
            if self.current_locks.get('ntc_interlock', False):
                active_protections.append('NTC Interlock')

            if active_protections:
                self.status_update.emit(f"⚠️ Cannot start - protection active: {', '.join(active_protections)}")
                return

        # Send current setpoint before starting
        current = self.current_setpoint.value()
        if self.driver.set_current(current):
            self.status_update.emit(f"Current set to {current:.2f} A")

        # In QCW mode, send frequency and duration before starting
        if self.qcw_radio.isChecked():
            freq = self.frequency.value()
            duration = self.duration.value()
            if self.driver.set_frequency(freq):
                self.status_update.emit(f"Frequency set to {freq:.1f} Hz")
            else:
                self.status_update.emit("Failed to set frequency")
                return
            if self.driver.set_duration(duration):
                self.status_update.emit(f"Duration set to {duration:.1f} ms")
            else:
                self.status_update.emit("Failed to set duration")
                return

        if self.driver.start():
            self.status_update.emit("Output STARTED")
            self.update_enable_status(True)
        else:
            self.status_update.emit("Failed to start output (check interlock!)")
    
    def stop_output(self):
        """Stop driver output"""
        if not self.driver.is_connected:
            return

        # Silence the Arduino laser pin first so the SF6100 enable input goes
        # LOW before the serial stop command is sent.
        main_window = self.parent()
        while main_window and not hasattr(main_window, 'arduino_tab'):
            main_window = main_window.parent()
        if main_window and hasattr(main_window, 'arduino_tab'):
            arduino_tab = main_window.arduino_tab
            if arduino_tab.connected and arduino_tab.arduino:
                spec_inactive = getattr(arduino_tab.arduino, 'spec_silenced', False)
                if not spec_inactive:
                    measurement_stopped = not (
                        hasattr(main_window, 'measurement_thread') and
                        main_window.measurement_thread is not None and
                        main_window.measurement_thread.isRunning()
                    )
                    spec_inactive = measurement_stopped
                if not spec_inactive:
                    status = arduino_tab.arduino.get_status()
                    if status:
                        spec_inactive = (
                            not status['running'] or
                            (status['spec_cycles'] > 0 and
                             status['cycles'] >= status['spec_cycles'])
                        )
                if spec_inactive:
                    arduino_tab.stop_triggers()
                else:
                    arduino_tab.arduino.stop_laser()
                    self.status_update.emit("Arduino LASER pin silenced")

        if self.driver.stop():
            self.status_update.emit("Output STOPPED")
            self.enable_status.setStyleSheet("color: red;")
            self.enable_status_text.setText("STOPPED")
            self.enable_status_text.setStyleSheet("font-weight: bold; color: red;")
        else:
            self.status_update.emit("Failed to stop output")
    
    def update_enable_status(self, is_started):
        """Update enable status display and disable parameter controls when running"""
        if is_started:
            self.enable_status.setStyleSheet("color: green;")
            self.enable_status_text.setText("STARTED")
            self.enable_status_text.setStyleSheet("font-weight: bold; color: green;")
            # Disable parameter controls while running
            self.current_setpoint.setEnabled(False)
            self.frequency.setEnabled(False)
            self.duration.setEnabled(False)
            self.cw_radio.setEnabled(False)
            self.qcw_radio.setEnabled(False)
            self.external_enable_radio.setEnabled(False)
            self.internal_enable_radio.setEnabled(False)
        else:
            # Check if Arduino triggers are running - if so, don't re-enable mode switches
            arduino_triggers_running = False
            main_window = self.parent()
            while main_window and not hasattr(main_window, 'arduino_tab'):
                main_window = main_window.parent()
            if main_window and hasattr(main_window, 'arduino_tab'):
                arduino_tab = main_window.arduino_tab
                if hasattr(arduino_tab, 'trigger_status_text'):
                    arduino_triggers_running = arduino_tab.trigger_status_text.text() == "RUNNING"

            # Only update status text if not running via external triggers
            if arduino_triggers_running and self.external_enable_radio.isChecked():
                # Keep showing STARTED (External) - don't change to STOPPED
                pass
            else:
                self.enable_status.setStyleSheet("color: red;")
                self.enable_status_text.setText("STOPPED")
                self.enable_status_text.setStyleSheet("font-weight: bold; color: red;")

            # Re-enable parameter controls when stopped (if connected)
            if self.driver.is_connected:
                self.current_setpoint.setEnabled(True)
                self.cw_radio.setEnabled(True)
                # Only re-enable mode switches if Arduino triggers are NOT running
                if not arduino_triggers_running:
                    self.external_enable_radio.setEnabled(True)
                    self.internal_enable_radio.setEnabled(True)
                # QCW controls depend on mode
                is_internal = self.internal_enable_radio.isChecked()
                self.qcw_radio.setEnabled(is_internal)
                is_qcw = self.qcw_radio.isChecked()
                self.frequency.setEnabled(is_qcw)
                self.duration.setEnabled(is_qcw)
    
    def update_protection_status(self, locks):
        """Update protection status displays"""
        # Store locks for checking in start_output
        self.current_locks = locks

        # Check for any new protection activations (rising edge) and auto-stop
        # Note: NTC interlock is handled separately due to denied/allowed logic
        new_protections = []
        for key, label_name in [
            ('interlock', 'Interlock'),
            ('crowbar', 'Crowbar'),
            ('overcurrent', 'Overcurrent'),
            ('overheat', 'Overheat'),
        ]:
            is_active = locks.get(key, False)
            was_active = self.previous_protection_states.get(key, False)

            # Rising edge detection - protection just became active
            if is_active and not was_active:
                new_protections.append(label_name)

            # Update previous state
            self.previous_protection_states[key] = is_active

        # Special handling for NTC Interlock - only stop if allowed (not denied)
        ntc_active = locks.get('ntc_interlock', False)
        ntc_was_active = self.previous_protection_states.get('ntc_interlock', False)
        ntc_denied = hasattr(self, 'current_state') and self.current_state and self.current_state.get('ntc_denied', False)
        ntc_was_denied = self.previous_protection_states.get('ntc_was_denied', True)  # Default True to avoid false trigger on startup

        # Hysteresis: count consecutive active readings (resets to 0 when not active or denied).
        # Require a confirmed state read before counting — avoids false trips on startup
        # when current_state is None and ntc_denied can't be verified.
        ntc_state_known = self.current_state is not None
        if ntc_active and not ntc_denied and ntc_state_known:
            self._ntc_consecutive += 1
        else:
            self._ntc_consecutive = 0
        ntc_confirmed = self._ntc_consecutive >= self._NTC_HYSTERESIS

        # NTC interlock triggers stop only after N consecutive confirmed readings
        if ntc_confirmed:
            if not ntc_was_active or ntc_was_denied:
                new_protections.append('NTC Interlock')

        # Update previous states for NTC
        self.previous_protection_states['ntc_interlock'] = ntc_confirmed
        self.previous_protection_states['ntc_was_denied'] = ntc_denied

        # Auto-stop if any protection became active
        if new_protections and self.driver.is_connected:
            self.status_update.emit(f"⚠️ PROTECTION ACTIVE: {', '.join(new_protections)} - Sending stop command")
            self.driver.stop()
            # Emit signal so main GUI can stop Arduino triggers
            self.protection_activated.emit(new_protections)

        # Update UI labels
        # Interlock
        if locks.get('interlock', False):
            self.protect_labels['Interlock'].setText("ACTIVE")
            self.protect_labels['Interlock'].setStyleSheet("color: red; font-weight: bold;")
        else:
            self.protect_labels['Interlock'].setText("OK")
            self.protect_labels['Interlock'].setStyleSheet("color: green; font-weight: bold;")

        # Crowbar
        if locks.get('crowbar', False):
            self.protect_labels['Crowbar'].setText("ACTIVE")
            self.protect_labels['Crowbar'].setStyleSheet("color: red; font-weight: bold;")
        else:
            self.protect_labels['Crowbar'].setText("OK")
            self.protect_labels['Crowbar'].setStyleSheet("color: green; font-weight: bold;")

        # Overcurrent
        if locks.get('overcurrent', False):
            self.protect_labels['Overcurrent'].setText("FAULT")
            self.protect_labels['Overcurrent'].setStyleSheet("color: red; font-weight: bold;")
        else:
            self.protect_labels['Overcurrent'].setText("OK")
            self.protect_labels['Overcurrent'].setStyleSheet("color: green; font-weight: bold;")

        # Overheat
        if locks.get('overheat', False):
            self.protect_labels['Overheat'].setText("WARNING")
            self.protect_labels['Overheat'].setStyleSheet("color: orange; font-weight: bold;")
        else:
            self.protect_labels['Overheat'].setText("OK")
            self.protect_labels['Overheat'].setStyleSheet("color: green; font-weight: bold;")

        # NTC Interlock - DENIED takes priority; use hysteresis-confirmed value for ACTIVE
        if hasattr(self, 'current_state') and self.current_state:
            ntc_denied = self.current_state.get('ntc_denied', False)
            if ntc_denied:
                self.protect_labels['NTC Interlock'].setText("DENIED")
                self.protect_labels['NTC Interlock'].setStyleSheet("color: orange; font-weight: bold;")
            elif ntc_confirmed:
                self.protect_labels['NTC Interlock'].setText("ACTIVE")
                self.protect_labels['NTC Interlock'].setStyleSheet("color: red; font-weight: bold;")
            else:
                self.protect_labels['NTC Interlock'].setText("OK")
                self.protect_labels['NTC Interlock'].setStyleSheet("color: green; font-weight: bold;")
        else:
            if ntc_confirmed:
                self.protect_labels['NTC Interlock'].setText("ACTIVE")
                self.protect_labels['NTC Interlock'].setStyleSheet("color: red; font-weight: bold;")
            else:
                self.protect_labels['NTC Interlock'].setText("OK")
                self.protect_labels['NTC Interlock'].setStyleSheet("color: green; font-weight: bold;")

            
    def allow_ntc(self):
        """Allow NTC interlock"""
        self.ntc_interlock_allowed = True
        if not self.driver.is_connected:
            return

        if self.driver.set_state(self.driver.STATE_NTC_ALLOW):
            self.status_update.emit("NTC Interlock: Allowed")
        else:
            self.status_update.emit("Failed to allow NTC interlock")

    def deny_ntc(self):
        """Deny NTC interlock"""
        self.ntc_interlock_allowed = False
        if not self.driver.is_connected:
            return

        if self.driver.set_state(self.driver.STATE_NTC_DENY):
            self.status_update.emit("NTC Interlock: Denied")
        else:
            self.status_update.emit("Failed to deny NTC interlock")
    
    def set_ntc_lower_limit(self):
        """Set NTC lower temperature limit"""
        if not self.driver.is_connected:
            return
        
        temp = self.ntc_lower.value()
        if self.driver.set_ntc_lower_limit(temp):
            self.status_update.emit(f"NTC lower limit set to {temp:.1f}°C")
        else:
            self.status_update.emit("Failed to set NTC lower limit")
    
    def set_ntc_upper_limit(self):
        """Set NTC upper temperature limit"""
        if not self.driver.is_connected:
            return
        
        temp = self.ntc_upper.value()
        if self.driver.set_ntc_upper_limit(temp):
            self.status_update.emit(f"NTC upper limit set to {temp:.1f}°C")
        else:
            self.status_update.emit("Failed to set NTC upper limit")
    
    def set_ntc_b_coefficient(self):
        """Set NTC B25/100 coefficient"""
        if not self.driver.is_connected:
            return
        
        b_value = self.ntc_b_coeff.value()
        if self.driver.set_ntc_b_coefficient(b_value):
            self.status_update.emit(f"NTC B25/100 set to {b_value}")
        else:
            self.status_update.emit("Failed to set NTC B coefficient")
    
    def cleanup(self):
        """Cleanup on close"""
        self.save_settings()

        # Stop monitoring thread first
        self.stop_monitoring()

        # Stop laser output before disconnecting (SAFETY)
        if self.driver.is_connected:
            try:
                # Check if output is running and stop it
                if hasattr(self, 'current_state') and self.current_state:
                    if self.current_state.get('started', False):
                        self.driver.stop()
            except:
                pass

        # Disconnect driver
        if self.driver.is_connected:
            try:
                self.driver.disconnect()
            except:
                pass
    
    def load_settings(self):
        """Load Driver parameters from settings"""
        try:
            if self.settings.contains('driver_port'):
                port = self.settings.value('driver_port', '', type=str)
                index = self.port_combo.findText(port)
                if index >= 0:
                    self.port_combo.setCurrentIndex(index)
            
            if self.settings.contains('driver_current'):
                self.current_setpoint.setValue(self.settings.value('driver_current', 0.0, type=float))
            
            if self.settings.contains('driver_mode_cw'):
                is_cw = self.settings.value('driver_mode_cw', True, type=bool)
                if is_cw:
                    self.cw_radio.setChecked(True)
                else:
                    self.qcw_radio.setChecked(True)
            
            if self.settings.contains('driver_external_enable'):
                self.external_enable_radio.setChecked(
                    self.settings.value('driver_external_enable', False, type=bool)
                )
            
            # Pulsed mode parameters
            if self.settings.contains('driver_frequency'):
                self.frequency.setValue(self.settings.value('driver_frequency', 1000.0, type=float))
            
            if self.settings.contains('driver_duration'):
                self.duration.setValue(self.settings.value('driver_duration', 100.0, type=float))
            
            # NTC parameters
            if self.settings.contains('driver_ntc_lower'):
                self.ntc_lower.setValue(self.settings.value('driver_ntc_lower', 0.0, type=float))

            if self.settings.contains('driver_ntc_upper'):
                self.ntc_upper.setValue(self.settings.value('driver_ntc_upper', 60.0, type=float))

            if self.settings.contains('driver_ntc_b_coeff'):
                self.ntc_b_coeff.setValue(self.settings.value('driver_ntc_b_coeff', 3977, type=int))

            if self.settings.contains('driver_ntc_interlock_allowed'):
                self.ntc_interlock_allowed = self.settings.value('driver_ntc_interlock_allowed', True, type=bool)

        except Exception as e:
            pass  # Silently ignore settings load errors
    
    def save_settings(self):
        """Save Driver parameters to settings (except started state)"""
        if not self.settings:
            return
        
        try:
            self.settings.setValue('driver_port', self.port_combo.currentText())
            self.settings.setValue('driver_current', self.current_setpoint.value())
            self.settings.setValue('driver_mode_cw', self.cw_radio.isChecked())
            self.settings.setValue('driver_external_enable', self.external_enable_radio.isChecked())
            
            # Pulsed mode parameters
            self.settings.setValue('driver_frequency', self.frequency.value())
            self.settings.setValue('driver_duration', self.duration.value())
            
            # NTC parameters
            self.settings.setValue('driver_ntc_lower', self.ntc_lower.value())
            self.settings.setValue('driver_ntc_upper', self.ntc_upper.value())
            self.settings.setValue('driver_ntc_b_coeff', self.ntc_b_coeff.value())
            self.settings.setValue('driver_ntc_interlock_allowed', self.ntc_interlock_allowed)

        except Exception as e:
            pass  # Silently ignore settings save errors
