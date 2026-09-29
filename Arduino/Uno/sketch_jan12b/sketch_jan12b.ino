/*
 * Dual Channel Hardware-Timed Trigger Generator
 * ---------------------------------------------
 * Arduino Uno (ATmega328P)
 *
 * D9  (PB1) : Spectrometer trigger
 * D10 (PB2) : Laser trigger
 *
 * Features:
 * - True rising-edge offset (signed)
 * - Independent duty cycles
 * - Stable period
 * - Hardware-timed edges
 * - Serial control preserved
 */

#include <avr/io.h>
#include <avr/interrupt.h>

// ============================================================================
// CONFIGURATION STRUCT (SERIAL PROTOCOL UNCHANGED)
// ============================================================================

struct {
  uint32_t cycles;        // 0 = continuous
  uint16_t frequency;     // Hz
  uint8_t duty_spec;      // %
  uint8_t duty_laser;     // %
  int16_t offset_us;      // signed (laser - spec)
} config = {0, 100, 50, 50, 0};

// ============================================================================
// INTERNAL STATE
// ============================================================================

volatile uint32_t cycle_count = 0;
volatile bool running = false;

uint16_t period_ticks;

// Spectrometer timing
uint16_t spec_on;
uint16_t spec_off;

// Laser timing
uint16_t laser_on;
uint16_t laser_off;

// Pin masks
#define SPEC_MASK  _BV(PB1)
#define LASER_MASK _BV(PB2)

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
  while (Serial.available() < 10) {
    if (millis() - start > 5000) {
      Serial.println(F("ERR:TIMEOUT"));
      return false;
    }
  }

  config.cycles     = readUint32();
  config.frequency  = readUint16();
  config.duty_spec  = Serial.read();
  config.duty_laser = Serial.read();
  config.offset_us  = (int16_t)readUint16();

  if (config.frequency < 1 || config.frequency > 1000 ||
      config.duty_spec > 100 || config.duty_laser > 100) {
    Serial.println(F("ERR:PARAM_RANGE"));
    return false;
  }

  return true;
}

// ============================================================================
// TIMING COMPUTATION (HARDWARE-CORRECT)
// ============================================================================
bool validateTiming() {

  if (spec_off > period_ticks) return false;
  if (laser_off > period_ticks) return false;

  // Prevent zero-width pulses
  if (spec_on == spec_off) return false;
  if (laser_on == laser_off) return false;

  return true;
}

void computeTiming() {

  period_ticks = (16000000UL / 8) / config.frequency;

  uint16_t spec_width  = (uint32_t)period_ticks * config.duty_spec  / 100;
  uint16_t laser_width = (uint32_t)period_ticks * config.duty_laser / 100;

  int32_t offset_ticks = (int32_t)config.offset_us * 2; // 0.5 µs per tick

  // Reference: spectrometer rising edge at t = 0
  int32_t s_on = 0;
  int32_t s_off = spec_width;

  int32_t l_on = offset_ticks;
  int32_t l_off = offset_ticks + laser_width;

  // Normalize negative times
  int32_t min_t = min(s_on, min(s_off, min(l_on, l_off)));
  if (min_t < 0) {
    s_on  -= min_t;
    s_off -= min_t;
    l_on  -= min_t;
    l_off -= min_t;
  }

  spec_on   = s_on;
  spec_off  = s_off;
  laser_on  = l_on;
  laser_off = l_off;
}

// ============================================================================
// START / STOP
// ============================================================================

void startGenerator() {

  stopGenerator();
  computeTiming();
  if (!validateTiming()) {
    Serial.println(F("ERR:INVALID_TIMING"));
    return;
  }

  DDRB  |= SPEC_MASK | LASER_MASK;
  PORTB &= ~(SPEC_MASK | LASER_MASK);

  cycle_count = 0;
  running = true;

  // Timer1 CTC
  TCCR1A = 0;
  TCCR1B = _BV(WGM12) | _BV(CS11); // prescaler 8
  TCNT1  = 0;

  OCR1A = spec_on;
  OCR1B = laser_on;

  TIMSK1 = _BV(OCIE1A) | _BV(OCIE1B);
}

void stopGenerator() {
  TIMSK1 = 0;
  TCCR1B = 0;
  PORTB &= ~(SPEC_MASK | LASER_MASK);
  running = false;
}

// ============================================================================
// COMPARE INTERRUPTS (HARDWARE TIMED EDGES)
// ============================================================================

ISR(TIMER1_COMPA_vect) {
  static bool spec_high = false;

  if (!spec_high) {
    PORTB |= SPEC_MASK;
    OCR1A = spec_off;
    spec_high = true;
  } else {
    PORTB &= ~SPEC_MASK;
    OCR1A = spec_on + period_ticks; // next cycle
    spec_high = false;
  }
}


ISR(TIMER1_COMPB_vect) {
  static bool laser_high = false;

  if (!laser_high) {
    // Rising edge
    PORTB |= LASER_MASK;
    OCR1B = laser_off;          // schedule falling edge
    laser_high = true;
  } else {
    // Falling edge
    PORTB &= ~LASER_MASK;
    OCR1B = laser_on + period_ticks;  // re-arm next rising edge
    laser_high = false;
  }
}


// Period end (overflow not used; manual reset)
ISR(TIMER1_OVF_vect) {
  TCNT1 = 0;
  cycle_count++;

  if (config.cycles && cycle_count >= config.cycles) {
    stopGenerator();
  }
}

// ============================================================================
// STATUS
// ============================================================================

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

// ============================================================================
// MAIN
// ============================================================================

void setup() {
  Serial.begin(115200);
  sei();
}

void loop() {

  if (!Serial.available()) return;

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
        computeTiming();
        Serial.println(F("OK:PARAMS_SET"));
      }
      break;
  }

  delay(5);
  while (Serial.available()) Serial.read();
}

