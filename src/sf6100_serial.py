"""
SF6100 Laser Driver Serial Communication Class
Handles UART communication with Maiman Electronics SF6100 driver
Protocol: 115200 baud, 8N1, text-based commands
"""
import serial
import serial.tools.list_ports
import time
from threading import Lock

class SF6100Serial:
    """Serial communication handler for SF6100 laser driver"""
    
    # Command constants (from SF6100 manual)
    CMD_FREQUENCY = "0100"           # QCW frequency (0.1 Hz)
    CMD_DURATION = "0200"            # Pulse duration (0.1 ms)
    CMD_CURRENT = "0300"             # Current setpoint (0.01 A)
    CMD_CURRENT_MIN = "0301"         # Current minimum (0.01 A) - read only
    CMD_CURRENT_MAX = "0302"         # Current maximum (0.01 A) - read only (hardware pot)
    CMD_CURRENT_MEASURED = "0307"    # Measured current (0.1 A)
    CMD_VOLTAGE_MEASURED = "0407"    # Measured voltage (0.1 V)
    CMD_STATE = "0700"               # Device state (bit mask)
    CMD_SERIAL_NUMBER = "0701"       # Serial number
    CMD_DEVICE_ID = "0702"           # Device model/version
    CMD_LOCK_STATUS = "0800"         # Lock status (bit mask)
    CMD_NTC_TEMP = "0AE4"            # NTC temperature (0.1 °C)
    CMD_NTC_LOWER = "0A05"           # NTC lower limit
    CMD_NTC_UPPER = "0A06"           # NTC upper limit
    CMD_PCB_TEMP = "0AF4"            # PCB temperature (0.1 °C)
    CMD_NTC_B_COEFF = "0B0E"         # NTC B25/100 coefficient
    CMD_PROTOCOL_EXT = "0704"        # Extended protocol configuration
    CMD_FREQUENCY_MIN = "0101"       # Frequency minimum (read-only)
    CMD_FREQUENCY_MAX = "0102"       # Frequency maximum (read-only)
    CMD_DURATION_MIN = "0201"        # Duration minimum (read-only)
    CMD_DURATION_MAX = "0202"        # Duration maximum (read-only)
    
    # State command values (for CMD_STATE)
    STATE_START = "0008"             # Enable output
    STATE_STOP = "0010"              # Disable output
    STATE_CURRENT_INTERNAL = "0020"  # Internal current set
    STATE_CURRENT_EXTERNAL = "0040"  # External current set
    STATE_ENABLE_EXTERNAL = "0200"   # External enable
    STATE_ENABLE_INTERNAL = "0400"   # Internal enable
    STATE_INTERLOCK_ALLOW = "1000"   # Allow interlock
    STATE_INTERLOCK_DENY = "2000"    # Deny interlock
    STATE_NTC_ALLOW = "8000"         # Allow NTC interlock
    STATE_NTC_DENY = "4000"          # Deny NTC interlock
    
    # State bit masks (for reading CMD_STATE)
    BIT_POWERED = 0x0001      # Bit 0: Device powered on
    BIT_STARTED = 0x0002      # Bit 1: Output started
    BIT_CURRENT_INT = 0x0004  # Bit 2: Internal current set
    BIT_ENABLE_INT = 0x0010   # Bit 4: Internal enable
    BIT_NTC_DENIED = 0x0040   # Bit 6: NTC interlock denied
    BIT_INTERLOCK_DENIED = 0x0080  # Bit 7: Interlock denied
    
    # Lock status bit masks
    LOCK_INTERLOCK = 0x0002   # Bit 1: Interlock active
    LOCK_CROWBAR = 0x0004     # Bit 2: Crowbar protection active
    LOCK_OVERCURRENT = 0x0008 # Bit 3: Overcurrent protection
    LOCK_OVERHEAT = 0x0010    # Bit 4: Overheat warning
    LOCK_NTC = 0x0020         # Bit 5: NTC interlock
    
    def __init__(self):
        self.serial_port = None
        self.is_connected = False
        self.lock = Lock()  # Thread safety for serial operations
        
    @staticmethod
    def list_ports():
        """Get list of available serial ports"""
        ports = serial.tools.list_ports.comports()
        return [port.device for port in ports]
    
    def connect(self, port, baudrate=115200, timeout=1.0):
        """
        Connect to SF6100 driver
        
        Args:
            port (str): Serial port name (e.g., 'COM3' or '/dev/ttyUSB0')
            baudrate (int): Baud rate (default 115200)
            timeout (float): Read timeout in seconds
            
        Returns:
            bool: True if connection successful
        """
        try:
            self.serial_port = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5
            )

            # Clear any pending data
            time.sleep(0.1)  # Wait for port to stabilize
            self.serial_port.reset_input_buffer()
            self.serial_port.reset_output_buffer()

            # Test connection by reading device ID (with retries)
            time.sleep(0.2)  # Give device time to initialize

            for attempt in range(3):
                self.is_connected = True
                device_id = self.get_device_id()

                if device_id is not None:
                    # Disable P-command echo (extended protocol 0x0704, bit 2 off).
                    # By spec the default is silent P commands, but the device saves
                    # this setting to flash — if the Maiman software ever turned echo on
                    # it persists across power cycles.  Sending 0010 forces it back off.
                    # If echo is currently on, this command itself generates one K-echo;
                    # the sleep+flush discards it so all subsequent J reads are clean.
                    self.serial_port.write(b'P0704 0010\r')
                    time.sleep(0.15)
                    self.serial_port.reset_input_buffer()
                    return True

                # Clear stale data before retrying
                self.serial_port.reset_input_buffer()
                time.sleep(0.15)

            # Connection test failed
            self.is_connected = False
            self.disconnect()
            return False

        except Exception as e:
            if self.serial_port and self.serial_port.is_open:
                self.serial_port.close()
            self.is_connected = False
            return False

    def disconnect(self):
        """Disconnect from SF6100 driver safely"""
        # Try to stop output before disconnecting (safety)
        if self.serial_port and self.is_connected:
            try:
                if self.serial_port.is_open:
                    self.set_state(self.STATE_STOP)
                    time.sleep(0.1)
            except (serial.SerialException, OSError):
                # Port already disconnected, ignore
                pass
            except Exception:
                pass

        # Close the port
        if self.serial_port:
            try:
                if self.serial_port.is_open:
                    self.serial_port.close()
            except (serial.SerialException, OSError):
                pass
            except Exception:
                pass

        self.is_connected = False
        self.serial_port = None
    
    def _send_command(self, cmd_type, param, value=None):
        """
        Send command to driver (internal method)

        Args:
            cmd_type (str): 'P' (set) or 'J' (get)
            param (str): 4-digit hex parameter code
            value (str): 4-digit hex value (for P commands)

        Returns:
            str: Response from driver, or None on error
        """
        # Check connection status
        if not self.is_connected or self.serial_port is None:
            return None

        with self.lock:
            try:
                # Verify port is still open before attempting operation
                if not self.serial_port.is_open:
                    self._handle_connection_lost()
                    return None

                # Flush any residual bytes before issuing a read (safety net
                # in case P-echo was re-enabled or a previous command left data)
                if cmd_type == 'J':
                    self.serial_port.reset_input_buffer()

                # Build command
                if value is not None:
                    command = f"{cmd_type}{param} {value}\r"
                else:
                    command = f"{cmd_type}{param}\r"

                encoded = command.encode('ascii')

                # Send command
                self.serial_port.write(encoded)

                # Read response (for J commands).
                # Protocol uses \r (0x0D) as line terminator, not \n.
                # read_until(b'\r') reads exactly one response.
                if cmd_type == 'J':
                    time.sleep(0.05)  # Allow USB-serial bridge to buffer the response

                    expected_prefix = f'K{param}'
                    response = ''

                    # Retry loop: handles stale P-echoes (up to 2 retransmissions).
                    # Each stale echo flushes the buffer and retransmits the J command.
                    for attempt in range(3):
                        response = self.serial_port.read_until(b'\r').decode('ascii').strip()

                        if not response:
                            # Timeout — device may be busy (300 ms save operation)
                            if attempt < 2:
                                self.serial_port.write(encoded)
                                time.sleep(0.1)
                            continue

                        if (response.startswith(expected_prefix)
                                or response.startswith('E')
                                or response.startswith('K0000')):
                            break  # Valid response for this parameter

                        # Stale echo from a P command — flush and retransmit
                        if attempt < 2:
                            self.serial_port.reset_input_buffer()
                            self.serial_port.write(encoded)
                            time.sleep(0.08)

                    return response
                else:
                    return "OK"

            except (serial.SerialException, OSError) as e:
                # USB disconnected or serial port error
                self._handle_connection_lost()
                return None
            except Exception as e:
                return None

    def _handle_connection_lost(self):
        """Handle unexpected connection loss (e.g., USB unplugged)"""
        self.is_connected = False
        if self.serial_port:
            try:
                self.serial_port.close()
            except Exception:
                pass
        self.serial_port = None
    
    def _parse_response(self, response, expected_param):
        """
        Parse K-type response from driver.

        Args:
            response (str): Response string (e.g., "K0300 03E8")
            expected_param (str): Expected parameter code

        Returns:
            int: Parsed hex value, or None on error/unrecognized response
        """
        if not response:
            return None

        # E-type: device error codes (E0000 buffer overflow, E0001 unknown cmd, E0002 CRC)
        if response.startswith('E'):
            return None

        if not response.startswith('K'):
            return None

        # K0000 0000 = requested parameter does not exist on this device
        if response.startswith('K0000'):
            return None

        try:
            # Format: K0300 03E8
            parts = response.split()
            if len(parts) != 2:
                return None

            param_code = parts[0][1:]  # Remove 'K' prefix
            value_hex = parts[1]

            if param_code == expected_param:
                return int(value_hex, 16)
            return None

        except Exception:
            return None
    
    # === HIGH-LEVEL CONTROL METHODS ===
    
    def set_current(self, current_amps, verify=True):
        """
        Set output current (internal current set mode)

        Args:
            current_amps (float): Current in Amperes (0-25A)
            verify (bool): If True, verify the value was accepted by querying back

        Returns:
            bool: True if successful (and verified if verify=True)
        """
        if not 0 <= current_amps <= 25:
            return False

        # Convert to 0.01A units (e.g., 10.5A -> 1050 -> 041A hex)
        value = int(current_amps * 100)
        value_hex = f"{value:04X}"

        result = self._send_command('P', self.CMD_CURRENT, value_hex)
        if result is None:
            return False

        if verify:
            # Wait for driver to process
            time.sleep(0.02)
            # Query back and verify
            readback = self.get_current_setpoint()
            if readback is None:
                return False
            # Compare with tolerance of 0.02 A
            if abs(readback - current_amps) > 0.02:
                return False

        return True
    
    def get_current_setpoint(self):
        """
        Get current setpoint

        Returns:
            float: Current setpoint in Amperes, or None on error
        """
        response = self._send_command('J', self.CMD_CURRENT)
        value = self._parse_response(response, self.CMD_CURRENT)

        if value is not None:
            return value / 100.0  # Convert from 0.01A units
        return None

    def get_current_min(self):
        """
        Get minimum current limit (hardware parameter)

        Returns:
            float: Minimum current in Amperes, or None on error
        """
        response = self._send_command('J', self.CMD_CURRENT_MIN)
        value = self._parse_response(response, self.CMD_CURRENT_MIN)

        if value is not None:
            return value / 100.0  # Convert from 0.01A units
        return None

    def get_current_max(self):
        """
        Get maximum current limit (reflects hardware potentiometer setting)

        Returns:
            float: Maximum current in Amperes, or None on error
        """
        response = self._send_command('J', self.CMD_CURRENT_MAX)
        value = self._parse_response(response, self.CMD_CURRENT_MAX)

        if value is not None:
            return value / 100.0  # Convert from 0.01A units
        return None

    def get_current_measured(self):
        """
        Get measured output current
        
        Returns:
            float: Measured current in Amperes, or None on error
        """
        response = self._send_command('J', self.CMD_CURRENT_MEASURED)
        value = self._parse_response(response, self.CMD_CURRENT_MEASURED)
        
        if value is not None:
            return value / 10.0  # Convert from 0.1A units
        return None
    
    def get_voltage_measured(self):
        """
        Get measured output voltage
        
        Returns:
            float: Measured voltage in Volts, or None on error
        """
        response = self._send_command('J', self.CMD_VOLTAGE_MEASURED)
        value = self._parse_response(response, self.CMD_VOLTAGE_MEASURED)
        
        if value is not None:
            return value / 10.0  # Convert from 0.1V units
        return None
    
    def set_frequency(self, frequency_hz, verify=True):
        """
        Set QCW frequency

        Args:
            frequency_hz (float): Frequency in Hz (0.1-500 Hz, 0 for CW mode)
            verify (bool): If True, verify the value was accepted by querying back

        Returns:
            bool: True if successful (and verified if verify=True)
        """
        if not 0 <= frequency_hz <= 500:
            return False

        # Convert to 0.1Hz units (e.g., 50Hz -> 500 -> 01F4 hex)
        value = int(frequency_hz * 10)
        value_hex = f"{value:04X}"

        result = self._send_command('P', self.CMD_FREQUENCY, value_hex)
        if result is None:
            return False

        if verify:
            # Wait for driver to process
            time.sleep(0.02)
            # Query back and verify
            readback = self.get_frequency()
            if readback is None:
                return False
            # Compare with tolerance of 0.1 Hz
            if abs(readback - frequency_hz) > 0.1:
                return False

        return True
    
    def get_frequency(self):
        """
        Get QCW frequency
        
        Returns:
            float: Frequency in Hz, or None on error
        """
        response = self._send_command('J', self.CMD_FREQUENCY)
        value = self._parse_response(response, self.CMD_FREQUENCY)
        
        if value is not None:
            return value / 10.0  # Convert from 0.1Hz units
        return None
    
    def set_duration(self, duration_ms, verify=True):
        """
        Set pulse duration (QCW mode)

        Args:
            duration_ms (float): Duration in milliseconds (2-5000ms)
            verify (bool): If True, verify the value was accepted by querying back

        Returns:
            bool: True if successful (and verified if verify=True)
        """
        if not 2 <= duration_ms <= 5000:
            return False

        # Convert to 0.1ms units (e.g., 100ms -> 1000 -> 03E8 hex)
        value = int(duration_ms * 10)
        value_hex = f"{value:04X}"

        result = self._send_command('P', self.CMD_DURATION, value_hex)
        if result is None:
            return False

        if verify:
            # Wait for driver to process
            time.sleep(0.02)
            # Query back and verify
            readback = self.get_duration()
            if readback is None:
                return False
            # Compare with tolerance of 0.1 ms
            if abs(readback - duration_ms) > 0.1:
                return False

        return True
    
    def get_duration(self):
        """
        Get pulse duration
        
        Returns:
            float: Duration in milliseconds, or None on error
        """
        response = self._send_command('J', self.CMD_DURATION)
        value = self._parse_response(response, self.CMD_DURATION)
        
        if value is not None:
            return value / 10.0  # Convert from 0.1ms units
        return None
    
    def set_state(self, state_value):
        """
        Set device state (generic method)
        
        Args:
            state_value (str): Hex state value (e.g., STATE_START)
            
        Returns:
            bool: True if successful
        """
        result = self._send_command('P', self.CMD_STATE, state_value)
        return result is not None
    
    def start(self):
        """Start output (requires internal enable mode)"""
        return self.set_state(self.STATE_START)
    
    def stop(self):
        """Stop output"""
        return self.set_state(self.STATE_STOP)
    
    def set_internal_enable(self):
        """Set internal (software) enable mode"""
        return self.set_state(self.STATE_ENABLE_INTERNAL)
    
    def set_external_enable(self):
        """Set external (hardware pin) enable mode"""
        return self.set_state(self.STATE_ENABLE_EXTERNAL)
    
    def set_internal_current(self):
        """Set internal (software) current control mode"""
        return self.set_state(self.STATE_CURRENT_INTERNAL)
    
    def set_external_current(self):
        """Set external (analog pin) current control mode"""
        return self.set_state(self.STATE_CURRENT_EXTERNAL)
    
    def get_state(self):
        """
        Get device state
        
        Returns:
            dict: State information, or None on error
            {
                'powered': bool,
                'started': bool,
                'current_internal': bool,
                'enable_internal': bool,
                'ntc_denied': bool,
                'interlock_denied': bool,
                'raw_value': int
            }
        """
        response = self._send_command('J', self.CMD_STATE)
        value = self._parse_response(response, self.CMD_STATE)
        
        if value is not None:
            return {
                'powered': bool(value & self.BIT_POWERED),
                'started': bool(value & self.BIT_STARTED),
                'current_internal': bool(value & self.BIT_CURRENT_INT),
                'enable_internal': bool(value & self.BIT_ENABLE_INT),
                'ntc_denied': bool(value & self.BIT_NTC_DENIED),
                'interlock_denied': bool(value & self.BIT_INTERLOCK_DENIED),
                'raw_value': value
            }
        return None
    
    def get_lock_status(self):
        """
        Get lock/protection status
        
        Returns:
            dict: Lock status information, or None on error
            {
                'interlock': bool,
                'crowbar': bool,
                'overcurrent': bool,
                'overheat': bool,
                'ntc_interlock': bool,
                'raw_value': int
            }
        """
        response = self._send_command('J', self.CMD_LOCK_STATUS)
        value = self._parse_response(response, self.CMD_LOCK_STATUS)
        
        if value is not None:
            return {
                'interlock': bool(value & self.LOCK_INTERLOCK),
                'crowbar': bool(value & self.LOCK_CROWBAR),
                'overcurrent': bool(value & self.LOCK_OVERCURRENT),
                'overheat': bool(value & self.LOCK_OVERHEAT),
                'ntc_interlock': bool(value & self.LOCK_NTC),
                'raw_value': value
            }
        return None
    
    def get_pcb_temperature(self):
        """
        Get PCB temperature
        
        Returns:
            float: Temperature in Celsius, or None on error
        """
        response = self._send_command('J', self.CMD_PCB_TEMP)
        value = self._parse_response(response, self.CMD_PCB_TEMP)
        
        if value is not None:
            return value / 10.0  # Convert from 0.1°C units
        return None
    
    def get_ntc_temperature(self):
        """
        Get NTC sensor temperature
        
        Returns:
            float: Temperature in Celsius, or None on error
        """
        response = self._send_command('J', self.CMD_NTC_TEMP)
        value = self._parse_response(response, self.CMD_NTC_TEMP)
        
        if value is not None:
            return value / 10.0  # Convert from 0.1°C units
        return None
        
    def set_ntc_lower_limit(self, temperature_celsius):
        """
        Set NTC lower temperature limit
        
        Args:
            temperature_celsius (float): Temperature in Celsius (-10 to 150°C)
            
        Returns:
            bool: True if successful
        """
        if not -10 <= temperature_celsius <= 150:
            return False
        
        # Convert to 0.1°C units
        value = int(temperature_celsius * 10)
        value_hex = f"{value:04X}"
        
        result = self._send_command('P', self.CMD_NTC_LOWER, value_hex)
        return result is not None
    
    def set_ntc_upper_limit(self, temperature_celsius):
        """
        Set NTC upper temperature limit
        
        Args:
            temperature_celsius (float): Temperature in Celsius (-10 to 150°C)
            
        Returns:
            bool: True if successful
        """
        if not -10 <= temperature_celsius <= 150:
            return False
        
        # Convert to 0.1°C units
        value = int(temperature_celsius * 10)
        value_hex = f"{value:04X}"
        
        result = self._send_command('P', self.CMD_NTC_UPPER, value_hex)
        return result is not None
    
    def get_ntc_lower_limit(self):
        """
        Get NTC lower temperature limit
        
        Returns:
            float: Temperature in Celsius, or None on error
        """
        response = self._send_command('J', self.CMD_NTC_LOWER)
        value = self._parse_response(response, self.CMD_NTC_LOWER)
        
        if value is not None:
            return value / 10.0
        return None
    
    def get_ntc_upper_limit(self):
        """
        Get NTC upper temperature limit
        
        Returns:
            float: Temperature in Celsius, or None on error
        """
        response = self._send_command('J', self.CMD_NTC_UPPER)
        value = self._parse_response(response, self.CMD_NTC_UPPER)
        
        if value is not None:
            return value / 10.0
        return None
    
    def set_ntc_b_coefficient(self, b_value):
        """
        Set NTC B25/100 coefficient

        Args:
            b_value (int): B coefficient (typically 2000-6000)

        Returns:
            bool: True if successful
        """
        if not 2000 <= b_value <= 6000:
            return False

        value_hex = f"{b_value:04X}"

        result = self._send_command('P', self.CMD_NTC_B_COEFF, value_hex)
        return result is not None

    def get_ntc_b_coefficient(self):
        """
        Get NTC B25/100 coefficient

        Returns:
            int: B coefficient, or None on error
        """
        response = self._send_command('J', self.CMD_NTC_B_COEFF)
        value = self._parse_response(response, self.CMD_NTC_B_COEFF)

        return value
    
    def get_device_id(self):
        """
        Get device model and version ID
        
        Returns:
            int: Device ID, or None on error
        """
        response = self._send_command('J', self.CMD_DEVICE_ID)
        value = self._parse_response(response, self.CMD_DEVICE_ID)
        return value
    
    def get_serial_number(self):
        """
        Get device serial number
        
        Returns:
            int: Serial number, or None on error
        """
        response = self._send_command('J', self.CMD_SERIAL_NUMBER)
        value = self._parse_response(response, self.CMD_SERIAL_NUMBER)
        return value

def test():
    # === TEST CODE ===
    if __name__ == "__main__":
        """Test the SF6100 serial communication"""
        
        print("SF6100 Serial Communication Test")
        print("=" * 50)
        
        # Create driver instance
        driver = SF6100Serial()
        
        # List available ports
        print("\nAvailable serial ports:")
        ports = driver.list_ports()
        for i, port in enumerate(ports):
            print(f"  {i}: {port}")
        
        if not ports:
            print("No serial ports found!")
            exit()
        
        # Get user input for port
        port_index = input(f"\nSelect port (0-{len(ports)-1}): ")
        try:
            selected_port = ports[int(port_index)]
        except:
            print("Invalid selection!")
            exit()
        
        # Connect
        print(f"\nConnecting to {selected_port}...")
        if driver.connect(selected_port):
            print("✓ Connected!")
            
            # Get device info
            serial_num = driver.get_serial_number()
            device_id = driver.get_device_id()
            print(f"\nDevice Info:")
            print(f"  Serial Number: {serial_num if serial_num else 'N/A'}")
            print(f"  Device ID: {device_id if device_id else 'N/A'}")
            
            # Get state
            state = driver.get_state()
            if state:
                print(f"\nDevice State:")
                print(f"  Powered: {state['powered']}")
                print(f"  Started: {state['started']}")
                print(f"  Enable Mode: {'Internal' if state['enable_internal'] else 'External'}")
                print(f"  Current Mode: {'Internal' if state['current_internal'] else 'External'}")
            
            # Get measurements
            current = driver.get_current_measured()
            voltage = driver.get_voltage_measured()
            pcb_temp = driver.get_pcb_temperature()
            
            print(f"\nMeasurements:")
            print(f"  Output Current: {current if current is not None else 'N/A'} A")
            print(f"  Output Voltage: {voltage if voltage is not None else 'N/A'} V")
            print(f"  PCB Temperature: {pcb_temp if pcb_temp is not None else 'N/A'} °C")
            
            # Get lock status
            locks = driver.get_lock_status()
            if locks:
                print(f"\nProtection Status:")
                print(f"  Interlock: {'ACTIVE' if locks['interlock'] else 'OK'}")
                print(f"  Overcurrent: {'FAULT' if locks['overcurrent'] else 'OK'}")
                print(f"  Overheat: {'WARNING' if locks['overheat'] else 'OK'}")
            
            # Disconnect
            driver.disconnect()
            print("\n✓ Disconnected")
            
        else:
            print("✗ Connection failed!")