/*
 * kfs_fan_4pin — direct 4-pin PWM fan control + DS18B20 ambient temperature
 *
 * Variant B: 25 kHz PWM signal via Timer1 for 4-pin fans (Intel spec).
 * No MOSFET needed — fan gets 12V directly, PWM controls speed internally.
 * Recommended fan: Arctic P12 Pro PST (0 RPM passive mode below 5% PWM).
 *
 * Serial protocol (9600 baud):
 *   Server -> Arduino: "PWM:xxx\n"      where xxx = 0-320 (0=stop, 320=max)
 *   Arduino -> Server: "TEMP:xx.xx\n"   every 5 seconds
 *   Arduino -> Server: "RPM:xxxx\n"     every 5 seconds (if tacho connected)
 *   Arduino -> Server: "FAN:READY:4PIN\n" on startup
 *   Arduino -> Server: "FAN:PWM=xxx\n"  on PWM change acknowledgment
 *
 * Wiring:
 *   Fan +12V (yellow)  -> 12V supply (+)
 *   Fan GND (black)    -> 12V supply (-) + Arduino GND (common ground!)
 *   Fan PWM (blue)     -> D9 (Timer1 OC1A, 25 kHz)
 *   Fan Tacho (green)  -> D2 (optional, interrupt pin for RPM reading)
 *   DS18B20 DQ         -> D3 (4.7k pull-up to 5V)
 *   DS18B20 VCC        -> 5V, GND -> GND
 */

#include <OneWire.h>
#include <DallasTemperature.h>

static const int FAN_PWM_PIN = 9;     // Timer1 OC1A — 25 kHz PWM output
static const int FAN_TACHO_PIN = 2;   // Interrupt pin for RPM (optional)
static const int TEMP_PIN = 3;        // DS18B20 data pin
static const unsigned long TEMP_INTERVAL = 5000;  // ms

static const int PWM_MAX = 320;       // Timer1 TOP = 100% duty cycle

OneWire oneWire(TEMP_PIN);
DallasTemperature sensors(&oneWire);

static unsigned long lastTempRead = 0;
static int currentPWM = 0;

// Tachometer — pulse counting
static volatile unsigned long tachoCount = 0;
static unsigned long lastTachoRead = 0;

static void tachoISR() {
    tachoCount++;
}

static void setup25kHzPWM() {
    // Timer1: Phase Correct PWM, TOP = ICR1
    // f = 16 MHz / (2 * prescaler * TOP) = 16 MHz / (2 * 1 * 320) = 25 kHz
    TCCR1A = _BV(COM1A1) | _BV(WGM11);
    TCCR1B = _BV(WGM13) | _BV(CS10);    // prescaler = 1, TOP = ICR1
    ICR1 = PWM_MAX;
    OCR1A = 0;                            // duty cycle = 0 (fan off)
    pinMode(FAN_PWM_PIN, OUTPUT);
}

static void setFanPWM(int value) {
    OCR1A = constrain(value, 0, PWM_MAX);
}

void setup() {
    Serial.begin(9600);
    setup25kHzPWM();

    sensors.begin();
    sensors.setResolution(12);
    sensors.setWaitForConversion(false);

    // Tachometer (optional — if connected)
    pinMode(FAN_TACHO_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(FAN_TACHO_PIN), tachoISR, FALLING);

    Serial.println("FAN:READY:4PIN");
}

void loop() {
    // Read commands from server
    if (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        if (cmd.startsWith("PWM:")) {
            currentPWM = constrain(cmd.substring(4).toInt(), 0, PWM_MAX);
            setFanPWM(currentPWM);
            Serial.print("FAN:PWM=");
            Serial.println(currentPWM);
        }
    }

    // Periodic temperature + RPM reading
    unsigned long now = millis();
    if (now - lastTempRead >= TEMP_INTERVAL) {
        // DS18B20 temperature
        sensors.requestTemperatures();
        float temp = sensors.getTempCByIndex(0);
        if (temp > -100) {
            Serial.print("TEMP:");
            Serial.println(temp, 2);
        }

        // RPM from tachometer (2 pulses per revolution for most fans)
        unsigned long elapsed = now - lastTachoRead;
        if (elapsed > 0) {
            noInterrupts();
            unsigned long count = tachoCount;
            tachoCount = 0;
            interrupts();
            unsigned long rpm = (count * 60000UL) / (elapsed * 2);
            if (rpm <= 4000) {
                Serial.print("RPM:");
                Serial.println(rpm);
            }
        }
        lastTachoRead = now;
        lastTempRead = now;
    }
}
