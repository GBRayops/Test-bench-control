/*
 * Dual Channel Trigger Generator - SIMPLE VERSION
 * Clean output for Python integration
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

#define SPEC_PIN   9   // Pin 9
#define LASER_PIN  10  // Pin 10

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
  digitalWrite(SPEC_PIN, LOW);
  digitalWrite(LASER_PIN, LOW);
}

void startGenerator() {
  stopGenerator();
  
  computeTiming();
  
  pinMode(SPEC_PIN, OUTPUT);
  pinMode(LASER_PIN, OUTPUT);
  digitalWrite(SPEC_PIN, LOW);
  digitalWrite(LASER_PIN, LOW);
  
  cycle_count = 0;
  running = true;
  cycle_start_us = micros();
}

void runGenerator() {
  if (!running) return;
  
  uint32_t elapsed = micros() - cycle_start_us;
  
  // Handle spec pin
  if (spec_fall_us > spec_rise_us) {
    if (elapsed >= spec_rise_us && elapsed < spec_fall_us) {
      digitalWrite(SPEC_PIN, HIGH);
    } else {
      digitalWrite(SPEC_PIN, LOW);
    }
  } else {
    if (elapsed >= spec_rise_us || elapsed < spec_fall_us) {
      digitalWrite(SPEC_PIN, HIGH);
    } else {
      digitalWrite(SPEC_PIN, LOW);
    }
  }
  
  // Handle laser pin
  if (laser_fall_us > laser_rise_us) {
    if (elapsed >= laser_rise_us && elapsed < laser_fall_us) {
      digitalWrite(LASER_PIN, HIGH);
    } else {
      digitalWrite(LASER_PIN, LOW);
    }
  } else {
    if (elapsed >= laser_rise_us || elapsed < laser_fall_us) {
      digitalWrite(LASER_PIN, HIGH);
    } else {
      digitalWrite(LASER_PIN, LOW);
    }
  }
  
  // Check if period complete
  if (elapsed >= period_us) {
    cycle_count++;
    cycle_start_us += period_us;
    
    if (config.cycles > 0 && cycle_count >= config.cycles) {
      stopGenerator();
    }
  }
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
}

void loop() {
  runGenerator();
  
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
    
    delay(5);
    while (Serial.available()) Serial.read();
  }
}