/*
 * Dual-Channel Precise Trigger Generator (WITH SERIAL CONTROL)
 * -----------------------------------------------------------
 * Hardware : Arduino Uno (ATmega328P)
 * Timer    : Timer1 (16-bit, CTC mode)
 *
 * Pin 9  (PB1) : Spectrometer trigger
 * Pin 10 (PB2) : Laser trigger
 *
 * Timing resolution : 0.5 µs (prescaler = 8)
 */

#include <avr/io.h>
#include <avr/interrupt.h>

// ============================================================================
// CONFIGURATION (unchanged protocol)
// ============================================================================

struct {
  uint32_t cycles;       // 0 = continuous
  uint16_t frequency;    // Hz
  uint8_t duty_spec;     // %
  uint8_t duty_laser;    // %
  int16_t offset_us;     // signed (laser - spec)
} config = {0, 100, 50, 50, 0};

// ============================================================================
// INTERNAL STATE
// ============================================================================

volatile bool running = false;
volatile uint32_t cycle_count = 0;

// Timer ticks (0.5 µs per tick)
uint16_t period_ticks;

uint16_t dt0, dt1, dt2, dt3;

// uint16_t t_spec_on;
// uint16_t t_spec_off;
// uint16_t t_laser_on;
// uint16_t t_laser_off;

struct Event {
  uint16_t time;
  uint8_t  set_mask;
  uint8_t  clr_mask;
};

volatile uint8_t event_index = 0;

// Pin masks
#define SPEC_MASK  _BV(PB1)   // D9
#define LASER_MASK _BV(PB2)   // D10

// ============================================================================
// SERIAL HELPERS (IDENTICAL BEHAVIOR)
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

  config.cycles      = readUint32();
  config.frequency   = readUint16();
  config.duty_spec   = Serial.read();
  config.duty_laser  = Serial.read();
  config.offset_us   = (int16_t)readUint16();

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

#define MAX_EVENTS 5

Event events[MAX_EVENTS];
uint16_t event_delta[MAX_EVENTS];
uint8_t event_count = 0;

void computeTiming() {

  period_ticks = (16000000UL / 8) / config.frequency;

  uint16_t spec_width  = (uint32_t)period_ticks * config.duty_spec  / 100;
  uint16_t laser_width = (uint32_t)period_ticks * config.duty_laser / 100;

  int32_t offset_ticks = (int32_t)config.offset_us * 2;

  int32_t spec_on   = 0;
  int32_t spec_off  = spec_width;
  int32_t laser_on  = offset_ticks;
  int32_t laser_off = offset_ticks + laser_width;

  // Normalize negative times
  int32_t min_t = min(spec_on, min(spec_off, min(laser_on, laser_off)));
  if (min_t < 0) {
    spec_on   -= min_t;
    spec_off  -= min_t;
    laser_on  -= min_t;
    laser_off -= min_t;
  }

  // Build events
  event_count = 0;
  events[event_count++] = { (uint16_t)spec_on,   SPEC_MASK, 0 };
  events[event_count++] = { (uint16_t)spec_off,  0, SPEC_MASK };
  events[event_count++] = { (uint16_t)laser_on,  LASER_MASK, 0 };
  events[event_count++] = { (uint16_t)laser_off, 0, LASER_MASK };
  events[event_count++] = { (uint16_t)period_ticks, 0, 0 };

  // Sort by time
  for (uint8_t i = 0; i < event_count - 1; i++) {
    for (uint8_t j = i + 1; j < event_count; j++) {
      if (events[j].time < events[i].time) {
        Event tmp = events[i];
        events[i] = events[j];
        events[j] = tmp;
      }
    }
  }

  // Convert to deltas
  event_delta[0] = events[0].time;
  for (uint8_t i = 1; i < event_count; i++) {
    event_delta[i] = events[i].time - events[i - 1].time;
  }
}


// ============================================================================
// START / STOP
// ============================================================================

void startGenerator() {

  stopGenerator();
  computeTiming();

  DDRB  |= SPEC_MASK | LASER_MASK;
  PORTB &= ~(SPEC_MASK | LASER_MASK);

  event_index = 0;
  cycle_count = 0;
  running = true;

  TCCR1A = 0;
  TCCR1B = _BV(WGM12) | _BV(CS11); // CTC, prescaler 8
  TCNT1  = 0;

  OCR1A  = 1;
  TIMSK1 = _BV(OCIE1A);
}

void stopGenerator() {
  TIMSK1 = 0;
  TCCR1B = 0;
  PORTB &= ~(SPEC_MASK | LASER_MASK);
  running = false;
}

// ============================================================================
// TIMER1 ISR (EVENT SEQUENCER)
// ============================================================================



ISR(TIMER1_COMPA_vect) {

  // Apply event
  PORTB |=  events[event_index].set_mask;
  PORTB &= ~events[event_index].clr_mask;

  OCR1A += event_delta[event_index];

  event_index++;
  if (event_index >= event_count) {
    event_index = 0;
    cycle_count++;

    if (config.cycles && cycle_count >= config.cycles) {
      stopGenerator();
    }
  }
}

// ============================================================================
// STATUS QUERY
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
// MAIN LOOP
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
      if (readParameters()) {
        computeTiming();
        Serial.println(F("OK:PARAMS_SET"));
      }
      break;
  }

  delay(5);
  while (Serial.available()) Serial.read();
}
