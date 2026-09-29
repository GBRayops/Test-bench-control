/*
 * Dual Channel Trigger Generator - Arduino Nano Every Version
 * Optimized with direct port manipulation for ATmega4809
 */

// ============================================================================
// CONFIGURATION
// ============================================================================

struct {
  uint32_t cycles;
  uint16_t frequency;
  uint8_t duty_spec;
  uint8_t duty_laser;
  int16_t offset_us;
} config = {0, 100, 50, 50, 0};

// ============================================================================
// STATE
// ============================================================================

uint32_t cycle_count = 0;
bool running = false;

// Pin definitions for Arduino Nano Every
// Using PORTF pins for fast direct manipulation
// Pin A0 = PF5 (Arduino pin 14)
// Pin A1 = PF4 (Arduino pin 15)
#define SPEC_PIN   5   // PF5 (Arduino A0/D14)
#define LASER_PIN  4   // PF4 (Arduino A1/D15)
#define SPEC_MASK  (1 << SPEC_PIN)
#define LASER_MASK (1 << LASER_PIN)

// Timing in microseconds
uint32_t period_us;
uint32_t spec_high_us;
uint32_t laser_high_us;

// Absolute edge times within the period
uint32_t spec_rise_us;
uint32_t spec_fall_us;
uint32_t laser_rise_us;
uint32_t laser_fall_us;

// Cycle start time
uint32_t cycle_start_us;

// ============================================================================
// SERIAL
// ============================================================================

uint32_t readUint32() {
  uint32_t v = Serial.read();
  v |= (uint32_t)Serial.read() << 8;
  v |= (uint32_t)Serial.read() << 16;
  v |= (uint32_t)Serial.read() << 24;
  return v;
}

uint16_t readUint16() {
  uint16_t v = Serial.read();
  v |= (uint16_t)Serial.read() << 8;
  return v;
}

bool readParameters() {
  uint32_t start = millis();
  while (Serial.available() < 10) {
    if (millis() - start > 5000) {
      Serial.println(F("ERR:TIMEOUT"));
      return false;
    }
  }

  config.cycles = readUint32();
  config.frequency = readUint16();
  config.duty_spec = Serial.read();
  config.duty_laser = Serial.read();
  config.offset_us = (int16_t)readUint16();

  if (config.frequency < 1 || config.frequency > 1000 ||
      config.duty_spec > 100 || config.duty_laser > 100) {
    Serial.println(F("ERR:PARAM_RANGE"));
    return false;
  }

  return true;
}

// ============================================================================
// TIMING
// ============================================================================

void computeTiming() {
  period_us = 1000000UL / config.frequency;
  spec_high_us = (period_us * config.duty_spec) / 100;
  laser_high_us = (period_us * config.duty_laser) / 100;
  
  // Spec edges (reference at t=0)
  spec_rise_us = 0;
  spec_fall_us = spec_high_us;
  
  // Laser edges with offset
  int32_t laser_rise_signed = config.offset_us;
  int32_t laser_fall_signed = config.offset_us + laser_high_us;
  
  // Normalize to 0..period range
  while (laser_rise_signed < 0) laser_rise_signed += period_us;
  while (laser_fall_signed < 0) laser_fall_signed += period_us;
  
  laser_rise_us = laser_rise_signed % period_us;
  laser_fall_us = laser_fall_signed % period_us;
}

// ============================================================================
// GENERATOR
// ============================================================================

void stopGenerator() {
  running = false;
  // Clear both pins - ATmega4809 uses PORTF.OUT for output
  PORTF.OUT &= ~(SPEC_MASK | LASER_MASK);
}

void startGenerator() {
  stopGenerator();
  
  computeTiming();
  
  // Set pins as outputs - ATmega4809 uses PORTF.DIR for direction
  PORTF.DIR |= SPEC_MASK | LASER_MASK;
  // Ensure pins are LOW
  PORTF.OUT &= ~(SPEC_MASK | LASER_MASK);
  
  cycle_count = 0;
  
  // Start cycle_start_us in the past so first pulse is complete
  uint32_t now = micros();
  
  // Find a safe starting point where both pins would be LOW
  uint32_t safe_time = max(spec_fall_us, laser_fall_us);
  if (safe_time < period_us) {
    safe_time += 100;
  }
  if (safe_time >= period_us) {
    safe_time = period_us - 100;
  }
  
  cycle_start_us = now - safe_time;
  
  running = true;
}

void runGenerator() {
  if (!running) {
    PORTF.OUT &= ~(SPEC_MASK | LASER_MASK);
    return;
  }
  
  uint32_t now = micros();
  
  // Handle potential missed cycles (micros() overflow or blocking delays)
  // Use unsigned arithmetic which handles wraparound correctly
  uint32_t elapsed = now - cycle_start_us;
  
  // Catch up if we've missed cycles
  while (elapsed >= period_us) {
    cycle_count++;
    cycle_start_us += period_us;
    elapsed = now - cycle_start_us;
    
    // Check if done
    if (config.cycles > 0 && cycle_count >= config.cycles) {
      stopGenerator();
      return;
    }
  }
  
  // Now elapsed is guaranteed to be < period_us
  
  // Read current port state once
  uint8_t port_state = PORTF.OUT;
  
  // Handle spec pin
  if (spec_fall_us > spec_rise_us) {
    if (elapsed >= spec_rise_us && elapsed < spec_fall_us) {
      port_state |= SPEC_MASK;
    } else {
      port_state &= ~SPEC_MASK;
    }
  } else {
    if (elapsed >= spec_rise_us || elapsed < spec_fall_us) {
      port_state |= SPEC_MASK;
    } else {
      port_state &= ~SPEC_MASK;
    }
  }
  
  // Handle laser pin
  if (laser_fall_us > laser_rise_us) {
    if (elapsed >= laser_rise_us && elapsed < laser_fall_us) {
      port_state |= LASER_MASK;
    } else {
      port_state &= ~LASER_MASK;
    }
  } else {
    if (elapsed >= laser_rise_us || elapsed < laser_fall_us) {
      port_state |= LASER_MASK;
    } else {
      port_state &= ~LASER_MASK;
    }
  }
  
  // Write port state once
  PORTF.OUT = port_state;
}

// ============================================================================
// STATUS
// ============================================================================

void printStatus() {
  Serial.print(F("STATUS,"));
  Serial.print(running ? 1 : 0); Serial.print(',');
  Serial.print(cycle_count); Serial.print(',');
  Serial.print(config.frequency); Serial.print(',');
  Serial.print(config.duty_spec); Serial.print(',');
  Serial.print(config.duty_laser); Serial.print(',');
  Serial.println(config.offset_us);
}

// ============================================================================
// MAIN
// ============================================================================

void setup() {
  Serial.begin(115200);
  
  // Ensure pins are LOW at startup
  // ATmega4809 uses PORTF.DIR for direction and PORTF.OUT for output
  PORTF.DIR |= SPEC_MASK | LASER_MASK;
  PORTF.OUT &= ~(SPEC_MASK | LASER_MASK);
}

void loop() {
  // Run generator first (highest priority)
  runGenerator();
  
  // Handle serial commands (lower priority, non-blocking)
  if (Serial.available()) {
    char cmd = Serial.read();
    
    switch (cmd) {
      case 'S':
        startGenerator();
        Serial.println(F("OK:STARTED"));
        break;
        
      case 'X':
        stopGenerator();
        Serial.print(F("OK:STOPPED:"));
        Serial.println(cycle_count);
        break;
        
      case 'Q':
        printStatus();
        break;
        
      case 'P':
        stopGenerator();
        if (readParameters()) {
          Serial.println(F("OK:PARAMS_SET"));
        }
        break;
    }
    
    // Minimal delay
    delay(1);
    while (Serial.available()) Serial.read();
  }
}