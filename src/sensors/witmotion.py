import argparse
import csv
import os
import sys
import threading
import time
from collections import deque

import numpy as np
import serial
from serial import SerialException

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'vb01_python_sdk'))
from vb01_python_sdk.device_model import DeviceModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_PORT = 'COM5' if os.name == 'nt' else '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
PORT = os.getenv('WTVB_PORT', DEFAULT_PORT)

BAUD_CANDIDATES = [
    int(part.strip())
    for part in os.getenv('WTVB_BAUD_CANDIDATES', '115200,38400,9600').split(',')
    if part.strip()
]
BAUD = BAUD_CANDIDATES[0]
MODBUS_ADDR = int(os.getenv('WTVB_MODBUS_ADDR', '0x50'), 0)

REG_AX = 0x34
REG_AY = 0x35
REG_AZ = 0x36
REG_VX = 0x3A
REG_VY = 0x3B
REG_VZ = 0x3C
REG_DX = 0x41
REG_DY = 0x42
REG_DZ = 0x43
REG_HDX = 0x47
REG_HDY = 0x48
REG_HDZ = 0x49
REG_TEMP = 0x40
REG_HZZ = 0x46
REG_RATE = 0x65
REG_UNLOCK = 0x69

FULL_START_REG = REG_AX
FULL_REG_COUNT = (REG_HDZ - REG_AX) + 1
REGISTER_LABELS = {
    REG_AX: 'acc_x',
    REG_AY: 'acc_y',
    REG_AZ: 'acc_z',
    REG_VX: 'vel_x',
    REG_VY: 'vel_y',
    REG_VZ: 'vel_z',
    REG_DX: 'disp_x',
    REG_DY: 'disp_y',
    REG_DZ: 'disp_z',
    REG_HDX: 'hi_disp_x',
    REG_HDY: 'hi_disp_y',
    REG_HDZ: 'hi_disp_z',
}

FAST_START_REG = REG_AZ
FAST_REG_COUNT = 7

SENSOR_RATE_HZ = int(float(os.getenv('WTVB_SENSOR_RATE_HZ', '150')))
SAMPLING_RATE = float(SENSOR_RATE_HZ)
MAX_TIME_PTS = int(max(600, 60 * SAMPLING_RATE))
SERIAL_TIMEOUT = float(os.getenv('WTVB_SERIAL_TIMEOUT', '0.03'))
AUTO_CONFIGURE_SENSOR_RATE = os.getenv('WTVB_SET_SENSOR_RATE', '0').lower() in {'1', 'true', 'yes', 'on'}

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'logs')
LOG_DIR = os.path.abspath(LOG_DIR)

# ---------------------------------------------------------------------------
# Shared data buffers
# ---------------------------------------------------------------------------
_vz_history = deque(maxlen=MAX_TIME_PTS)
_raw_vz_history = deque(maxlen=MAX_TIME_PTS)
_raw_az_history = deque(maxlen=MAX_TIME_PTS)
_latest_full_registers = {}
_hzz_history = deque(maxlen=MAX_TIME_PTS)
_ts_history = deque(maxlen=MAX_TIME_PTS)

_lock = threading.Lock()
_connected = False

# ---------------------------------------------------------------------------
# Logging state
# ---------------------------------------------------------------------------
_log_active = False
_log_buffer = []
_log_counter = 0
_log_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Modbus helpers
# ---------------------------------------------------------------------------
_crc_helper = DeviceModel.__new__(DeviceModel)


def _build_read_request(addr: int, reg: int, count: int) -> bytes:
    frame = [addr, 0x03, reg >> 8, reg & 0xFF, count >> 8, count & 0xFF, 0x00, 0x00]
    crc = _crc_helper.get_crc(frame, 6)
    frame[6] = (crc >> 8) & 0xFF
    frame[7] = crc & 0xFF
    return bytes(frame)


def _build_write_request(addr: int, reg: int, value: int) -> bytes:
    frame = [addr, 0x06, reg >> 8, reg & 0xFF, (value >> 8) & 0xFF, value & 0xFF, 0x00, 0x00]
    crc = _crc_helper.get_crc(frame, 6)
    frame[6] = (crc >> 8) & 0xFF
    frame[7] = crc & 0xFF
    return bytes(frame)


def decode_vz_mm_s(raw_value: int) -> float:
    return float(raw_value) / 100.0


def decode_frequency_hz(raw_value: int) -> float:
    return raw_value / 10.0


def _to_int16(raw_value: int) -> int:
    return raw_value - 0x10000 if raw_value & 0x8000 else raw_value


def _decode_registers(registers: dict) -> dict:
    decoded = {}
    for reg, value in registers.items():
        name = REGISTER_LABELS.get(reg, f'reg_0x{reg:02X}')
        signed_val = _to_int16(value)
        decoded[f'reg_{name}'] = signed_val
    return decoded


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


def _parse_read_response(buffer: bytes, start_reg: int, register_count: int) -> dict:
    frame = _extract_valid_frame(buffer, MODBUS_ADDR, 0x03, 2 * register_count)
    if not frame:
        return {}

    registers = {}
    for offset in range(register_count):
        raw_value = (frame[3 + 2 * offset] << 8) | frame[4 + 2 * offset]
        registers[start_reg + offset] = raw_value
    return registers


def _send_and_read_exact(ser: serial.Serial, request: bytes, expected_len: int) -> bytes:
    ser.reset_input_buffer()
    ser.write(request)
    ser.flush()
    return ser.read(expected_len)


def write_register(ser: serial.Serial, reg_addr: int, value: int) -> bool:
    request = _build_write_request(MODBUS_ADDR, reg_addr, value)
    response = _send_and_read_exact(ser, request, 8)
    return response == request


def set_sensor_sampling_rate(ser: serial.Serial, sample_rate_hz: int) -> bool:
    if not write_register(ser, REG_UNLOCK, 0xB588):
        return False
    time.sleep(0.05)
    if not write_register(ser, REG_RATE, int(sample_rate_hz)):
        return False
    time.sleep(0.05)
    return write_register(ser, 0x00, 0x0000)


# ---------------------------------------------------------------------------
# Background serial reader
# ---------------------------------------------------------------------------
class SerialReader(threading.Thread):
    def __init__(
        self,
        port: str = PORT,
        baud_candidates=None,
        sample_rate_hz: int = SENSOR_RATE_HZ,
        auto_configure_sensor_rate: bool = AUTO_CONFIGURE_SENSOR_RATE,
        read_full_registers: bool = False,
    ):
        super().__init__(daemon=True, name='SerialReader')
        self.port = port
        self.baud_candidates = list(baud_candidates or BAUD_CANDIDATES)
        self.sample_rate_hz = int(sample_rate_hz)
        self.auto_configure_sensor_rate = auto_configure_sensor_rate
        self.read_full_registers = read_full_registers
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._last_hzz_hz = 0.0
        self._hzz_poll_every = 50
        self._current_baud = None
        self._retry_delay = 2.0

    def _probe_device(self, ser: serial.Serial) -> bool:
        request = _build_read_request(MODBUS_ADDR, FAST_START_REG, FAST_REG_COUNT)
        response = _send_and_read_exact(ser, request, 5 + 2 * FAST_REG_COUNT)
        registers = _parse_read_response(response, FAST_START_REG, FAST_REG_COUNT)
        return REG_VZ in registers

    def _open_serial(self) -> serial.Serial:
        last_error = None
        for baud in self.baud_candidates:
            try:
                ser = serial.Serial(
                    self.port,
                    baudrate=baud,
                    timeout=SERIAL_TIMEOUT,
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
            f'Unable to communicate with sensor on {self.port} using bauds {self.baud_candidates}'
        )

    def run(self):
        global _connected, _log_active, _log_buffer, _log_counter, _latest_full_registers

        fast_request = _build_read_request(MODBUS_ADDR, FAST_START_REG, FAST_REG_COUNT)
        fast_resp_len = 5 + 2 * FAST_REG_COUNT

        hzz_request = _build_read_request(MODBUS_ADDR, REG_HZZ, 1)
        hzz_resp_len = 7

        full_request = None
        full_resp_len = 0
        if self.read_full_registers:
            full_request = _build_read_request(MODBUS_ADDR, FULL_START_REG, FULL_REG_COUNT)
            full_resp_len = 5 + 2 * FULL_REG_COUNT

        while not self._stop_event.is_set():
            try:
                with self._open_serial() as ser:
                    if self.auto_configure_sensor_rate:
                        ok = set_sensor_sampling_rate(ser, self.sample_rate_hz)
                        status = 'OK' if ok else 'FAILED'
                        print(f'[SerialReader] Set sensor rate to {self.sample_rate_hz} Hz: {status}')

                    _connected = True
                    print(f'[SerialReader] Connected -> {self.port} @ {self._current_baud} baud')

                    cycle = 0
                    while not self._stop_event.is_set():
                        self._pause_event.wait()
                        loop_started_at = time.perf_counter()

                        fast_response = _send_and_read_exact(ser, fast_request, fast_resp_len)
                        registers = _parse_read_response(fast_response, FAST_START_REG, FAST_REG_COUNT)
                        if not registers:
                            raise SerialException('Empty register read')

                        raw_vz_u16 = registers[REG_VZ]
                        vz_unsigned = decode_vz_mm_s(raw_vz_u16)

                        full_registers = {}
                        decoded_registers = {}
                        if self.read_full_registers and full_request:
                            full_response = _send_and_read_exact(ser, full_request, full_resp_len)
                            full_registers = _parse_read_response(full_response, FULL_START_REG, FULL_REG_COUNT)
                            if full_registers:
                                decoded_registers = _decode_registers(full_registers)

                        if cycle % self._hzz_poll_every == 0:
                            hzz_response = _send_and_read_exact(ser, hzz_request, hzz_resp_len)
                            hzz_registers = _parse_read_response(hzz_response, REG_HZZ, 1)
                            if hzz_registers:
                                self._last_hzz_hz = decode_frequency_hz(hzz_registers[REG_HZZ])

                        ts = time.time()
                        vz_mm_s = vz_unsigned
                        hzz_hz = self._last_hzz_hz

                        with _lock:
                            _raw_vz_history.append(raw_vz_u16)
                            _vz_history.append(vz_mm_s)
                            _hzz_history.append(hzz_hz)
                            _ts_history.append(ts)
                            if decoded_registers:
                                _latest_full_registers = decoded_registers
                            elif self.read_full_registers:
                                _latest_full_registers = {}

                        with _log_lock:
                            if _log_active:
                                _log_counter += 1
                                log_row = {
                                    'counter': _log_counter,
                                    'unix_time': ts,
                                    'iso_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts)),
                                    'raw_vz_u16': raw_vz_u16,
                                    'vz_mm_s': vz_mm_s,
                                    'hzz_hz': hzz_hz,
                                }
                                if decoded_registers:
                                    log_row.update(decoded_registers)
                                _log_buffer.append(log_row)

                        print(
                            f'vz={vz_mm_s:8.3f} mm/s  hzz={hzz_hz:6.1f} Hz',
                            end='\r',
                        )

                        cycle += 1
                        elapsed = time.perf_counter() - loop_started_at
                        sleep_time = (1.0 / self.sample_rate_hz) - elapsed
                        if sleep_time > 0:
                            time.sleep(sleep_time)

            except SerialException as exc:
                print(f'\n[SerialReader] Serial error: {exc}')
            except Exception as exc:
                print(f'\n[SerialReader] Error: {exc}')
            finally:
                _connected = False
                print('\n[SerialReader] Disconnected. Reconnecting in 2s...')
                if self._stop_event.is_set():
                    break
                time.sleep(self._retry_delay)

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
    with _lock:
        raw_az = list(_raw_az_history)
        raw_vz = list(_raw_vz_history)
        vz = list(_vz_history)
        hzz = list(_hzz_history)
        ts = list(_ts_history)
    rel = [stamp - ts[0] for stamp in ts] if ts else []
    return {'raw_vz_u16': raw_vz, 'vz': vz, 'hzz': hzz, 'ts': ts, 'rel_s': rel}


def get_latest_sample() -> dict:
    with _lock:
        if not _ts_history:
            return {}
        return {
            'vz_mm_s': _vz_history[-1],
            'hzz_hz': _hzz_history[-1],
            'unix_time': _ts_history[-1],
            'registers': dict(_latest_full_registers),
        }


def is_connected() -> bool:
    return _connected


def start_logging() -> None:
    global _log_active, _log_buffer, _log_counter
    with _log_lock:
        _log_active = True
        _log_buffer = []
        _log_counter = 0


def stop_logging() -> list:
    global _log_active
    with _log_lock:
        _log_active = False
        data = list(_log_buffer)
    return data


def save_log(rpm: int, load_w: int, data: list) -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    filename = f'vibration_RPM{rpm}_LOAD{load_w}W_{timestamp}.csv'
    filepath = os.path.join(LOG_DIR, filename)

    vz_vals = [row['vz_mm_s'] for row in data] if data else []
    timestamps = [row['unix_time'] for row in data] if data else []
    vz_rms = float(np.sqrt(np.mean(np.square(vz_vals)))) if vz_vals else 0.0
    effective_rate_hz = SAMPLING_RATE
    if len(timestamps) >= 2:
        intervals = np.diff(np.array(timestamps, dtype=float))
        median_dt = float(np.median(intervals)) if len(intervals) else 0.0
        if median_dt > 0:
            effective_rate_hz = 1.0 / median_dt

    with open(filepath, 'w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['# Engine Vibration Log'])
        writer.writerow(['# RPM', rpm])
        writer.writerow(['# Load (W)', load_w])
        writer.writerow(['# Timestamp', timestamp])
        writer.writerow(['# Samples', len(data)])
        writer.writerow(['# Sensor Rate (Hz)', SENSOR_RATE_HZ])
        writer.writerow(['# Effective Poll Rate (Hz)', f'{effective_rate_hz:.6f}'])
        writer.writerow(['# VZ RMS (mm/s)', f'{vz_rms:.6f}'])
        writer.writerow([])
        base_keys = [
            'counter',
            'unix_time',
            'iso_time',
            'vz_mm_s',
            'hzz_hz',
            'raw_vz_u16',
        ]
        extra_keys = sorted({
            key
            for row in data
            for key in row.keys()
            if key not in base_keys
        })
        header = base_keys + extra_keys
        writer.writerow(header)
        for row in data:
            row_out = []
            for key in header:
                val = row.get(key, '')
                if isinstance(val, float):
                    row_out.append(f"{val:.6f}")
                else:
                    row_out.append(val)
            writer.writerow(row_out)

    print(
        f'[Logger] Saved -> {filepath} '
        f'({len(data)} samples, VZ RMS={vz_rms:.4f} mm/s)'
    )
    return filepath
