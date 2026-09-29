/*
 * Dual Channel Trigger Generator
 * Board: Arduino Nano Every (ATmega4809)
 *
 * Pin mapping (verified):
 *   SPEC  -> D14 (A0) -> PD3
 *   LASER -> D15 (A1) -> PD2
 *
 * Channel counts are independent:
 *   spec_cycles  == 0  -> SPEC  fires for the full sequence (continuous)
 *   laser_cycles == 0  -> LASER fires for the full sequence (continuous)
 *   Both > 0           -> sequence runs for max(spec_cycles, laser_cycles)
 *   Both == 0          -> run forever until 'X' command
 *
 * Per-channel soft-stop commands (pin goes LOW, other channel unaffected):
 *   'E' -> silence SPEC  pin for remainder of sequence
 *   'L' -> silence LASER pin for remainder of sequence
 */

// ============================================================================
// CONFIGURATION
// ============================================================================

struct {
  uint32_t spec_cycles;  // SPEC  triggers (0 = continuous)
  uint32_t laser_cycles; // LASER triggers (0 = continuous)
  uint16_t frequency;
  uint8_t duty_spec;
  uint8_t duty_laser;
  int16_t offset_us;
} config = {0, 0, 100, 50, 50, 0};

// ============================================================================
// STATE
// ============================================================================

uint32_t cycle_count = 0;
bool running = false;

bool spec_force_stop  = false; // set by 'E' command
bool laser_force_stop = false; // set by 'L' command

// --- Nano Every pin mapping (PORTD) ---
#define SPEC_BIT   3   // D14 (PD3)
#define LASER_BIT  2   // D15 (PD2)

#define SPEC_MASK  (1 << SPEC_BIT)
#define LASER_MASK (1 << LASER_BIT)

// Timing (µs)
uint32_t period_us;
uint32_t spec_high_us;
uint32_t laser_high_us;

// Edge times inside period
uint32_t spec_rise_us;
uint32_t spec_fall_us;
uint32_t laser_rise_us;
uint32_t laser_fall_us;

// Precomputed sequence stop point (0 = run forever)
uint32_t stop_at;

// Cycle start timestamp
uint32_t cycle_start_us;

// ============================================================================
// SERIAL HELPERS
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
  while (Serial.available() < 14) {
    if (millis() - start > 5000) {
      Serial.println(F("ERR:TIMEOUT"));
      return false;
    }
  }

  config.spec_cycles  = readUint32();
  config.laser_cycles = readUint32();
  config.frequency    = readUint16();
  config.duty_spec    = Serial.read();
  config.duty_laser   = Serial.read();
  config.offset_us    = (int16_t)readUint16();

  if (config.frequency < 1 || config.frequency > 1000 ||
      config.duty_spec > 100 || config.duty_laser > 100) {
    Serial.println(F("ERR:PARAM_RANGE"));
    return false;
  }

  return true;
}

// ============================================================================
// TIMING COMPUTATION
// ============================================================================

void computeTiming() {
  period_us = 1000000UL / config.frequency;

  spec_high_us  = (period_us * config.duty_spec)  / 100;
  laser_high_us = (period_us * config.duty_laser) / 100;

  // SPEC reference at t = 0
  spec_rise_us = 0;
  spec_fall_us = spec_high_us;

  // LASER with offset
  int32_t lr = config.offset_us;
  int32_t lf = config.offset_us + laser_high_us;

  while (lr < 0) lr += period_us;
  while (lf < 0) lf += period_us;

  laser_rise_us = lr % period_us;
  laser_fall_us = lf % period_us;

  // Auto-stop only when both channels have a finite limit.
  // If either channel is 0 (continuous), the generator runs indefinitely;
  // the finite channel goes silent naturally via its active-window check.
  if (config.spec_cycles > 0 && config.laser_cycles > 0)
    stop_at = max(config.spec_cycles, config.laser_cycles);
  else
    stop_at = 0;
}

// ============================================================================
// GENERATOR CONTROL
// ============================================================================

void stopGenerator() {
  running = false;
  VPORTD.OUT &= ~(SPEC_MASK | LASER_MASK);
}

void startGenerator() {
  stopGenerator();
  computeTiming();

  // Configure outputs
  VPORTD.DIR |= SPEC_MASK | LASER_MASK;
  VPORTD.OUT &= ~(SPEC_MASK | LASER_MASK);

  cycle_count = 0;
  spec_force_stop  = false;
  laser_force_stop = false;

  uint32_t now = micros();

  // Start from a guaranteed LOW region so cycle 1 begins with a clean
  // rising edge on both pins (no mid-pulse start).
  uint32_t safe_time = max(spec_fall_us, laser_fall_us);
  if (safe_time < period_us) safe_time += 100;
  if (safe_time >= period_us) safe_time = period_us - 100;

  cycle_start_us = now - safe_time;
  running = true;
}

void runGenerator() {
  if (!running) {
    VPORTD.OUT &= ~(SPEC_MASK | LASER_MASK);
    return;
  }

  uint32_t now = micros();
  uint32_t elapsed = now - cycle_start_us;

  // Catch up missed cycles safely
  while (elapsed >= period_us) {
    cycle_count++;
    cycle_start_us += period_us;
    elapsed = now - cycle_start_us;

    if (stop_at > 0 && cycle_count > stop_at) {
      stopGenerator();
      return;
    }
  }

  // ===== SPEC =====
  // Active when not force-stopped AND within its cycle window.
  // Uses <= so cycle_count==spec_cycles still fires; the stop_at check above
  // halts the generator one increment later (cycle_count > stop_at).
  bool spec_active = !spec_force_stop &&
                     (config.spec_cycles == 0 || cycle_count <= config.spec_cycles);

  if (spec_active) {
    if (spec_fall_us > spec_rise_us) {
      if (elapsed >= spec_rise_us && elapsed < spec_fall_us)
        VPORTD.OUT |= SPEC_MASK;
      else
        VPORTD.OUT &= ~SPEC_MASK;
    } else {
      if (elapsed >= spec_rise_us || elapsed < spec_fall_us)
        VPORTD.OUT |= SPEC_MASK;
      else
        VPORTD.OUT &= ~SPEC_MASK;
    }
  } else {
    VPORTD.OUT &= ~SPEC_MASK;
  }

  // ===== LASER =====
  // Active when not force-stopped AND within its cycle window
  bool laser_active = !laser_force_stop &&
                      (config.laser_cycles == 0 || cycle_count <= config.laser_cycles);

  if (laser_active) {
    if (laser_fall_us > laser_rise_us) {
      if (elapsed >= laser_rise_us && elapsed < laser_fall_us)
        VPORTD.OUT |= LASER_MASK;
      else
        VPORTD.OUT &= ~LASER_MASK;
    } else {
      if (elapsed >= laser_rise_us || elapsed < laser_fall_us)
        VPORTD.OUT |= LASER_MASK;
      else
        VPORTD.OUT &= ~LASER_MASK;
    }
  } else {
    VPORTD.OUT &= ~LASER_MASK;
  }
}

// ============================================================================
// STATUS
// ============================================================================

void printStatus() {
  Serial.print(F("STATUS,"));
  Serial.print(running ? 1 : 0);        Serial.print(',');
  Serial.print(cycle_count);            Serial.print(',');
  Serial.print(config.frequency);       Serial.print(',');
  Serial.print(config.duty_spec);       Serial.print(',');
  Serial.print(config.duty_laser);      Serial.print(',');
  Serial.print(config.offset_us);       Serial.print(',');
  Serial.print(config.laser_cycles);    Serial.print(',');
  Serial.println(config.spec_cycles);
}

// ============================================================================
// MAIN
// ============================================================================

void setup() {
  Serial.begin(115200);

  // Ensure pins start LOW
  VPORTD.DIR |= SPEC_MASK | LASER_MASK;
  VPORTD.OUT &= ~(SPEC_MASK | LASER_MASK);
}

void loop() {
  // Highest priority: waveform generation
  runGenerator();

  // Serial command handling
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

      case 'E':  // silence SPEC pin only
        spec_force_stop = true;
        VPORTD.OUT &= ~SPEC_MASK;
        {
          // If laser is also done/silent, stop the whole generator
          bool laser_still_active = !laser_force_stop &&
                                    (config.laser_cycles == 0 || cycle_count <= config.laser_cycles);
          if (!laser_still_active) {
            stopGenerator();
            Serial.println(F("OK:ALL_STOPPED"));
          } else {
            Serial.println(F("OK:SPEC_STOPPED"));
          }
        }
        break;

      case 'L':  // silence LASER pin only
        laser_force_stop = true;
        VPORTD.OUT &= ~LASER_MASK;
        {
          // If spec is also done/silent, stop the whole generator
          bool spec_still_active = !spec_force_stop &&
                                   (config.spec_cycles == 0 || cycle_count <= config.spec_cycles);
          if (!spec_still_active) {
            stopGenerator();
            Serial.println(F("OK:ALL_STOPPED"));
          } else {
            Serial.println(F("OK:LASER_STOPPED"));
          }
        }
        break;

      case 'Q':
        printStatus();
        break;

      case 'P':
        stopGenerator();
        if (readParameters())
          Serial.println(F("OK:PARAMS_SET"));
        break;
    }

    delay(1);
    while (Serial.available()) Serial.read();
  }
}
