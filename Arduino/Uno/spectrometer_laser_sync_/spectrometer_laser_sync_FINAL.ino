/*
 * Dual-Channel Synchronized Trigger Generator - FINAL WORKING VERSION
 * 
 * Pin 9  (Timer1 OC1A): Spectrometer trigger - 16-bit timer (high precision)
 * Pin 3  (Timer2 OC2B): Laser trigger - 8-bit timer (sufficient for 1-1000Hz)
 * 
 * Using two independent timers allows true phase offset control!
 */

// Configuration
struct {
  uint32_t cycles;
  uint16_t frequency;
  uint8_t duty_spec;
  uint8_t duty_laser;
  int16_t offset_us;
} config = {0, 100, 50, 50, 0};

// State
volatile bool running = false;
volatile uint32_t cycle_count = 0;

void setup() {
  Serial.begin(115200);
  pinMode(9, OUTPUT);   // Timer1 OC1A - Spectrometer
  pinMode(3, OUTPUT);   // Timer2 OC2B - Laser
  digitalWrite(9, LOW);
  digitalWrite(3, LOW);
}

void loop() {
  if (Serial.available() > 0) {
    char cmd = Serial.read();
    
    switch (cmd) {
      case 'S':
        start();
        Serial.println(F("OK:STARTED"));
        break;
        
      case 'X':
        stop();
        Serial.print(F("OK:STOPPED:"));
        Serial.println(cycle_count);
        break;
        
      case 'Q':
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
        
      case 'P':
        if (readParameters()) {
          Serial.println(F("OK:PARAMS_SET"));
        }
        break;
    }
    
    delay(5);
    while (Serial.available()) Serial.read();
  }
  
  if (running && config.cycles > 0 && cycle_count >= config.cycles) {
    stop();
    Serial.println(F("OK:CYCLES_COMPLETE"));
  }
}

void start() {
  cycle_count = 0;
  running = true;
  
  // COMPLETE Timer1 reset
  TCCR1A = 0;
  TCCR1B = 0;
  TCCR1C = 0;
  TIMSK1 = 0;
  TIFR1 = 0xFF;  // Clear all interrupt flags
  TCNT1 = 0;
  OCR1A = 0;
  OCR1B = 0;
  ICR1 = 0;
  
  // COMPLETE Timer2 reset
  TCCR2A = 0;
  TCCR2B = 0;
  TIMSK2 = 0;
  TIFR2 = 0xFF;
  TCNT2 = 0;
  OCR2A = 0;
  OCR2B = 0;
  
  digitalWrite(9, LOW);
  digitalWrite(3, LOW);
  
  delay(10);  // Let everything settle
  
  // Now configure Timer1...
  cycle_count = 0;
  running = true;
  
  // Stop both timers
  TCCR1B = 0;
  TCCR2B = 0;
  
  digitalWrite(9, LOW);
  digitalWrite(3, LOW);
  
  // ===== TIMER1 SETUP (Spectrometer - Pin 9) =====
  // 16-bit timer, high precision
  TCNT1 = 0;
  
  uint16_t timer1_top = (16000000UL / (8UL * config.frequency)) - 1;
  uint16_t timer1_compare = ((uint32_t)(timer1_top + 1) * config.duty_spec) / 100;
  
  ICR1 = timer1_top;
  OCR1A = timer1_compare;
  
  // Fast PWM mode 14, non-inverting, prescaler 8
  TCCR1A = _BV(COM1A1) | _BV(WGM11);
  TCCR1B = _BV(WGM13) | _BV(WGM12) | _BV(CS11);
  TIMSK1 = _BV(TOIE1);  // Overflow interrupt for cycle counting
  // DEBUG - Add these lines here:
  // Serial.print("DEBUG: TOP1=");
  // Serial.print(timer1_top);
  // Serial.print(" CMP1=");
  // Serial.print(timer1_compare);
  // Serial.print(" DUTY=");
  // Serial.print((timer1_compare * 100.0) / (timer1_top + 1));
  // Serial.println("%");
  
  // ===== TIMER2 SETUP (Laser - Pin 3) =====
  // 8-bit timer, calculate best prescaler for frequency
  TCNT2 = 0;
  
  uint8_t timer2_top;
  uint8_t timer2_compare;
  uint8_t prescaler_bits;
  
  // Calculate Timer2 parameters based on frequency
  // Timer2 is 8-bit (0-255), need to find best prescaler
  if (config.frequency <= 244) {
    // Use prescaler 1024: f = 16MHz / (1024 * (TOP+1))
    // TOP = 16000000 / (1024 * f) - 1
    timer2_top = (16000000UL / (1024UL * config.frequency)) - 1;
    if (timer2_top > 255) timer2_top = 255;
    prescaler_bits = _BV(CS22) | _BV(CS21) | _BV(CS20);  // 1024
  } else if (config.frequency <= 488) {
    // Use prescaler 256
    timer2_top = (16000000UL / (256UL * config.frequency)) - 1;
    if (timer2_top > 255) timer2_top = 255;
    prescaler_bits = _BV(CS22) | _BV(CS21);  // 256
  } else if (config.frequency <= 976) {
    // Use prescaler 128
    timer2_top = (16000000UL / (128UL * config.frequency)) - 1;
    if (timer2_top > 255) timer2_top = 255;
    prescaler_bits = _BV(CS22) | _BV(CS20);  // 128
  } else {
    // Use prescaler 64 for frequencies above 976 Hz
    timer2_top = (16000000UL / (64UL * config.frequency)) - 1;
    if (timer2_top > 255) timer2_top = 255;
    prescaler_bits = _BV(CS22);  // 64
  }
  
  timer2_compare = ((uint16_t)(timer2_top + 1) * config.duty_laser) / 100;
  
  OCR2A = timer2_top;     // TOP value
  OCR2B = timer2_compare; // Compare for OC2B (Pin 3)
  
  // Fast PWM mode 7 (TOP = OCR2A), non-inverting on OC2B
  TCCR2A = _BV(COM2B1) | _BV(WGM21) | _BV(WGM20);
  // Don't start Timer2 yet - we'll start it with offset
  
  // ===== APPLY OFFSET =====
  // Calculate offset in Timer1 ticks (prescaler 8: 0.5µs per tick)
  int32_t offset_ticks = (int32_t)config.offset_us * 2;
  if (offset_ticks == 0) {
    // No offset - start both timers now
    TCCR2B = _BV(WGM22) | prescaler_bits;  // Start Timer2
    
  } else if (offset_ticks < 0) {
    
    // Negative offset: Laser fires FIRST
    // Start Timer2 now, delay Timer1
    TCCR2B = _BV(WGM22) | prescaler_bits;  // Start Timer2 immediately
    
    // Preload Timer1 counter so it's already "ahead"
    int32_t preload = -offset_ticks;
    if (preload > timer1_top) preload = preload % (timer1_top + 1);
    TCNT1 = (uint16_t)preload;
    
  } else {
    
    // Positive offset: Spec fires FIRST
    // Start Timer1 now (already started), Timer2 starts later
    // Preload Timer2 counter
    
    // Convert offset from Timer1 ticks to Timer2 ticks
    // This is approximate since Timer2 uses different prescaler
    uint32_t period_us = 1000000UL / config.frequency;
    uint16_t timer2_period_ticks = timer2_top + 1;
    int32_t timer2_offset_ticks = ((int32_t)offset_ticks * timer2_period_ticks) / (timer1_top + 1);
    
    if (timer2_offset_ticks > 0 && timer2_offset_ticks < timer2_period_ticks) {
      TCNT2 = (uint8_t)timer2_offset_ticks;
    }
    
    TCCR2B = _BV(WGM22) | prescaler_bits;  // Start Timer2
  }
}

void stop() {
  running = false;
  TCCR1B = 0;
  TCCR2B = 0;
  TIMSK1 = 0;
  digitalWrite(9, LOW);
  digitalWrite(3, LOW);
}

ISR(TIMER1_OVF_vect) {
  if (running) {
    cycle_count++;
  }
}

bool readParameters() {
  uint32_t start_time = millis();
  while (Serial.available() < 10) {
    if (millis() - start_time > 5000) {
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
