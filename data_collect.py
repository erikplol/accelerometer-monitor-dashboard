"""Dual-sensor data collection for the vibration dashboard.

Data responsibilities:
- Pixhawk (MAVLink): provides calibrated AZ acceleration in m/s² for FFT.
- Witmotion (Modbus): provides VZ velocity in mm/s and HZZ in Hz.

Logging rows intentionally pair Pixhawk AZ with the latest Witmotion VZ/HZZ.
"""

import argparse
import csv
import json
import os
import sys
import threading
import time
from collections import deque

import numpy as np
import serial
from serial import SerialException

try:
    from gpiozero import LED
    _GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    LED = None
    _GPIO_AVAILABLE = False

from pymavlink import mavutil

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vb01_python_sdk'))
from vb01_python_sdk.device_model import DeviceModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_PORT = 'COM5' if os.name == 'nt' else '/dev/serial/by-id/usb-Auterion_PX4_FMU_v6X.x_0-if00'
PORT = os.getenv('MAVLINK_PORT', DEFAULT_PORT)
BAUD = int(os.getenv('MAVLINK_BAUD', '1000000'))

TARGET_IMU_RATE_HZ = int(os.getenv('MAVLINK_IMU_RATE_HZ', '200'))
SAMPLING_RATE = float(TARGET_IMU_RATE_HZ)

DEFAULT_WITMOTION_PORT = 'COM6' if os.name == 'nt' else '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
WITMOTION_PORT = os.getenv('WTVB_PORT', DEFAULT_WITMOTION_PORT)
WITMOTION_BAUD_CANDIDATES = [
    int(part.strip())
    for part in os.getenv('WTVB_BAUD_CANDIDATES', '115200,38400,9600').split(',')
    if part.strip()
]
WITMOTION_MODBUS_ADDR = int(os.getenv('WTVB_MODBUS_ADDR', '0x50'), 0)
WITMOTION_SENSOR_RATE_HZ = int(float(os.getenv('WTVB_SENSOR_RATE_HZ', '100')))
WITMOTION_SERIAL_TIMEOUT = float(os.getenv('WTVB_SERIAL_TIMEOUT', '0.15'))

REG_AZ = 0x36
REG_VZ = 0x3C
REG_HZZ = 0x46
WITMOTION_FAST_START_REG = REG_AZ
WITMOTION_FAST_REG_COUNT = 7

MAX_TIME_PTS = int(max(600, 60 * SAMPLING_RATE))

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
CALIB_FILE = os.getenv('MAVLINK_CALIB_FILE', os.path.join(LOG_DIR, 'gravity_calib.json'))

# Thresholds for VZ RMS (mm/s) - ISO 10816 based
THRESH_GREEN  = 2.8    # below  → green  (good)
THRESH_YELLOW = 7.1    # below  → yellow (acceptable), above → red (alarm)

# ---------------------------------------------------------------------------
# GPIO Traffic Lights
# ---------------------------------------------------------------------------
if _GPIO_AVAILABLE:
    led_red = LED(17, active_high=False)
    led_yellow = LED(27, active_high=False)
    led_green = LED(22, active_high=False)
else:
    led_red = led_yellow = led_green = None

def _set_gpio_lights(red: bool, yellow: bool, green: bool) -> None:
    if not _GPIO_AVAILABLE:
        return
    if red:
        led_red.on()
    else:
        led_red.off()
    if yellow:
        led_yellow.on()
    else:
        led_yellow.off()
    if green:
        led_green.on()
    else:
        led_green.off()

# ArduPilot RAW_IMU sends mG (milli-G) for xacc/yacc/zacc
MG_TO_MS2 = 9.80665 / 1000.0

# ---------------------------------------------------------------------------
# Shared data buffers
# ---------------------------------------------------------------------------
_az_ms2_history = deque(maxlen=MAX_TIME_PTS)  # Z acceleration in m/s² (for FFT)
_wit_vz_mms_history = deque(maxlen=MAX_TIME_PTS)  # Witmotion VZ in mm/s
_wit_hzz_history = deque(maxlen=MAX_TIME_PTS)     # Witmotion HZZ in Hz
_ts_history = deque(maxlen=MAX_TIME_PTS)

_lock = threading.Lock()
_connected = False
_actual_rate_hz = 0.0
_wit_connected = False
_wit_latest_vz_mms = 0.0
_wit_latest_hzz_hz = 0.0

# ---------------------------------------------------------------------------
# Logging state
# ---------------------------------------------------------------------------
_log_active = False
_log_buffer = []
_log_counter = 0
_log_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Witmotion Modbus helpers
# ---------------------------------------------------------------------------
_crc_helper = DeviceModel.__new__(DeviceModel)


def _build_read_request(addr: int, reg: int, count: int) -> bytes:
    frame = [addr, 0x03, reg >> 8, reg & 0xFF, count >> 8, count & 0xFF, 0x00, 0x00]
    crc = _crc_helper.get_crc(frame, 6)
    frame[6] = (crc >> 8) & 0xFF
    frame[7] = crc & 0xFF
    return bytes(frame)


def _extract_valid_frame(buffer: bytes, addr: int, function_code: int, byte_count: int) -> bytes:
    expected_len = byte_count + 5
    for index in range(0, max(0, len(buffer) - expected_len + 1)):
        if buffer[index] != addr:
            continue
        if buffer[index + 1] != function_code:
            continue
        if buffer[index + 2] != byte_count:
            continue
        candidate = buffer[index:index + expected_len]
        crc_calc = _crc_helper.get_crc(list(candidate), len(candidate) - 2)
        crc_recv = (candidate[-2] << 8) | candidate[-1]
        if crc_calc == crc_recv:
            return candidate
    return b''


def _parse_read_response(buffer: bytes, addr: int, start_reg: int, register_count: int) -> dict:
    frame = _extract_valid_frame(buffer, addr, 0x03, 2 * register_count)
    if not frame:
        return {}

    registers = {}
    for offset in range(register_count):
        raw_value = (frame[3 + 2 * offset] << 8) | frame[4 + 2 * offset]
        registers[start_reg + offset] = raw_value
    return registers


def _decode_signed_u16(raw_value: int) -> int:
    return raw_value - 65536 if raw_value >= 32768 else raw_value


def _decode_vz_mm_s(raw_value: int) -> float:
    # Register is centi-mm/s as a signed 16-bit value.
    return float(_decode_signed_u16(raw_value)) / 100.0


def _decode_hz(raw_value: int) -> float:
    # Register is deci-Hz.
    return float(_decode_signed_u16(raw_value)) / 10.0


class WitmotionReader(threading.Thread):
    """Polls Witmotion over Modbus and updates VZ/HZZ shared state."""

    def __init__(
        self,
        port: str = WITMOTION_PORT,
        baud_candidates=None,
        modbus_addr: int = WITMOTION_MODBUS_ADDR,
        sample_rate_hz: int = WITMOTION_SENSOR_RATE_HZ,
    ):
        super().__init__(daemon=True, name='WitmotionReader')
        self.port = port
        self.baud_candidates = list(baud_candidates or WITMOTION_BAUD_CANDIDATES)
        self.modbus_addr = int(modbus_addr)
        self.sample_rate_hz = int(sample_rate_hz)
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._current_baud = None

    def _send_and_read_exact(self, ser: serial.Serial, request: bytes, expected_len: int) -> bytes:
        ser.reset_input_buffer()
        ser.write(request)
        ser.flush()
        return ser.read(expected_len)

    def _probe_device(self, ser: serial.Serial) -> bool:
        request = _build_read_request(self.modbus_addr, WITMOTION_FAST_START_REG, WITMOTION_FAST_REG_COUNT)
        response = self._send_and_read_exact(ser, request, 5 + 2 * WITMOTION_FAST_REG_COUNT)
        registers = _parse_read_response(response, self.modbus_addr, WITMOTION_FAST_START_REG, WITMOTION_FAST_REG_COUNT)
        return REG_VZ in registers

    def _open_serial(self) -> serial.Serial:
        last_error = None
        for baud in self.baud_candidates:
            try:
                ser = serial.Serial(
                    self.port,
                    baudrate=baud,
                    timeout=WITMOTION_SERIAL_TIMEOUT,
                    bytesize=8,
                    parity='N',
                    stopbits=1,
                )
                if self._probe_device(ser):
                    self._current_baud = baud
                    return ser
                ser.close()
            except SerialException as exc:
                last_error = exc
        raise last_error or SerialException(
            f'Unable to communicate with Witmotion sensor on {self.port} '
            f'using bauds {self.baud_candidates}'
        )

    def run(self):
        global _wit_connected, _wit_latest_vz_mms, _wit_latest_hzz_hz

        fast_request = _build_read_request(self.modbus_addr, WITMOTION_FAST_START_REG, WITMOTION_FAST_REG_COUNT)
        fast_resp_len = 5 + 2 * WITMOTION_FAST_REG_COUNT
        hzz_request = _build_read_request(self.modbus_addr, REG_HZZ, 1)
        hzz_resp_len = 7

        while not self._stop_event.is_set():
            try:
                with self._open_serial() as ser:
                    _wit_connected = True
                    print(f'[Witmotion] Connected -> {self.port} @ {self._current_baud} baud')
                    cycle = 0

                    while not self._stop_event.is_set():
                        self._pause_event.wait()
                        loop_started_at = time.perf_counter()

                        fast_response = self._send_and_read_exact(ser, fast_request, fast_resp_len)
                        registers = _parse_read_response(
                            fast_response,
                            self.modbus_addr,
                            WITMOTION_FAST_START_REG,
                            WITMOTION_FAST_REG_COUNT,
                        )
                        if not registers:
                            continue

                        if cycle % 10 == 0:
                            hzz_response = self._send_and_read_exact(ser, hzz_request, hzz_resp_len)
                            hzz_registers = _parse_read_response(hzz_response, self.modbus_addr, REG_HZZ, 1)
                            if hzz_registers:
                                _wit_latest_hzz_hz = _decode_hz(hzz_registers[REG_HZZ])

                        ts = time.time()
                        vz_mms = _decode_vz_mm_s(registers[REG_VZ])

                        with _lock:
                            _wit_latest_vz_mms = vz_mms
                            _wit_vz_mms_history.append(vz_mms)
                            _wit_hzz_history.append(_wit_latest_hzz_hz)

                        cycle += 1
                        elapsed = time.perf_counter() - loop_started_at
                        sleep_time = (1.0 / self.sample_rate_hz) - elapsed
                        if sleep_time > 0:
                            time.sleep(sleep_time)

            except SerialException as exc:
                _wit_connected = False
                print(f'\n[Witmotion] Serial error: {exc}')
                if self._stop_event.wait(1.0):
                    break
            except Exception as exc:
                _wit_connected = False
                print(f'\n[Witmotion] Error: {exc}')
                if self._stop_event.wait(1.0):
                    break

        _wit_connected = False
        print('\n[Witmotion] Reader stopped.')

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()


# ---------------------------------------------------------------------------
# MAVLink Reader Thread
# ---------------------------------------------------------------------------
class MAVLinkReader(threading.Thread):
    """
    Reads IMU data from ArduPilot via MAVLink.

    - RAW_IMU/HIGHRES_IMU: Z-axis acceleration used for FFT/logging.
    - Does not compute velocity; VZ is sourced from Witmotion.
    """

    def __init__(
        self,
        port: str = PORT,
        baud: int = BAUD,
        target_rate_hz: int = TARGET_IMU_RATE_HZ,
    ):
        super().__init__(daemon=True, name='MAVLinkReader')
        self.port = port
        self.baud = baud
        self.target_rate_hz = target_rate_hz
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()

        # Calibration
        # Default offset (milli-G). We will try these in order:
        # 1) env MAVLINK_FIXED_GRAVITY_OFFSET -> use and mark calibrated
        # 2) calibration file (CALIB_FILE) -> load and mark calibrated
        # 3) otherwise perform runtime calibration once (collect samples)
        self._gravity_offset = -980.0
        self._calibrated = True
        self._calibration_samples = []

        # Env override (explicit fixed offset)
        fixed_offset = os.getenv('MAVLINK_FIXED_GRAVITY_OFFSET')
        if fixed_offset is not None:
            try:
                self._gravity_offset = float(fixed_offset)
                self._calibrated = True
            except Exception:
                self._calibrated = False
                self._calibration_samples = []
        else:
            # Try loading calibration file
            try:
                if os.path.exists(CALIB_FILE):
                    with open(CALIB_FILE, 'r') as fh:
                        data = json.load(fh)
                    val = data.get('gravity_offset_mg')
                    if val is not None:
                        self._gravity_offset = float(val)
                        self._calibrated = True
                    else:
                        self._calibrated = False
                        self._calibration_samples = []
                else:
                    # No file -> perform runtime calibration
                    self._calibrated = False
                    self._calibration_samples = []
            except Exception as exc:
                print(f'[MAVLink] Warning: could not load calibration: {exc}')
                self._calibrated = False
                self._calibration_samples = []

        # Rate measurement
        self._msg_count = 0
        self._rate_window_start = None
        self._rate_window_count = 0


    def _connect(self) -> mavutil.mavlink_connection:
        print(f'[MAVLink] Connecting to {self.port} @ {self.baud} baud...')

        connection = mavutil.mavlink_connection(
            self.port,
            baud=self.baud,
            autoreconnect=True,
        )

        print('[MAVLink] Waiting for heartbeat...')
        connection.wait_heartbeat(timeout=10)
        print(f'[MAVLink] Heartbeat received (system {connection.target_system}, '
              f'component {connection.target_component})')

        return connection

    def _configure_stream_rate(self, connection: mavutil.mavlink_connection):
        """Configure stream rate for RAW_IMU messages."""

        # Request RAW_SENSORS stream (includes RAW_IMU)
        connection.mav.request_data_stream_send(
            connection.target_system,
            connection.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_RAW_SENSORS,
            self.target_rate_hz,
            1,
        )

        # Also try message interval for RAW_IMU
        interval_us = int(1_000_000 / self.target_rate_hz)
        connection.mav.command_long_send(
            connection.target_system,
            connection.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0,
            mavutil.mavlink.MAVLINK_MSG_ID_RAW_IMU,
            interval_us,
            0, 0, 0, 0, 0,
        )

        print(f'[MAVLink] Requested RAW_IMU at {self.target_rate_hz} Hz')

    def _calibrate_gravity(self, zacc_mg: float):
        if self._calibrated:
            return

        self._calibration_samples.append(zacc_mg)
        if len(self._calibration_samples) < 50:
            return

        self._gravity_offset = float(np.median(self._calibration_samples))
        self._calibrated = True
        print(f'[MAVLink] Gravity calibrated: offset = {self._gravity_offset:.2f} mG')
        # Save calibration to file so future runs reuse it.
        try:
            os.makedirs(os.path.dirname(CALIB_FILE), exist_ok=True)
            with open(CALIB_FILE, 'w') as fh:
                json.dump({'gravity_offset_mg': self._gravity_offset, 'timestamp': time.time()}, fh)
            print(f'[MAVLink] Saved gravity calibration -> {CALIB_FILE}')
        except Exception as exc:
            print(f'[MAVLink] Warning: could not save calibration: {exc}')

    def _update_rate_measurement(self, ts: float):
        global _actual_rate_hz

        if self._rate_window_start is None:
            self._rate_window_start = ts
            self._rate_window_count = 0

        self._rate_window_count += 1
        elapsed = ts - self._rate_window_start

        if elapsed >= 1.0:
            _actual_rate_hz = self._rate_window_count / elapsed
            self._rate_window_start = ts
            self._rate_window_count = 0

    def _reset_state(self):
        """Reset calibration and rate state for reconnection."""
        # Preserve any loaded/fixed calibration across reconnects.
        # If calibration hasn't completed, reset sample buffer so we can recalibrate.
        if not self._calibrated:
            self._calibration_samples = []
        self._rate_window_start = None
        self._rate_window_count = 0

    def run(self):
        global _connected, _actual_rate_hz

        RECONNECT_DELAY = 3  # seconds between reconnection attempts

        while not self._stop_event.is_set():
            connection = None
            try:
                connection = self._connect()
                self._configure_stream_rate(connection)
                _connected = True

                print(f'[MAVLink] Starting data collection')
                if not self._calibrated:
                    print('[MAVLink] Calibrating gravity offset (keep device still)...')
                else:
                    print(f'[MAVLink] Using fixed gravity offset: {self._gravity_offset:+.1f} mG')

                while not self._stop_event.is_set():
                    self._pause_event.wait()

                    # Accept multiple IMU message types (RAW_IMU in mG or HIGHRES_IMU in m/s²)
                    msg = connection.recv_match(blocking=True, timeout=0.5)

                    if msg is None:
                        continue

                    ts = time.time()
                    self._msg_count += 1
                    self._update_rate_measurement(ts)

                    msg_type = msg.get_type()

                    if msg_type == 'RAW_IMU':
                        # RAW_IMU: zacc is in mG
                        raw_zacc_mg = msg.zacc

                        # Calibrate using mG units (existing calibration expects mG)
                        self._calibrate_gravity(raw_zacc_mg)

                        if self._calibrated:
                            zacc_corrected_mg = raw_zacc_mg - self._gravity_offset
                        else:
                            zacc_corrected_mg = raw_zacc_mg - 1000

                        az_ms2 = zacc_corrected_mg * MG_TO_MS2

                    elif msg_type == 'HIGHRES_IMU':
                        # HIGHRES_IMU: zacc is in m/s² (SI units)
                        raw_zacc_ms2 = msg.zacc

                        # Convert to mG for calibration logic
                        raw_zacc_mg = raw_zacc_ms2 / MG_TO_MS2
                        self._calibrate_gravity(raw_zacc_mg)

                        if self._calibrated:
                            # gravity offset stored in mG -> convert to m/s²
                            az_ms2 = raw_zacc_ms2 - (self._gravity_offset * MG_TO_MS2)
                        else:
                            # assume ~1g offset until calibrated
                            az_ms2 = raw_zacc_ms2 - 9.80665

                    else:
                        # Not an IMU message we care about
                        continue

                    with _lock:
                        _az_ms2_history.append(az_ms2)
                        _ts_history.append(ts)
                        wit_vz_mms = _wit_latest_vz_mms
                        wit_hzz_hz = _wit_latest_hzz_hz
                        
                        # Evaluate GPIO traffic lights every 25 samples (~0.25s at 100Hz)
                        if self._msg_count % 25 == 0:
                            rms = _get_vz_rms_last_1s()
                            if rms > 0:
                                is_red = rms >= THRESH_YELLOW
                                is_yellow = THRESH_GREEN <= rms < THRESH_YELLOW
                                is_green = rms < THRESH_GREEN
                            else:
                                is_red, is_yellow, is_green = False, False, False
                            
                            _set_gpio_lights(is_red, is_yellow, is_green)

                    with _log_lock:
                        if _log_active:
                            global _log_counter, _log_buffer
                            _log_counter += 1
                            _log_buffer.append({
                                'counter': _log_counter,
                                'unix_time': ts,
                                'iso_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts)),
                                'az_ms2': az_ms2,
                                'vz_mms': wit_vz_mms,
                                'hzz_hz': wit_hzz_hz,
                            })

                    if self._msg_count % 25 == 0:
                        print(
                            f'az={az_ms2:+8.4f} m/s²  rate={_actual_rate_hz:.1f} Hz',
                            end='\r',
                        )

            except Exception as exc:
                print(f'\n[MAVLink] Error: {exc}')
                import traceback
                traceback.print_exc()

            finally:
                _connected = False
                _actual_rate_hz = 0.0
                if connection:
                    try:
                        connection.close()
                    except Exception:
                        pass

            # Retry connection unless stopped
            if not self._stop_event.is_set():
                self._reset_state()
                print(f'[MAVLink] Reconnecting in {RECONNECT_DELAY}s...')
                # Use wait with timeout so we can respond to stop_event
                if self._stop_event.wait(RECONNECT_DELAY):
                    break  # Stop event was set

        print('\n[MAVLink] Reader stopped.')

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_histories() -> dict:
    """Get all history buffers for plotting/FFT."""
    with _lock:
        az_ms2 = list(_az_ms2_history)
        vz_mms = list(_wit_vz_mms_history)
        hzz_hz = list(_wit_hzz_history)
        ts = list(_ts_history)
    rel = [stamp - ts[0] for stamp in ts] if ts else []
    return {
        'az_ms2': az_ms2,    # Z acceleration in m/s² (for FFT)
        'vz_mms': vz_mms,    # Witmotion Z velocity in mm/s (for display/RMS)
        'hzz_hz': hzz_hz,
        'ts': ts,
        'rel_s': rel,
    }


def get_latest_sample() -> dict:
    """Get the most recent sample."""
    with _lock:
        if not _ts_history:
            return {}
        return {
            'az_ms2': _az_ms2_history[-1],
            'vz_mms': _wit_latest_vz_mms,
            'hzz_hz': _wit_latest_hzz_hz,
            'unix_time': _ts_history[-1],
            'rate_hz': _actual_rate_hz,
        }


def get_actual_rate() -> float:
    return _actual_rate_hz


def is_connected() -> bool:
    return _connected


def is_witmotion_connected() -> bool:
    return _wit_connected


def _get_vz_rms_last_1s() -> float:
    """Return VZ RMS over the latest ~1 second of Witmotion data."""
    n_1s = max(1, int(WITMOTION_SENSOR_RATE_HZ))
    if len(_wit_vz_mms_history) < n_1s:
        return 0.0
    recent_vz = list(_wit_vz_mms_history)[-n_1s:]
    return float(np.sqrt(np.mean(np.array(recent_vz) ** 2)))


def start_logging() -> None:
    """Start capturing combined AZ/VZ samples into an in-memory log buffer."""
    global _log_active, _log_buffer, _log_counter
    with _log_lock:
        _log_active = True
        _log_buffer = []
        _log_counter = 0


def stop_logging() -> list:
    """Stop logging and return a snapshot of buffered rows."""
    global _log_active
    with _log_lock:
        _log_active = False
        data = list(_log_buffer)
    return data


def save_log(rpm: int, load_w: int, data: list) -> str:
    """Save combined Pixhawk/Witmotion rows to a CSV file."""
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    filename = f'vibration_RPM{rpm}_LOAD{load_w}W_{timestamp}.csv'
    filepath = os.path.join(LOG_DIR, filename)

    az_vals = [row['az_ms2'] for row in data] if data else []
    vz_vals = [row['vz_mms'] for row in data] if data else []
    timestamps = [row['unix_time'] for row in data] if data else []
    az_rms = float(np.sqrt(np.mean(np.square(az_vals)))) if az_vals else 0.0
    vz_rms = float(np.sqrt(np.mean(np.square(vz_vals)))) if vz_vals else 0.0

    effective_rate_hz = SAMPLING_RATE
    if len(timestamps) >= 2:
        intervals = np.diff(np.array(timestamps, dtype=float))
        median_dt = float(np.median(intervals)) if len(intervals) else 0.0
        if median_dt > 0:
            effective_rate_hz = 1.0 / median_dt

    with open(filepath, 'w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['# Engine Vibration Log (Pixhawk AZ + Witmotion VZ)'])
        writer.writerow(['# RPM', rpm])
        writer.writerow(['# Load (W)', load_w])
        writer.writerow(['# Timestamp', timestamp])
        writer.writerow(['# Samples', len(data)])
        writer.writerow(['# Target Rate (Hz)', TARGET_IMU_RATE_HZ])
        writer.writerow(['# Effective Rate (Hz)', f'{effective_rate_hz:.6f}'])
        writer.writerow(['# AZ RMS (m/s²)', f'{az_rms:.6f}'])
        writer.writerow(['# VZ RMS (mm/s)', f'{vz_rms:.6f}'])
        writer.writerow([])
        writer.writerow([
            'counter',
            'unix_time',
            'iso_time',
            'az_ms2',
            'vz_mms',
            'hzz_hz',
        ])
        for row in data:
            writer.writerow([
                row['counter'],
                f"{row['unix_time']:.6f}",
                row['iso_time'],
                f"{row['az_ms2']:.6f}",
                f"{row['vz_mms']:.6f}",
                f"{row.get('hzz_hz', 0.0):.6f}",
            ])

    print(
        f'[Logger] Saved -> {filepath} '
        f'({len(data)} samples, AZ RMS={az_rms:.4f} m/s², VZ RMS={vz_rms:.4f} mm/s, '
        f'rate={effective_rate_hz:.1f} Hz)'
    )
    return filepath


# ---------------------------------------------------------------------------
# Stand-alone mode
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='Read IMU data via MAVLink for vibration FFT analysis.'
    )
    parser.add_argument('--port', default=PORT, help='Serial port')
    parser.add_argument('--baud', type=int, default=BAUD, help='Baudrate')
    parser.add_argument('--rate', type=int, default=TARGET_IMU_RATE_HZ, help='Target IMU rate Hz')
    parser.add_argument('--duration', type=float, default=0.0, help='Run duration (0=forever)')
    parser.add_argument('--rpm', type=int, default=0, help='RPM metadata')
    parser.add_argument('--load', type=int, default=0, help='Load in watts')
    parser.add_argument('--log', action='store_true', help='Save CSV log')
    args = parser.parse_args()

    mav_reader = MAVLinkReader(
        port=args.port,
        baud=args.baud,
        target_rate_hz=args.rate,
    )
    wit_reader = WitmotionReader()

    if args.log:
        start_logging()

    mav_reader.start()
    wit_reader.start()
    started_at = time.time()
    print('Collecting data (Pixhawk AZ + Witmotion VZ). Press Ctrl+C to stop.')

    try:
        while True:
            if args.duration > 0 and (time.time() - started_at) >= args.duration:
                break
            latest = get_latest_sample()
            if latest:
                print(
                    f"latest -> az={latest['az_ms2']:+.4f} m/s², "
                    f"vz={latest['vz_mms']:+.2f} mm/s, "
                    f"rate={latest.get('rate_hz', 0):.1f} Hz"
                )
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        mav_reader.stop()
        wit_reader.stop()
        mav_reader.join(timeout=2.0)
        wit_reader.join(timeout=2.0)

    if args.log:
        data = stop_logging()
        save_log(args.rpm, args.load, data)


if __name__ == '__main__':
    main()
