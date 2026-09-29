/*
 * Dual-Channel Synchronized Trigger Generator - OPTIMIZED
 * 
 * Hardware: Arduino Uno
 * Pin 9  (OC1A): Spectrometer trigger output
 * Pin 10 (OC1B): Laser driver trigger output
 * 
 * Key Features:
 * - Fast PWM mode for predictable rising edges
 * - Offset measured between rising edges
 * - Both channels start LOW
 * - First pulse respects offset timing
 */

// Configuration
struct {
  uint32_t cycles;      // 0 = continuous
  uint16_t frequency;   // 1-1000 Hz
  uint8_t duty_spec;    // 0-100%
  uint8_t duty_laser;   // 0-100%
  int16_t offset_us;    // Signed offset in microseconds
} config = {0, 100, 50, 50, 0};  // Default values

// State
volatile bool running = false;
volatile uint32_t cycle_count = 0;

// Timer values (calculated from config)
uint16_t timer_top;
uint16_t spec_compare;
uint16_t laser_compare;

void setup() {
  Serial.begin(115200);
  pinMode(9, OUTPUT);
  pinMode(10, OUTPUT);
  digitalWrite(9, LOW);
  digitalWrite(10, LOW);
  
  // Pre-calculate timer values
  updateTimerValues();
}

void loop() {
  if (Serial.available() > 0) {
    char cmd = Serial.read();
    
    switch (cmd) {
      case 'S':  // Start
        start();
        Serial.println(F("OK:STARTED"));
        break;
        
      case 'X':  // Stop
        stop();
        Serial.print(F("OK:STOPPED:"));
        Serial.println(cycle_count);
        break;
        
      case 'Q':  // Query status
        printStatus();
        break;
        
      case 'P':  // Set all parameters
        if (readParameters()) {
          updateTimerValues();
          Serial.println(F("OK:PARAMS_SET"));
        }
        break;
    }
    
    // Flush remaining bytes
    delay(5);
    while (Serial.available()) Serial.read();
  }
  
  // Auto-stop when cycles complete
  if (running && config.cycles > 0 && cycle_count >= config.cycles) {
    stop();
    Serial.println(F("OK:CYCLES_COMPLETE"));
  }
}

// ============================================================================
// TIMER CONTROL
// ============================================================================

void updateTimerValues() {
  // Calculate TOP value for frequency
  // Fast PWM: f = F_CPU / (prescaler * (1 + TOP))
  timer_top = (16000000UL / (8UL * config.frequency)) - 1;
  
  // Calculate compare values for duty cycles
  // Compare match clears output (output is HIGH from 0 to compare)
  spec_compare = ((uint32_t)(timer_top + 1) * config.duty_spec) / 100;
  laser_compare = ((uint32_t)(timer_top + 1) * config.duty_laser) / 100;
}

volatile uint16_t laser_start_ticks = 0;
volatile uint16_t laser_end_ticks = 0;
volatile bool use_software_laser = false;

void start() {
  cycle_count = 0;
  running = true;
  
  TCCR1A = 0;
  TCCR1B = 0;
  TCNT1 = 0;
  
  digitalWrite(9, LOW);
  digitalWrite(10, LOW);
  
  ICR1 = timer_top;
  
  // Calculate offset in ticks (0.5μs per tick)
  int32_t offset_ticks = (int32_t)config.offset_us * 2;
  
  if (offset_ticks == 0) {
    // No offset - hardware PWM for both
    use_software_laser = false;
    OCR1A = spec_compare;
    OCR1B = laser_compare;
    TCCR1A = _BV(COM1A1) | _BV(COM1B1) | _BV(WGM11);
    
  } else {
    // Use software control for laser timing
    use_software_laser = true;
    OCR1A = spec_compare;  // Spec uses hardware PWM
    
    // Calculate when laser should toggle
    laser_start_ticks = (offset_ticks < 0) ? (timer_top + offset_ticks) : offset_ticks;
    laser_end_ticks = laser_start_ticks + laser_compare;
    
    // Wrap around if needed
    if (laser_start_ticks > timer_top) laser_start_ticks -= timer_top;
    if (laser_end_ticks > timer_top) laser_end_ticks -= timer_top;
    
    // Spec uses hardware, laser controlled in ISR
    TCCR1A = _BV(COM1A1) | _BV(WGM11);  // Only spec hardware PWM
    
    // Enable compare match interrupt for laser timing
    OCR1B = laser_start_ticks;
    TIMSK1 = _BV(TOIE1) | _BV(OCIE1B);  // Overflow + Compare B interrupt
  }
  
  TCCR1B = _BV(WGM13) | _BV(WGM12) | _BV(CS11);  // Fast PWM, prescaler=8
  if (!use_software_laser) {
    TIMSK1 = _BV(TOIE1);  // Only overflow for cycle count
  }
}

// Compare match B interrupt - toggle laser at precise time
ISR(TIMER1_COMPB_vect) {
  if (use_software_laser) {
    static bool laser_high = false;
    
    if (!laser_high) {
      // Rising edge
      digitalWrite(10, HIGH);
      OCR1B = laser_end_ticks;  // Set next interrupt for falling edge
      laser_high = true;
    } else {
      // Falling edge
      digitalWrite(10, LOW);
      OCR1B = laser_start_ticks;  // Set next interrupt for rising edge
      laser_high = false;
    }
  }
}

void stop() {
  running = false;
  
  // Stop timer
  TCCR1B = 0;
  TIMSK1 = 0;
  
  // Force outputs LOW
  TCCR1A = 0;
  digitalWrite(9, LOW);
  digitalWrite(10, LOW);
}

// Overflow interrupt - count cycles
ISR(TIMER1_OVF_vect) {
  if (running) {
    cycle_count++;
  }
}

// ============================================================================
// COMMUNICATION
// ============================================================================

bool readParameters() {
  // Wait for 10 bytes with timeout
  uint32_t start = millis();
  while (Serial.available() < 10) {
    if (millis() - start > 5000) {
      Serial.println(F("ERR:TIMEOUT"));
      return false;
    }
  }
  
  // Read parameters
  config.cycles = readUint32();
  config.frequency = readUint16();
  config.duty_spec = Serial.read();
  config.duty_laser = Serial.read();
  config.offset_us = (int16_t)readUint16();
  
  // Validate
  if (config.frequency < 1 || config.frequency > 1000 ||
      config.duty_spec > 100 || config.duty_laser > 100) {
    Serial.println(F("ERR:PARAM_RANGE"));
    return false;
  }
  
  return true;
}

void printStatus() {
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

uint32_t readUint32() {
  uint32_t val = Serial.read();
  val |= (uint32_t)Serial.read() << 8;
  val |= (uint32_t)Serial.read() << 16;
  val |= (uint32_t)Serial.read() << 24;
  return val;
}

uint16_t readUint16() {
  uint16_t val = Serial.read();
  val |= (uint16_t)Serial.read() << 8;
  return val;
}
