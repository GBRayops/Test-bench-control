/*
 * Dual Channel Trigger Generator
 * Hardware-timed, deterministic start
 *
 * Board: Arduino Nano Every (ATmega4809)
 *
 * Pins:
 *   SPEC  -> D14 (A0) -> PD3
 *   LASER -> D15 (A1) -> PD2
 *
 * Timing:
 *   Base tick = 1 µs (TCB0)
 *   Target frequency = 100 Hz
 */

#include <Arduino.h>
#include <avr/io.h>
#include <avr/interrupt.h>

// ============================================================================
// CONFIGURATION
// ============================================================================

struct {
  uint32_t cycles;        // 0 = infinite
  uint16_t frequency;     // Hz
  uint8_t  duty_spec;     // %
  uint8_t  duty_laser;    // %
  int16_t  offset_us;     // µs (can be negative)
} config = {0, 100, 50, 50, 0};

// ============================================================================
// PIN DEFINITIONS (PORTD)
// ============================================================================

#define SPEC_BIT   3   // D14 -> PD3
#define LASER_BIT  2   // D15 -> PD2

#define SPEC_MASK  (1 << SPEC_BIT)
#define LASER_MASK (1 << LASER_BIT)

// ============================================================================
// STATE
// ============================================================================

volatile bool running = false;
volatile uint32_t cycle_count = 0;

// Timing (µs)
volatile uint16_t period_us;
volatile uint16_t spec_fall_us;
volatile uint16_t laser_rise_us;
volatile uint16_t laser_fall_us;

// Internal time counter
volatile uint16_t t_us = 0;

// ============================================================================
// TIMER ISR — 1 µs tick
// ============================================================================

ISR(TCB0_INT_vect)
{
  // Clear interrupt flag
  TCB0.INTFLAGS = TCB_CAPT_bm;

  if (!running)
    return;

  // ===== SPEC =====
  if (t_us == 0)
    VPORTD.OUT |= SPEC_MASK;

  if (t_us == spec_fall_us)
    VPORTD.OUT &= ~SPEC_MASK;

  // ===== LASER =====
  if (t_us == laser_rise_us)
    VPORTD.OUT |= LASER_MASK;

  if (t_us == laser_fall_us)
    VPORTD.OUT &= ~LASER_MASK;

  // Advance time
  t_us++;

  // End of period
  if (t_us >= period_us) {
    t_us = 0;
    cycle_count++;

    if (config.cycles > 0 && cycle_count >= config.cycles) {
      running = false;
      VPORTD.OUT &= ~(SPEC_MASK | LASER_MASK);
      TCB0.CTRLA &= ~TCB_ENABLE_bm;
    }
  }
}

// ============================================================================
// TIMER SETUP
// ============================================================================

void setupTimer()
{
  // Clock = 20 MHz / 2 = 10 MHz
  TCB0.CTRLA = TCB_CLKSEL_CLKDIV2_gc;

  // Periodic interrupt mode
  TCB0.CTRLB = TCB_CNTMODE_INT_gc;

  // 10 ticks @ 10 MHz = 1 µs
  TCB0.CCMP = 10;

  // Enable interrupt
  TCB0.INTCTRL = TCB_CAPT_bm;
}

// ============================================================================
// GENERATOR CONTROL
// ============================================================================

void startGenerator()
{
  cli();

  // Force outputs LOW
  VPORTD.OUT &= ~(SPEC_MASK | LASER_MASK);

  // --- Compute timing ---
  period_us = 1000000UL / config.frequency;

  spec_fall_us = (period_us * config.duty_spec) / 100;

  // Signed math for offset
  int32_t lr = config.offset_us;
  while (lr < 0)
    lr += period_us;

  laser_rise_us = lr % period_us;

  laser_fall_us = (laser_rise_us +
                   (period_us * config.duty_laser) / 100) % period_us;

  // Reset state
  t_us = 0;
  cycle_count = 0;
  running = true;

  // Reset and start timer
  TCB0.CNT = 0;
  TCB0.CTRLA |= TCB_ENABLE_bm;

  sei();
}

void stopGenerator()
{
  cli();
  running = false;
  TCB0.CTRLA &= ~TCB_ENABLE_bm;
  VPORTD.OUT &= ~(SPEC_MASK | LASER_MASK);
  sei();
}

// ============================================================================
// SETUP / LOOP
// ============================================================================

void setup()
{
  Serial.begin(115200);

  // Configure pins as outputs
  VPORTD.DIR |= SPEC_MASK | LASER_MASK;
  VPORTD.OUT &= ~(SPEC_MASK | LASER_MASK);

  setupTimer();
}

void loop()
{
  if (Serial.available()) {
    char cmd = Serial.read();

    if (cmd == 'S') {
      startGenerator();
      Serial.println(F("OK:STARTED"));
    }
    else if (cmd == 'X') {
      stopGenerator();
      Serial.println(F("OK:STOPPED"));
    }
  }
}
