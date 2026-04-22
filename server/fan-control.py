#!/usr/bin/env python3
"""fan-control.py — server fan control daemon via Arduino USB serial.

Reads CPU/disk temperatures, controls fan via Arduino 4-pin PWM,
monitors ambient temperature from DS18B20 sensor on Arduino,
sends Telegram alarm if cooling is ineffective.

Runs as systemd service (continuous daemon).
Writes /opt/fan-control/status.json for server-info.py integration.

Deploy: cp server/fan-control.py /opt/fan-control/
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

import serial

# === CONFIGURATION ===

SERIAL_PORT = os.environ.get('FAN_SERIAL', '/dev/arduino-fan')
BAUD = 9600

# Temperature thresholds (°C)
CPU_ON = 65             # fan ON when CPU above this
CPU_OFF = 55            # fan OFF candidate when below this
DISK_ON = 45            # fan ON when disk above this
DISK_OFF = 38           # fan OFF candidate when below this

COOLDOWN_SEC = 120      # keep fan running after temp drops (seconds)
CHECK_SEC = 10          # seconds between temperature checks

# PWM (4-pin variant: 0-320)
PWM_MAX = 320           # Timer1 TOP = 100% duty
PWM_MIN = 0             # 4-pin fans start reliably

# Disk SMART
SMARTCTL = '/usr/sbin/smartctl'
DISK_DEVICES = ['/dev/sda', '/dev/sdb']

# Status file — read by server-info.py every 5 min
STATUS_FILE = os.environ.get('FAN_STATUS', '/opt/fan-control/status.json')

# Status write intervals
STATUS_WRITE_ON = 30    # seconds when fan is running
STATUS_WRITE_OFF = 600  # seconds when fan is off (10 min)

# Alarm: temp not decreasing after N seconds of fan running
ALARM_NO_DROP_SEC = 600  # 10 minutes
ALARM_CONFIG = '/opt/alarms/config.json'

# === LOGGING ===

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger('fan-control')

# === GLOBALS (updated by serial reader thread) ===

arduino_temp = None
arduino_rpm = None


# === TEMPERATURE READING ===

def get_cpu_temp():
    """Read max CPU temperature from thermal zones."""
    temps = []
    thermal_base = '/sys/class/thermal'
    try:
        for zone in sorted(os.listdir(thermal_base)):
            if zone.startswith('thermal_zone'):
                try:
                    with open(os.path.join(thermal_base, zone, 'temp')) as f:
                        temps.append(int(f.read().strip()) / 1000.0)
                except (OSError, ValueError):
                    pass
    except OSError:
        pass
    return round(max(temps), 1) if temps else None


def get_disk_temp():
    """Read max disk temperature via smartctl."""
    max_t = None
    for dev in DISK_DEVICES:
        try:
            r = subprocess.run(
                [SMARTCTL, '-a', '-j', dev],
                capture_output=True, text=True, timeout=10,
            )
            data = json.loads(r.stdout)
            t = data.get('temperature', {}).get('current')
            if t is not None and (max_t is None or t > max_t):
                max_t = t
        except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
            pass
    return max_t


# === PWM CALCULATION ===

def calc_pwm(cpu_t, disk_t):
    """Calculate PWM value based on how far above OFF thresholds."""
    ratios = []
    if cpu_t is not None and cpu_t > CPU_OFF:
        ratios.append((cpu_t - CPU_OFF) / max(CPU_ON - CPU_OFF, 1))
    if disk_t is not None and disk_t > DISK_OFF:
        ratios.append((disk_t - DISK_OFF) / max(DISK_ON - DISK_OFF, 1))

    if not ratios:
        return 0

    ratio = min(max(max(ratios), 0.0), 1.0)
    return int(PWM_MIN + ratio * (PWM_MAX - PWM_MIN))


# === SERIAL COMMUNICATION ===

def serial_reader(ser):
    """Read data from Arduino in background thread."""
    global arduino_temp, arduino_rpm
    while True:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line:
                continue
            if line.startswith('TEMP:'):
                try:
                    arduino_temp = round(float(line[5:]), 1)
                except ValueError:
                    pass
            elif line.startswith('RPM:'):
                try:
                    arduino_rpm = int(line[4:])
                except ValueError:
                    pass
            elif line.startswith('FAN:'):
                log.debug('Arduino: %s', line)
        except (serial.SerialException, OSError):
            log.error('Serial read error, waiting 5s...')
            time.sleep(5)


def send_pwm(ser, pwm):
    """Send PWM command to Arduino."""
    try:
        ser.write(f'PWM:{pwm}\n'.encode())
        ser.flush()
    except (serial.SerialException, OSError) as e:
        log.error('Serial write error: %s', e)


# === STATUS FILE ===

def write_status(fan_on, pwm, cpu_t, disk_t, alarm_active=False,
                 alarm_detail=''):
    """Write status.json atomically for server-info.py."""
    status = {
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'ambient': arduino_temp,
        'rpm': arduino_rpm,
        'fan_on': fan_on,
        'pwm': pwm,
        'pwm_pct': round(pwm / PWM_MAX * 100) if PWM_MAX > 0 else 0,
        'cpu_temp': cpu_t,
        'disk_temp': disk_t,
    }
    if alarm_active:
        status['alarm'] = {
            'active': True,
            'title': 'CPU teplota neklesá',
            'detail': alarm_detail,
        }

    try:
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
        tmp = STATUS_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(status, f, indent=2)
        os.replace(tmp, STATUS_FILE)
    except OSError as e:
        log.error('Failed to write status: %s', e)


# === TELEGRAM ===

def send_telegram(message):
    """Send Telegram notification using alarm system config."""
    try:
        import requests  # noqa: delay import — may not be installed
    except ImportError:
        log.error('python3-requests not installed, cannot send Telegram')
        return False

    try:
        with open(ALARM_CONFIG) as f:
            config = json.load(f)
        tg = config.get('telegram', {})
        token = tg.get('token', '')
        chat_id = tg.get('chat_id', '')
        if not token or not chat_id:
            log.warning('Telegram not configured in %s', ALARM_CONFIG)
            return False
        resp = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True,
            },
            timeout=10,
        )
        if resp.ok:
            log.info('Telegram sent')
        else:
            log.error('Telegram failed: %s', resp.text)
        return resp.ok
    except Exception as e:
        log.error('Telegram error: %s', e)
        return False


# === MAIN LOOP ===

def main():
    log.info('Fan control starting, port=%s', SERIAL_PORT)

    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=2)
    time.sleep(2)  # Arduino resets on serial connect

    reader = threading.Thread(target=serial_reader, args=(ser,), daemon=True)
    reader.start()

    fan_on = False
    cooldown_start = None
    current_pwm = 0
    last_status_write = 0

    # Alarm tracking
    fan_on_since = None
    fan_on_temp = None       # CPU temp when fan turned on
    alarm_sent = False

    while True:
        cpu_t = get_cpu_temp()
        disk_t = get_disk_temp()

        # --- threshold checks ---
        above_on = False
        if cpu_t is not None and cpu_t >= CPU_ON:
            above_on = True
        if disk_t is not None and disk_t >= DISK_ON:
            above_on = True

        below_off = True
        if cpu_t is not None and cpu_t > CPU_OFF:
            below_off = False
        if disk_t is not None and disk_t > DISK_OFF:
            below_off = False

        alarm_active = False
        alarm_detail = ''

        # --- fan state machine ---
        if above_on and not fan_on:
            # === TURN ON ===
            pwm = calc_pwm(cpu_t, disk_t)
            send_pwm(ser, pwm)
            current_pwm = pwm
            fan_on = True
            cooldown_start = None
            fan_on_since = time.time()
            fan_on_temp = cpu_t
            alarm_sent = False
            log.info('FAN ON (PWM=%d/%d) — CPU:%.1f°C Disk:%s Ambient:%s RPM:%s',
                     pwm, PWM_MAX, cpu_t or 0,
                     f'{disk_t}°C' if disk_t else 'N/A',
                     f'{arduino_temp}°C' if arduino_temp else 'N/A',
                     arduino_rpm or 'N/A')

        elif fan_on and not below_off:
            # === STILL HOT — adjust PWM ===
            pwm = calc_pwm(cpu_t, disk_t)
            if abs(pwm - current_pwm) > 10:  # hysteresis
                send_pwm(ser, pwm)
                current_pwm = pwm
            cooldown_start = None

            # --- alarm: temp not decreasing after 10 min ---
            if (fan_on_since and cpu_t is not None
                    and fan_on_temp is not None):
                elapsed = time.time() - fan_on_since
                if elapsed >= ALARM_NO_DROP_SEC and cpu_t >= fan_on_temp:
                    alarm_active = True
                    alarm_detail = (
                        f'Fan běží {int(elapsed / 60)} min, '
                        f'CPU {cpu_t:.1f}°C (při zapnutí: {fan_on_temp:.1f}°C)'
                    )
                    if not alarm_sent:
                        log.warning('ALARM: %s', alarm_detail)
                        send_telegram(
                            '\U0001f525 <b>Fan control: teplota neklesá</b>\n'
                            + alarm_detail
                        )
                        alarm_sent = True
                elif cpu_t < fan_on_temp:
                    # Temp is decreasing — update baseline
                    fan_on_temp = cpu_t
                    fan_on_since = time.time()  # reset timer
                    if alarm_sent:
                        log.info('Alarm cleared — temp dropping: %.1f°C', cpu_t)
                        send_telegram(
                            f'\u2705 Fan control — teplota klesá ({cpu_t:.1f}°C)'
                        )
                        alarm_sent = False

        elif fan_on and below_off:
            # === BELOW SAFE — cooldown ===
            if cooldown_start is None:
                cooldown_start = time.time()
                log.info('Cooldown started — CPU:%.1f°C Disk:%s',
                         cpu_t or 0, f'{disk_t}°C' if disk_t else 'N/A')
            elif time.time() - cooldown_start >= COOLDOWN_SEC:
                # === TURN OFF ===
                send_pwm(ser, 0)
                current_pwm = 0
                fan_on = False
                cooldown_start = None
                fan_on_since = None
                fan_on_temp = None
                if alarm_sent:
                    send_telegram('\u2705 Fan control — teplota v normálu')
                    alarm_sent = False
                log.info('FAN OFF — CPU:%.1f°C Disk:%s Ambient:%s',
                         cpu_t or 0,
                         f'{disk_t}°C' if disk_t else 'N/A',
                         f'{arduino_temp}°C' if arduino_temp else 'N/A')

        # --- write status.json at appropriate interval ---
        now = time.time()
        interval = STATUS_WRITE_ON if fan_on else STATUS_WRITE_OFF
        if now - last_status_write >= interval:
            write_status(fan_on, current_pwm, cpu_t, disk_t,
                         alarm_active, alarm_detail)
            last_status_write = now

        time.sleep(CHECK_SEC)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log.info('Shutting down')
        sys.exit(0)
