/*
 * kfs_fan_mosfet — fan control via MOSFET + DS18B20 ambient temperature
 *
 * Variant A: MOSFET switches power to a 2/3-pin 12V fan.
 * Standard analogWrite() PWM (~490 Hz).
 *
 * Serial protocol (9600 baud):
 *   Server -> Arduino: "PWM:xxx\n"   where xxx = 0-255
 *   Arduino -> Server: "TEMP:xx.xx\n" every 5 seconds
 *   Arduino -> Server: "FAN:READY\n"  on startup
 *   Arduino -> Server: "FAN:PWM=xxx\n" on PWM change acknowledgment
 *
 * Wiring:
 *   MOSFET gate     -> D9 (PWM)
 *   MOSFET drain    -> fan (-), MOSFET source -> GND
 *   Fan (+)         -> +12V
 *   1N4007 flyback  -> cathode on +12V, anode on drain
 *   DS18B20 DQ      -> D4 (4.7k pull-up to 5V)
 *   DS18B20 VCC     -> 5V, GND -> GND
 *   Common GND: Arduino + 12V supply + MOSFET source
 */

#include <OneWire.h>
#include <DallasTemperature.h>

static const int FAN_PIN = 9;
static const int TEMP_PIN = 4;
static const unsigned long TEMP_INTERVAL = 5000;  // ms

OneWire oneWire(TEMP_PIN);
DallasTemperature sensors(&oneWire);

static unsigned long lastTempRead = 0;
static int currentPWM = 0;

void setup() {
    Serial.begin(9600);
    pinMode(FAN_PIN, OUTPUT);
    analogWrite(FAN_PIN, 0);
    sensors.begin();
    sensors.setResolution(12);
    sensors.setWaitForConversion(false);
    Serial.println("FAN:READY");
}

void loop() {
    // Read commands from server
    if (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        if (cmd.startsWith("PWM:")) {
            currentPWM = constrain(cmd.substring(4).toInt(), 0, 255);
            analogWrite(FAN_PIN, currentPWM);
            Serial.print("FAN:PWM=");
            Serial.println(currentPWM);
        }
    }

    // Periodic temperature reading
    unsigned long now = millis();
    if (now - lastTempRead >= TEMP_INTERVAL) {
        lastTempRead = now;
        sensors.requestTemperatures();
        float temp = sensors.getTempCByIndex(0);
        if (temp > -100) {
            Serial.print("TEMP:");
            Serial.println(temp, 2);
        }
    }
}
