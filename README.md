# kfs-fan-control

Arduino firmware for k-server fan control — temperature-driven fan management via USB serial.

## Variants

### kfs_fan_mosfet (Variant A)

MOSFET switches power to a standard 2/3-pin 12V fan. Uses `analogWrite()` (~490 Hz PWM).

**Wiring:**
```
12V supply ─── Fan(+)    Fan(-) ─── MOSFET drain
                                     MOSFET gate ─── Arduino D9 (PWM)
                                     MOSFET source ── GND (common)
1N4007 flyback diode across fan terminals
DS18B20 DQ ─── Arduino D4 (4.7k pull-up to 5V)
```

### kfs_fan_4pin (Variant B) — recommended

Direct 25 kHz PWM signal for 4-pin fans (Intel spec). No MOSFET needed.
Recommended fan: **Arctic P12 Pro PST** (0 RPM passive mode below 5% PWM).

**Wiring:**
```
12V supply ─── Fan +12V (yellow)
GND (common) ── Fan GND (black) + Arduino GND
Arduino D9 ─── Fan PWM (blue)
Arduino D2 ─── Fan Tacho (green, optional)
DS18B20 DQ ─── Arduino D4 (4.7k pull-up to 5V)
```

## Serial protocol (9600 baud)

```
Server -> Arduino:  PWM:xxx\n       (0-255 for mosfet, 0-320 for 4pin)
Arduino -> Server:  TEMP:xx.xx\n    (DS18B20 ambient, every 5s)
Arduino -> Server:  RPM:xxxx\n      (tachometer, 4pin only, every 5s)
Arduino -> Server:  FAN:READY\n     (startup, mosfet)
Arduino -> Server:  FAN:READY:4PIN\n (startup, 4pin)
Arduino -> Server:  FAN:PWM=xxx\n   (acknowledgment)
```

## Server-side daemon

Python daemon (`fan-control.py`) on k-server reads CPU/disk temps, sends PWM commands via USB serial. See [fan-control.md](https://github.com/k0fis/kfs-fan-control/wiki) for full documentation.

## Build

### Arduino IDE
1. Install libraries: **OneWire**, **DallasTemperature**
2. Open sketch folder, select board (Arduino Nano / Uno), upload

### Arduino CLI
```bash
arduino-cli core install arduino:avr
arduino-cli lib install "OneWire" "DallasTemperature"

# Variant A (MOSFET):
arduino-cli compile --fqbn arduino:avr:nano kfs_fan_mosfet/
arduino-cli upload --fqbn arduino:avr:nano -p /dev/ttyUSB0 kfs_fan_mosfet/

# Variant B (4-pin PWM):
arduino-cli compile --fqbn arduino:avr:nano kfs_fan_4pin/
arduino-cli upload --fqbn arduino:avr:nano -p /dev/ttyUSB0 kfs_fan_4pin/
```

## License

MIT
