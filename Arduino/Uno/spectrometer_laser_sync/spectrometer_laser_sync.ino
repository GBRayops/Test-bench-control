/*
 * Dual-Channel Precision Trigger Generator for Spectrometer & Laser Driver
 * Target: Arduino Uno (ATmega328P)
 * 
 * Hardware Connections:
 * - Pin 9 (PB1/OC1A): Spectrometer Trigger Output
 * - Pin 10 (PB2/OC1B): Laser Driver Trigger Output
 * - USB: Serial communication with Python GUI
 * 
 * Features:
 * - Direct register manipulation for precise timing
 * - Hardware Timer1 (16-bit) for microsecond precision
 * - Independent duty cycle control for each channel
 * - Programmable phase offset between channels
 * - Cycle counting with automatic stop
 * - Serial command interface
 */

// Pin definitions (using PORT B direct manipulation)
#define SPEC_PIN 9   // PB1 (OC1A) - Spectrometer trigger
#define LASER_PIN 10 // PB2 (OC1B) - Laser driver trigger
#define time_out_delay 5000

// Control structure
struct TriggerConfig {
  uint32_t cycles;          // Number of cycles to execute (0 = continuous)
  uint16_t frequency;       // Frequency in Hz (1-1000 Hz)
  uint8_t duty_spec;        // Spectrometer duty cycle (0-100%)
  uint8_t duty_laser;       // Laser duty cycle (0-100%)
  int16_t offset_us;        // Laser offset in microseconds (negative = laser before spec, positive = laser after spec)
} config;

// Runtime variables
volatile uint32_t cycle_count = 0;
volatile bool running = false;
volatile bool cycle_complete = false;

// Timer calculation variables
uint16_t timer_top;           // ICR1 value for frequency
uint16_t spec_compare;        // OCR1A value for spec duty cycle
uint16_t laser_compare;       // OCR1B value for laser duty cycle
int16_t laser_offset_ticks;   // Laser offset in timer ticks (can be negative)

void setup() {
  // Initialize serial communication
  Serial.begin(115200);
  Serial.setTimeout(100);
  
  // Configure pins as outputs using DDR register
  DDRB |= (1 << DDB1) | (1 << DDB2);  // Set PB1 and PB2 as outputs
  PORTB &= ~((1 << PB1) | (1 << PB2)); // Set outputs LOW initially
  
  // Default configuration
  config.cycles = 0;        // Continuous mode
  config.frequency = 100;   // 100 Hz
  config.duty_spec = 50;    // 50% duty cycle
  config.duty_laser = 50;   // 50% duty cycle
  config.offset_us = 0;     // No offset
  
  // Disable Timer1 initially
  stopTimer();
  
  //Serial.println(F("Dual-Channel Trigger Generator Ready"));
  //Serial.println(F("Commands: START, STOP, STATUS, CONFIG"));
  //printConfig();
}

void loop() {
  // Check for serial commands
  if (Serial.available() > 0) {
    // Peek at first character to determine if binary or text command
    char first = Serial.peek();
    
    if (first == 'S' && Serial.available() >= 5) {
      // Could be "START" text command, check if followed by 'T'
      Serial.read(); // consume 'S'
      char second = Serial.peek();
      if (second == 'T' || second == 't') {
        // Text command, reconstruct
        processTextCommand('S');
      } else {
        // Binary START command
        startTriggers();
        Serial.println(F("OK:STARTED"));
      }
    }
    else if (first >= 'A' && first <= 'Z') {
      // Binary command
      processBinaryCommand();
    }
    else {
      // Text command
      processTextCommand(0);
    }
  }
  
  // Check if cycle count reached
  if (running && config.cycles > 0 && cycle_complete) {
    cycle_complete = false;
    if (cycle_count >= config.cycles) {
      stopTriggers();
      Serial.println(F("OK:CYCLES_COMPLETE"));
    }
  }
}

void processBinaryCommand() {
  // Protocol: Single byte command followed by parameters
  // Commands:
  // 'S' - START
  // 'X' - STOP
  // 'Q' - Query STATUS
  // 'C' - Set CYCLES (4 bytes follow: uint32_t)
  // 'F' - Set FREQUENCY (2 bytes follow: uint16_t)
  // 'A' - Set duty cycle SPEC (1 byte: uint8_t)
  // 'B' - Set duty cycle LASER (1 byte: uint8_t)
  // 'O' - Set OFFSET (2 bytes follow: uint16_t microseconds)
  // 'P' - Upload complete PARAMETER set (11 bytes total)
  // '?' - Get help/config
  
  char cmd = Serial.read();
  
  switch(cmd) {
    case 'S':
      startTriggers();
      Serial.println(F("OK:STARTED"));
      break;
      
    case 'X':
      stopTriggers();
      Serial.print(F("OK:STOPPED:"));
      Serial.println(cycle_count);
      break;
      
    case 'Q':
      sendStatus();
      break;
      
    case 'C':
      if (waitForBytes(4, time_out_delay)) {
        config.cycles = readUint32();
        Serial.print(F("OK:CYCLES:"));
        Serial.println(config.cycles);
      } else {
        Serial.println(F("ERR:TIMEOUT"));
      }
      break;
      
    case 'F':
      if (waitForBytes(2, time_out_delay)) {
        uint16_t freq = readUint16();
        if (freq >= 1 && freq <= 1000) {
          config.frequency = freq;
          Serial.print(F("OK:FREQ:"));
          Serial.println(config.frequency);
        } else {
          Serial.println(F("ERR:FREQ_RANGE"));
        }
      } else {
        Serial.println(F("ERR:TIMEOUT"));
      }
      break;
      
    case 'A':
      if (waitForBytes(1, time_out_delay)) {
        uint8_t duty = Serial.read();
        if (duty <= 100) {
          config.duty_spec = duty;
          Serial.print(F("OK:DUTY_SPEC:"));
          Serial.println(config.duty_spec);
        } else {
          Serial.println(F("ERR:DUTY_RANGE"));
        }
      } else {
        Serial.println(F("ERR:TIMEOUT"));
      }
      break;
      
    case 'B':
      if (waitForBytes(1, time_out_delay)) {
        uint8_t duty = Serial.read();
        if (duty <= 100) {
          config.duty_laser = duty;
          Serial.print(F("OK:DUTY_LASER:"));
          Serial.println(config.duty_laser);
        } else {
          Serial.println(F("ERR:DUTY_RANGE"));
        }
      } else {
        Serial.println(F("ERR:TIMEOUT"));
      }
      break;
      
    case 'O':
      if (waitForBytes(2, time_out_delay)) {
        // Read as signed int16_t
        int16_t offset_value;
        offset_value = (int16_t)readUint16();
        config.offset_us = offset_value;
        Serial.print(F("OK:OFFSET:"));
        Serial.println(config.offset_us);
      } else {
        Serial.println(F("ERR:TIMEOUT"));
      }
      break;
      
    case 'P':
      //Serial.println(F("DEBUG:P_CMD_RECEIVED"));  // ← Add this
      
      //Serial.println(F("DEBUG:BYTES_ARRIVED"));  // ← Add this
        
      config.cycles = readUint32();
      config.frequency = readUint16();
      config.duty_spec = Serial.read();
      config.duty_laser = Serial.read();
      config.offset_us = (int16_t)readUint16();
      while (Serial.available() > 0) {
        char lol = Serial.read(); 
        Serial.println("lol"); // Discard any remaining bytes
      }
      if (config.frequency >= 1 && config.frequency <= 1000 &&
          config.duty_spec <= 100 && config.duty_laser <= 100) {
        Serial.println(F("OK:PARAMS_SET"));
      } 
      else {
        Serial.println(F("ERR:PARAM_RANGE"));
      }

      break;
      
    case '?':
      printConfig();
      printHelp();
      break;
      
    default:
      Serial.println(F("ERR:UNKNOWN_CMD"));
      break;
  }
  // Flush serial buffer to prevent ERR:UNKNOWN from trailing characters
  while (Serial.available() > 0) {
    Serial.read();  // Discard any remaining bytes
  }
}

void processTextCommand(char first) {
  // Process text commands for manual testing via Serial Monitor
  String cmd = "";
  
  if (first != 0) {
    cmd += first;
  }
  
  cmd += Serial.readStringUntil('\n');
  cmd.trim();
  cmd.toUpperCase();
  
  if (cmd == "START" || cmd == "TART") { // Handle "START" where 'S' was already read
    startTriggers();
    Serial.println(F("OK:STARTED"));
  }
  else if (cmd == "STOP") {
    stopTriggers();
    Serial.print(F("OK:STOPPED:"));
    Serial.println(cycle_count);
  }
  else if (cmd == "STATUS") {
    sendStatus();
    printStatus();
  }
  else if (cmd == "CONFIG") {
    printConfig();
  }
  else if (cmd.startsWith("SET ")) {
    parseSetCommand(cmd);
  }
  else {
    Serial.println(F("ERR:UNKNOWN"));
  }
}

void parseSetCommand(String cmd) {
  int firstSpace = cmd.indexOf(' ');
  int secondSpace = cmd.indexOf(' ', firstSpace + 1);
  
  if (secondSpace == -1) {
    Serial.println(F("ERR:SYNTAX"));
    return;
  }
  
  String param = cmd.substring(firstSpace + 1, secondSpace);
  uint32_t value = cmd.substring(secondSpace + 1).toInt();
  
  if (param == "CYCLES") {
    config.cycles = value;
    Serial.print(F("OK:CYCLES:"));
    Serial.println(config.cycles);
  }
  else if (param == "FREQ") {
    if (value >= 1 && value <= 1000) {
      config.frequency = value;
      Serial.print(F("OK:FREQ:"));
      Serial.println(config.frequency);
    } else {
      Serial.println(F("ERR:FREQ_RANGE"));
    }
  }
  else if (param == "DUTY_SPEC") {
    if (value <= 100) {
      config.duty_spec = value;
      Serial.print(F("OK:DUTY_SPEC:"));
      Serial.println(config.duty_spec);
    } else {
      Serial.println(F("ERR:DUTY_RANGE"));
    }
  }
  else if (param == "DUTY_LASER") {
    if (value <= 100) {
      config.duty_laser = value;
      Serial.print(F("OK:DUTY_LASER:"));
      Serial.println(config.duty_laser);
    } else {
      Serial.println(F("ERR:DUTY_RANGE"));
    }
  }
  else if (param == "OFFSET") {
    config.offset_us = value;
    Serial.print(F("OK:OFFSET:"));
    Serial.println(config.offset_us);
  }
  else {
    Serial.println(F("ERR:UNKNOWN_PARAM"));
  }
}

// Helper functions for reading multi-byte values
bool waitForBytes(int count, unsigned long timeout_ms) {
  unsigned long start = millis();
  while (Serial.available() < count) {
    if (millis() - start > timeout_ms) {
      return false;
    }
  }
  return true;
}

uint16_t readUint16() {
  uint16_t value = 0;
  value = Serial.read();
  value |= ((uint16_t)Serial.read()) << 8;
  return value;
}

uint32_t readUint32() {
  uint32_t value = 0;
  value = Serial.read();
  value |= ((uint32_t)Serial.read()) << 8;
  value |= ((uint32_t)Serial.read()) << 16;
  value |= ((uint32_t)Serial.read()) << 24;
  return value;
}

void sendStatus() {
  // Send status in parseable format
  // Format: STATUS,running,cycles_completed,frequency,duty_spec,duty_laser,offset
  Serial.print(F("STATUS,"));
  Serial.print(running ? 1 : 0);
  Serial.print(',');
  Serial.print(cycle_count);
  Serial.print(',');
  Serial.print(config.frequency);
  Serial.print(',');
  Serial.print(config.duty_spec);
  Serial.print(',');
  Serial.print(config.duty_laser);
  Serial.print(',');
  Serial.println(config.offset_us);
}

void startTriggers() {
  if (running) {
    Serial.println(F("Already running"));
    return;
  }
  
  // Calculate timer parameters
  calculateTimerParams();
  
  // Reset cycle counter
  cycle_count = 0;
  cycle_complete = false;
  
  // Configure Timer1 for Phase and Frequency Correct PWM
  // This mode provides symmetric PWM with updates at BOTTOM
  
  // Stop timer
  TCCR1A = 0;
  TCCR1B = 0;
  TCNT1 = 0;
  
  // Set ICR1 as TOP for frequency control
  ICR1 = timer_top;
  
  // Set compare values for duty cycles
  OCR1A = spec_compare;
  OCR1B = laser_compare;
  
  // Configure Timer1 Control Registers
  // COM1A1:0 = 10 (Clear OC1A on compare match, set at BOTTOM)
  // COM1B1:0 = 10 (Clear OC1B on compare match, set at BOTTOM)
  // WGM13:10 = 1000 (Phase and Frequency Correct PWM, TOP=ICR1)
  TCCR1A = (1 << COM1A1) | (1 << COM1B1);
  
  // CS12:10 = 010 (prescaler = 8, gives 2MHz timer clock at 16MHz CPU)
  // WGM13 = 1
  TCCR1B = (1 << WGM13) | (1 << CS11);
  
  // Enable Timer1 overflow interrupt for cycle counting
  TIMSK1 = (1 << TOIE1);
  
  running = true;
  
  Serial.println(F("Triggers started"));
  printConfig();
}

void stopTriggers() {
  if (!running) {
    Serial.println(F("Not running"));
    return;
  }
  
  stopTimer();
  
  Serial.print(F("Triggers stopped. Cycles completed: "));
  Serial.println(cycle_count);
}

void stopTimer() {
  // Disable timer
  TCCR1A = 0;
  TCCR1B = 0;
  TIMSK1 = 0;
  TCNT1 = 0;
  
  // Set outputs LOW
  PORTB &= ~((1 << PB1) | (1 << PB2));
  
  running = false;
}

void calculateTimerParams() {
  // Timer1 clock: 16MHz / 8 (prescaler) = 2MHz = 0.5us per tick
  // Period = TOP * 2 * prescaler / F_CPU
  // For Phase and Frequency Correct PWM: f_pwm = F_CPU / (2 * prescaler * TOP)
  // TOP = F_CPU / (2 * prescaler * f_pwm)
  
  const uint32_t F_CPU_VAL = 16000000UL;
  const uint16_t prescaler = 8;
  
  // Calculate TOP value for desired frequency
  timer_top = F_CPU_VAL / (2UL * prescaler * (uint32_t)config.frequency);
  
  // Calculate compare values for duty cycles
  spec_compare = ((uint32_t)timer_top * config.duty_spec) / 100;
  laser_compare = ((uint32_t)timer_top * config.duty_laser) / 100;
  
  // Calculate offset in timer ticks (can be negative)
  // 1 tick = 0.5us, so offset_us * 2 = ticks
  laser_offset_ticks = config.offset_us * 2;
  
  // Apply phase shift to laser channel
  // Negative offset = laser fires BEFORE spec (advance phase)
  // Positive offset = laser fires AFTER spec (delay phase)
  
  if (laser_offset_ticks != 0) {
    // Calculate the phase-shifted starting point for laser
    // We do this by offsetting the OCR1B compare value
    // Note: In Phase & Frequency Correct PWM, output toggles at BOTTOM and at compare match
    
    int32_t shifted_compare = (int32_t)laser_compare + laser_offset_ticks;
    
    // Wrap around if needed (handle negative and overflow)
    while (shifted_compare < 0) {
      shifted_compare += (int32_t)timer_top;
    }
    while (shifted_compare > (int32_t)timer_top) {
      shifted_compare -= (int32_t)timer_top;
    }
    
    laser_compare = (uint16_t)shifted_compare;
  }
}

// Timer1 overflow interrupt - called each complete cycle
ISR(TIMER1_OVF_vect) {
  cycle_count++;
  cycle_complete = true;
}

void printConfig() {
  Serial.println(F("=== Configuration ==="));
  Serial.print(F("Cycles: "));
  if (config.cycles == 0) {
    Serial.println(F("Continuous"));
  } else {
    Serial.println(config.cycles);
  }
  Serial.print(F("Frequency: "));
  Serial.print(config.frequency);
  Serial.println(F(" Hz"));
  Serial.print(F("Spec Duty: "));
  Serial.print(config.duty_spec);
  Serial.println(F("%"));
  Serial.print(F("Laser Duty: "));
  Serial.print(config.duty_laser);
  Serial.println(F("%"));
  Serial.print(F("Laser Offset: "));
  Serial.print(config.offset_us);
  Serial.println(F(" us"));
  Serial.println(F("===================="));
}

void printStatus() {
  Serial.println(F("=== Status ==="));
  Serial.print(F("Running: "));
  Serial.println(running ? F("YES") : F("NO"));
  Serial.print(F("Cycles completed: "));
  Serial.println(cycle_count);
  if (running) {
    Serial.print(F("Timer TOP: "));
    Serial.println(timer_top);
    Serial.print(F("Spec Compare: "));
    Serial.println(spec_compare);
    Serial.print(F("Laser Compare: "));
    Serial.println(laser_compare);
  }
  Serial.println(F("=============="));
}

void printHelp() {
  Serial.println(F("\n=== Binary Protocol Commands ==="));
  Serial.println(F("'S' - Start triggers"));
  Serial.println(F("'X' - Stop triggers"));
  Serial.println(F("'Q' - Query status (returns CSV)"));
  Serial.println(F("'C' + 4 bytes - Set cycles (uint32_t, little-endian)"));
  Serial.println(F("'F' + 2 bytes - Set frequency Hz (uint16_t, 1-1000)"));
  Serial.println(F("'A' + 1 byte - Set spec duty % (uint8_t, 0-100)"));
  Serial.println(F("'B' + 1 byte - Set laser duty % (uint8_t, 0-100)"));
  Serial.println(F("'O' + 2 bytes - Set offset us (uint16_t)"));
  Serial.println(F("'P' + 11 bytes - Set all params"));
  Serial.println(F("'?' - Show this help"));
  Serial.println(F("\n=== Text Commands (for manual testing) ==="));
  Serial.println(F("START - Start triggers"));
  Serial.println(F("STOP - Stop triggers"));
  Serial.println(F("STATUS - Show status"));
  Serial.println(F("CONFIG - Show configuration"));
  Serial.println(F("SET CYCLES <n>"));
  Serial.println(F("SET FREQ <Hz>"));
  Serial.println(F("SET DUTY_SPEC <0-100>"));
  Serial.println(F("SET DUTY_LASER <0-100>"));
  Serial.println(F("SET OFFSET <us>"));
  Serial.println(F("================================\n"));
}
