/*
 * Dual-Channel Synchronized Trigger Generator
 * Simplified Binary-Only Protocol
 * 
 * Hardware: Arduino Uno
 * Pin 9  (OC1A): Spectrometer trigger output
 * Pin 10 (OC1B): Laser driver trigger output
 * 
 * Binary Protocol Commands:
 * 'S' - Start triggers
 * 'X' - Stop triggers  
 * 'Q' - Query status
 * 'P' + 10 bytes - Set all parameters
 */

// Control structure
struct Config {
  uint32_t cycles;      // Number of cycles (0 = continuous)
  uint16_t frequency;   // Frequency in Hz (1-1000)
  uint8_t duty_spec;    // Spectrometer duty cycle (0-100%)
  uint8_t duty_laser;   // Laser duty cycle (0-100%)
  int16_t offset_us;    // Laser offset in microseconds (signed)
} config;

// State variables
volatile bool running = false;
volatile uint32_t cycle_count = 0;
volatile bool cycle_complete = false;

// Timer parameters
uint16_t timer_top;
uint16_t spec_compare;
uint16_t laser_compare;

void setup() {
  Serial.begin(115200);
  
  // Initialize default config
  config.cycles = 0;        // Continuous
  config.frequency = 100;   // 100 Hz
  config.duty_spec = 50;    // 50%
  config.duty_laser = 50;   // 50%
  config.offset_us = 0;     // No offset
  
  // Setup Timer1 for PWM
  setupTimer();
  
  // Don't print anything on startup - stay silent
}

void loop() {
  // Process serial commands
  if (Serial.available() > 0) {
    char cmd = Serial.read();
    
    switch (cmd) {
      case 'S':  // Start
        startTriggers();
        Serial.println(F("OK:STARTED"));
        break;
        
      case 'X':  // Stop
        stopTriggers();
        Serial.print(F("OK:STOPPED:"));
        Serial.println(cycle_count);
        break;
        
      case 'Q':  // Query status
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
        break;
        
      case 'P':  // Set all parameters (10 bytes follow)
        if (waitForBytes(10)) {
          // Read parameters
          config.cycles = readUint32();
          config.frequency = readUint16();
          config.duty_spec = Serial.read();
          config.duty_laser = Serial.read();
          config.offset_us = (int16_t)readUint16();
          
          // Validate
          if (config.frequency >= 1 && config.frequency <= 1000 &&
              config.duty_spec <= 100 && config.duty_laser <= 100) {
            
            // Recalculate timer if running
            if (running) {
              stopTriggers();
              calculateTimerParams();
              setupTimer();
              startTriggers();
            } else {
              calculateTimerParams();
              setupTimer();
            }
            
            Serial.println(F("OK:PARAMS_SET"));
          } else {
            Serial.println(F("ERR:PARAM_RANGE"));
          }
        } else {
          Serial.println(F("ERR:TIMEOUT"));
        }
        break;
        
      default:
        // Unknown command - ignore silently
        break;
    }
    
    // Flush any remaining bytes (newlines, etc)
    delay(10);
    while (Serial.available() > 0) {
      Serial.read();
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

// ============================================================================
// TIMER FUNCTIONS
// ============================================================================

void setupTimer() {
  // Stop timer
  TCCR1B = 0;
  
  // Configure pins as outputs
  pinMode(9, OUTPUT);   // OC1A - Spectrometer
  pinMode(10, OUTPUT);  // OC1B - Laser
  
  // Calculate timer parameters
  calculateTimerParams();
  
  // Phase and Frequency Correct PWM mode (mode 8)
  // COM1A1, COM1B1 = 1 for non-inverting mode
  TCCR1A = (1 << COM1A1) | (1 << COM1B1) | (1 << WGM10);
  
  // WGM13 = 1 for phase/freq correct PWM with ICR1 as TOP
  // CS11 = 1 for prescaler = 8
  TCCR1B = (1 << WGM13) | (1 << CS11);
  
  // Set TOP value
  ICR1 = timer_top;
  
  // Set compare values
  OCR1A = spec_compare;
  OCR1B = laser_compare;
  
  // Enable overflow interrupt for cycle counting
  TIMSK1 = (1 << TOIE1);
  
  // Start with outputs low
  digitalWrite(9, LOW);
  digitalWrite(10, LOW);
}

void calculateTimerParams() {
  // Calculate TOP for frequency
  timer_top = 16000000UL / (2 * 8 * (uint32_t)config.frequency);
  
  // Calculate base duty cycle values WITHOUT offset
  spec_compare = ((uint32_t)timer_top * config.duty_spec) / 100;
  laser_compare = ((uint32_t)timer_top * config.duty_laser) / 100;
  
}

void startTriggers() {
  cycle_count = 0;
  cycle_complete = false;
  
  // Stop timer
  TCCR1A = 0;
  TCCR1B = 0;
  TCNT1 = 0;
  
  // Force outputs LOW initially
  pinMode(9, OUTPUT);
  pinMode(10, OUTPUT);
  digitalWrite(9, LOW);
  digitalWrite(10, LOW);
  
  // Set TOP and compare values  
  ICR1 = timer_top;
  OCR1A = spec_compare;
  OCR1B = laser_compare;
  
  // Use Fast PWM mode (mode 14) instead of Phase-Correct
  // In Fast PWM: 
  // - Timer counts up from 0 to TOP
  // - Output goes HIGH at BOTTOM (0)
  // - Output goes LOW at compare match
  // - Rising edge is predictable at timer=0
  
  TCCR1A = (1 << COM1A1) | (1 << COM1B1) | (1 << WGM11);  // Fast PWM, non-inverting
  TCCR1B = (1 << WGM13) | (1 << WGM12) | (1 << CS11);      // Fast PWM mode 14, prescaler=8
  
  // For negative offset: delay the spec channel instead
  // This way laser rising edge comes first
  if (config.offset_us < 0) {
    // Laser fires first - delay spec by |offset|
    int32_t offset_ticks = -(int32_t)config.offset_us * 2;
    OCR1A = spec_compare + offset_ticks;  // Delay spec
    OCR1B = laser_compare;                 // Laser at normal time
  } else if (config.offset_us > 0) {
    // Spec fires first - delay laser
    int32_t offset_ticks = (int32_t)config.offset_us * 2;
    OCR1A = spec_compare;                  // Spec at normal time
    OCR1B = laser_compare + offset_ticks;  // Delay laser
  }
  
  // Enable overflow interrupt
  TIMSK1 = (1 << TOIE1);
  
  running = true;
}

void stopTriggers() {
  running = false;
  
  // Stop timer by clearing prescaler bits
  TCCR1B = (1 << WGM13);  // ← Keep WGM13, just remove CS11
  
  // Set outputs low
  digitalWrite(9, LOW);
  digitalWrite(10, LOW);
}

// Timer1 overflow interrupt - count cycles
ISR(TIMER1_OVF_vect) {
  if (running) {
    cycle_count++;
    cycle_complete = true;
  }
}

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

bool waitForBytes(uint8_t count) {
  uint32_t start = millis();
  while (Serial.available() < count) {
    if (millis() - start > 5000) {  // 5 second timeout
      return false;
    }
    delay(1);
  }
  return true;
}

uint32_t readUint32() {
  uint32_t value = 0;
  value |= (uint32_t)Serial.read();
  value |= (uint32_t)Serial.read() << 8;
  value |= (uint32_t)Serial.read() << 16;
  value |= (uint32_t)Serial.read() << 24;
  return value;
}

uint16_t readUint16() {
  uint16_t value = 0;
  value |= (uint16_t)Serial.read();
  value |= (uint16_t)Serial.read() << 8;
  return value;
}
